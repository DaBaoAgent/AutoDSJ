"""Anchored drama narration pipeline.

The pipeline keeps source subtitle ranges attached to every narration block,
synthesises one stable audio file per full block, splits that audio at natural
pauses for visual matching, and allocates non-overlapping source intervals globally.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from pathlib import Path
import wave

from backend.concurrency import get_concurrency
from backend.ad_filter import detect_ad_intervals
from backend.media_tools import ffmpeg, ffprobe
from backend.net_retry import retry_call
from backend.scene_map import scene_map_digest, validate_scene_map
from backend.vision_api import parse_srt
from backend.qwen_voice import (
    DEFAULT_QWEN_CLONE_MODEL,
    DEFAULT_QWEN_REFERENCE_AUDIO,
    DEFAULT_QWEN_REFERENCE_TEXT_PATH,
    ensure_qwen_clone_voice,
    is_qwen_realtime_model,
    read_reference_text,
    synthesize_qwen_http_to_file,
)
from backend.visual_matcher import (
    VisualIntervalAllocator, load_visual_frames, split_visual_clauses, dominant_character_group,
)

ROOT = Path(__file__).resolve().parent
def force_ipv4() -> None:
    """DashScope WebSocket is unreliable when Windows selects an unreachable IPv6 route."""
    original = socket.getaddrinfo

    def getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
        return original(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_v4
    original_connect = socket.create_connection

    def connect_v4(address, timeout=None, source_address=None, **kwargs):
        host, port = address[:2]
        error = None
        for family, kind, proto, _, sockaddr in original(host, port, socket.AF_INET, socket.SOCK_STREAM):
            sock = None
            try:
                sock = socket.socket(family, kind, proto)
                if timeout is not None:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                error = exc
                if sock:
                    sock.close()
        raise error or OSError(f"IPv4 connection failed: {host}:{port}")

    socket.create_connection = connect_v4
    os.environ.setdefault("PREFER_IPV4", "1")


def run(cmd: list[str], *, timeout: int | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    if cmd:
        if cmd[0] == "ffmpeg":
            cmd = [ffmpeg(), *cmd[1:]]
        elif cmd[0] == "ffprobe":
            cmd = [ffprobe(), *cmd[1:]]
    return subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace",
                          capture_output=capture, timeout=timeout)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text("utf-8-sig").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


CN_DIGITS = "零一二三四五六七八九"


def _digitwise(value: str) -> str:
    return "".join(CN_DIGITS[int(ch)] for ch in value if ch.isdigit())


def _section_to_cn(number: int) -> str:
    units = ["", "十", "百", "千"]
    parts: list[str] = []
    zero_pending = False
    for position in range(3, -1, -1):
        divisor = 10 ** position
        digit = number // divisor
        number %= divisor
        if digit:
            if zero_pending and parts:
                parts.append("零")
            parts.append(CN_DIGITS[digit] + units[position])
            zero_pending = False
        elif parts:
            zero_pending = True
    result = "".join(parts)
    return result[1:] if result.startswith("一十") else result


def _int_to_cn(number: int) -> str:
    if number == 0:
        return "零"
    if number < 0:
        return "负" + _int_to_cn(-number)
    groups = []
    while number:
        groups.append(number % 10000)
        number //= 10000
    large_units = ["", "万", "亿", "兆"]
    result = ""
    zero_between = False
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if not group:
            if result:
                zero_between = True
            continue
        if result and (zero_between or group < 1000):
            result += "零"
        result += _section_to_cn(group) + large_units[index]
        zero_between = False
    return result


def _number_to_cn(value: str) -> str:
    value = value.strip()
    if value.startswith("+"):
        value = value[1:]
    if value.startswith("-"):
        return "负" + _number_to_cn(value[1:])
    if "." in value:
        integer, decimal = value.split(".", 1)
        return _int_to_cn(int(integer or 0)) + "点" + "".join(CN_DIGITS[int(ch)] for ch in decimal if ch.isdigit())
    return _int_to_cn(int(value or 0))


def _clock_tail_to_cn(value: str) -> str:
    number = int(value)
    if number == 0:
        return "零"
    if number < 10 and len(value) > 1:
        return "零" + CN_DIGITS[number]
    return _int_to_cn(number)


def _time_match_to_cn(match: re.Match) -> str:
    result = f"{_int_to_cn(int(match.group(1)))}点{_clock_tail_to_cn(match.group(2))}分"
    if match.group(3):
        result += f"{_clock_tail_to_cn(match.group(3))}秒"
    return result


def _normalize_tts_speech_text(text: str) -> str:
    """Build temporary TTS reading text without changing subtitles."""
    text = text.strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])[\.\u00b7·](?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)",
                  lambda m: f"{_digitwise(m.group(1))}年{_int_to_cn(int(m.group(2)))}月{_int_to_cn(int(m.group(3)))}日",
                  text)
    text = re.sub(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日",
                  lambda m: f"{_digitwise(m.group(1))}年{_int_to_cn(int(m.group(2)))}月{_int_to_cn(int(m.group(3)))}日",
                  text)
    text = re.sub(r"(?<!\d)(\d{3,4})(?=年)",
                  lambda m: _digitwise(m.group(1)), text)
    text = re.sub(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", _time_match_to_cn, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: "百分之" + _number_to_cn(m.group(1)), text)
    text = re.sub(r"(?<![A-Za-z0-9])Q([1-4])(?![A-Za-z0-9])",
                  lambda m: f"第{_int_to_cn(int(m.group(1)))}季度", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])([A-Za-z]{1,8})-(\d+(?:\.\d+)?)(?![A-Za-z0-9])",
                  lambda m: f"{m.group(1)} {_number_to_cn(m.group(2))}", text)
    text = re.sub(r"(?<![A-Za-z])(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)(?![A-Za-z])",
                  lambda m: f"{_number_to_cn(m.group(1))}到{_number_to_cn(m.group(2))}", text)
    text = re.sub(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)",
                  lambda m: f"{_int_to_cn(int(m.group(2)))}分之{_int_to_cn(int(m.group(1)))}", text)
    text = re.sub(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)",
                  lambda m: f"{_int_to_cn(int(m.group(1)))}比{_int_to_cn(int(m.group(2)))}", text)
    text = re.sub(r"(?<!\d)(\d{3,4})\s*[pP]\b", lambda m: _digitwise(m.group(1)) + "P", text)
    text = re.sub(r"(?<!\d)(\d+(?:\.\d+)?)\s*[kK]\b", lambda m: _number_to_cn(m.group(1)) + "K", text)
    text = re.sub(r"(?<!\d)(\d+(?:\.\d+)?)\s*fps\b",
                  lambda m: "每秒" + _number_to_cn(m.group(1)) + "帧", text, flags=re.I)
    unit_map = {
        "kg": "千克", "km": "公里", "m": "米", "cm": "厘米", "mm": "毫米",
        "s": "秒", "ms": "毫秒", "h": "小时",
    }
    for unit, spoken in sorted(unit_map.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<!\d)(\d+(?:\.\d+)?)\s*{unit}\b",
                      lambda m, spoken=spoken: _number_to_cn(m.group(1)) + spoken,
                      text, flags=re.I)
    text = re.sub(r"(?<!\d)0\d+(?!\d)", lambda m: _digitwise(m.group(0)), text)
    text = re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])",
                  lambda m: _number_to_cn(m.group(1)), text)
    return text


def prepare_tts_speech_script(segments: list["NarrationSegment"], folder: Path) -> dict[int, str]:
    speech_texts = {segment.segment_id: _normalize_tts_speech_text(segment.text)
                    for segment in segments}
    output = folder / "\u914d\u97f3\u7a3f_\u6717\u8bfb\u7248.txt"
    output.write_text("\n".join(speech_texts[segment.segment_id] for segment in segments), "utf-8")
    return speech_texts


def probe_duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


def format_srt_time(value: float) -> str:
    value = max(0.0, value)
    ms = round(value * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class NarrationSegment:
    segment_id: int
    text: str
    source_chunk_ids: list[int]
    source_start: float
    source_end: float
    visual_intent: str
    importance: str
    audio_file: str = ""
    audio_offset: float = 0.0
    audio_duration: float = 0.0
    output_start: float = 0.0
    output_end: float = 0.0
    clip_start: float = 0.0
    clip_end: float = 0.0
    match_confidence: str = ""
    row_type: str = "narration"
    source_audio_mode: str = "narration_only"
    insert_role_label: str = ""
    script_row_id: int = 0
    shot_index: int = 1
    shot_count: int = 1
    visual_match_score: float = 0.0
    visual_match_evidence: str = ""
    tts_parent_id: int = 0
    # For source clips: sub-intervals (absolute source seconds) to keep after
    # cutting >1s speech pauses. Empty = play [clip_start, clip_start+audio_duration] whole.
    keep_ranges: list = field(default_factory=list)


def write_plain_script(data: dict, output: Path) -> None:
    output.write_text("\n".join(x["text"] for x in data["segments"]), "utf-8")



def synthesize_qwen_clone(segments: list[NarrationSegment], folder: Path, api_key: str,
                          model: str, voice: str, rate: float,
                          volume: int = 55, pitch: float = 1.0,
                          reference_audio: str = DEFAULT_QWEN_REFERENCE_AUDIO,
                          reference_text_path: str = DEFAULT_QWEN_REFERENCE_TEXT_PATH,
                          speech_texts: dict[int, str] | None = None) -> None:
    """Generate sentence-aligned WAV files with a Bailian cloned voice (concurrent)."""
    force_ipv4()
    model = model or DEFAULT_QWEN_CLONE_MODEL
    if not is_qwen_realtime_model(model):
        # A configured clone ID is already reusable.  Re-validating/creating it
        # on every render adds a network round-trip before the local WAV cache
        # can be used, and can stall otherwise offline-safe rerenders.
        if not voice:
            voice, _, _ = ensure_qwen_clone_voice(
                api_key,
                model,
                voice,
                reference_audio,
                reference_text_path,
                ROOT / "voice_dabao_bailian.json",
            )
        reference_text = read_reference_text(reference_text_path)
        safe_voice = re.sub(r"[^A-Za-z0-9_-]", "_", voice)[-32:]
        voice_digest = hashlib.sha1(
            f"{model}|{voice}|{reference_audio}|{reference_text}".encode("utf-8", errors="ignore")
        ).hexdigest()[:10]
        seg_dir = folder / f"_anchored_tts_bailian_http_{safe_voice}_{voice_digest}"
        seg_dir.mkdir(exist_ok=True)
        progress_lock = threading.Lock()
        completed = 0
        total = len(segments)

        def _synth_single_http(index: int, segment: NarrationSegment) -> None:
            nonlocal completed
            tts_text = (speech_texts or {}).get(segment.segment_id, segment.text)
            digest = hashlib.sha1(tts_text.encode("utf-8")).hexdigest()[:10]
            raw_target = seg_dir / f"tts_{index:04d}_{digest}.wav"
            target = (seg_dir / f"tts_{index:04d}_{digest}_v{int(volume)}.wav"
                      if int(volume) > 100 else raw_target)
            if not raw_target.exists() or raw_target.stat().st_size < 1000:
                retry_call(
                    lambda: synthesize_qwen_http_to_file(api_key, model, voice, tts_text, raw_target),
                    attempts=4, base_delay=2.0,
                    on_retry=lambda a, e, d: print(
                        f"  [重试] 配音第{index}段 第{a}次失败：{e}；{d:.0f}s 后重试", flush=True),
                )
            if int(volume) > 100 and (not target.exists() or target.stat().st_size < 1000):
                gain = max(1.0, min(2.0, float(volume) / 100.0))
                run(["ffmpeg", "-y", "-i", str(raw_target), "-af",
                     f"volume={gain:.3f},alimiter=limit=0.98", "-c:a", "pcm_s16le", str(target)],
                    timeout=300)
            segment.audio_file = str(target)
            segment.audio_duration = probe_duration(target)
            with progress_lock:
                completed += 1
                print(f"  Bailian HTTP TTS {completed}/{total} {segment.audio_duration:.2f}s", flush=True)

        workers = min(get_concurrency(), 5)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_synth_single_http, i, seg) for i, seg in enumerate(segments, 1)]
            for future in as_completed(futures):
                future.result()
        return

    import dashscope
    from dashscope.audio.qwen_tts_realtime import (
        AudioFormat, QwenTtsRealtime, QwenTtsRealtimeCallback,
    )

    class Callback(QwenTtsRealtimeCallback):
        def __init__(self):
            self.done = threading.Event()
            self.audio = bytearray()
            self.error = None

        def on_event(self, response):
            kind = response.get("type")
            if kind == "response.audio.delta":
                self.audio.extend(base64.b64decode(response["delta"]))
            elif kind == "response.done":
                self.done.set()
            elif kind == "error":
                self.error = response
                self.done.set()

        def on_close(self, code, message):
            if code not in (None, 1000):
                self.error = {"code": code, "message": message}
            self.done.set()

    dashscope.api_key = api_key
    safe_voice = re.sub(r"[^A-Za-z0-9_-]", "_", voice)[-32:]
    safe_model = re.sub(r"[^A-Za-z0-9_-]", "_", model)[-32:]
    requested_volume = max(0, min(200, int(volume)))
    volume = min(100, requested_volume)
    pitch = max(0.5, min(2.0, float(pitch)))
    seg_dir = folder / f"_anchored_tts_qwen_{safe_model}_{safe_voice}_r{rate:.2f}_v{volume}_p{pitch:.2f}"
    seg_dir.mkdir(exist_ok=True)
    progress_lock = threading.Lock()
    completed = 0
    total = len(segments)

    def _synth_single(index: int, segment: NarrationSegment) -> None:
        nonlocal completed
        tts_text = (speech_texts or {}).get(segment.segment_id, segment.text)
        digest = hashlib.sha1(tts_text.encode("utf-8")).hexdigest()[:10]
        target = seg_dir / f"tts_{index:04d}_{digest}.wav"
        if target.exists() and target.stat().st_size >= 1000:
            segment.audio_file = str(target)
            segment.audio_duration = probe_duration(target)
        else:
            cb = Callback()
            tts = QwenTtsRealtime(model=model, callback=cb)
            try:
                tts.connect()
                tts.update_session(
                    voice=voice,
                    response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    sample_rate=24000,
                    speech_rate=rate,
                    pitch_rate=pitch,
                    volume=volume,
                    language_type="Chinese",
                    mode="commit",
                )
                tts.append_text(tts_text)
                tts.commit()
                if not cb.done.wait(180):
                    raise TimeoutError(f"第 {index} 段配音超时")
                if cb.error:
                    raise RuntimeError(f"第 {index} 段配音失败: {cb.error}")
                if not cb.audio:
                    raise RuntimeError(f"第 {index} 段没有返回音频")
                with wave.open(str(target), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(24000)
                    wav.writeframes(cb.audio)
                segment.audio_file = str(target)
                segment.audio_duration = probe_duration(target)
            finally:
                try:
                    tts.finish()
                except Exception:
                    pass
        with progress_lock:
            nonlocal completed
            completed += 1
            print(f"  Qwen TTS {completed}/{total} {segment.audio_duration:.2f}s", flush=True)

    workers = min(get_concurrency(), 5)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_synth_single, i, seg) for i, seg in enumerate(segments, 1)]
        for future in as_completed(futures):
            future.result()


def concat_audio(segments: list[NarrationSegment], folder: Path) -> Path:
    concat = folder / "_anchored_audio_concat.txt"
    pieces = []
    for segment in segments:
        pieces.append(f"file '{Path(segment.audio_file).as_posix()}'\n")
    concat.write_text("".join(pieces), "utf-8")
    output = folder / "配音.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output)], timeout=1200)
    cursor = 0.0
    for segment in segments:
        segment.output_start = cursor
        cursor += segment.audio_duration
        segment.output_end = cursor
    return output


def _silence_boundaries(audio_file: Path, duration: float) -> list[float]:
    """Return the middle of short natural pauses in a narration block."""
    command = [
        ffmpeg(), "-hide_banner", "-nostats", "-i", str(audio_file),
        "-af", "silencedetect=noise=-38dB:d=0.07", "-f", "null", "-",
    ]
    result = subprocess.run(
        command, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180,
    )
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    boundaries: list[float] = []
    end_index = 0
    for start in starts:
        while end_index < len(ends) and ends[end_index] <= start:
            end_index += 1
        if end_index >= len(ends):
            break
        end = ends[end_index]
        end_index += 1
        middle = (start + end) / 2.0
        if 0.25 < middle < duration - 0.25:
            boundaries.append(middle)
    return boundaries


def _clause_audio_ranges(clauses: list[str], duration: float,
                         silence_points: list[float]) -> list[tuple[float, float]]:
    if len(clauses) <= 1:
        return [(0.0, duration)]
    weights = [max(1, len(re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text))) for text in clauses]
    total_weight = sum(weights)
    targets: list[float] = []
    running = 0
    for weight in weights[:-1]:
        running += weight
        targets.append(duration * running / total_weight)

    cuts: list[float] = []
    available = list(silence_points)
    minimum_slice = min(0.55, max(0.28, duration / (len(clauses) * 4.0)))
    for index, target in enumerate(targets):
        lower = cuts[-1] + minimum_slice if cuts else minimum_slice
        remaining = len(targets) - index
        upper = duration - remaining * minimum_slice
        candidates = [point for point in available if lower <= point <= upper]
        nearby = [point for point in candidates if abs(point - target) <= 1.35]
        cut = min(nearby or candidates or [max(lower, min(upper, target))],
                  key=lambda point: abs(point - target))
        cuts.append(cut)
        available = [point for point in available if point > cut + 0.04]

    edges = [0.0, *cuts, duration]
    return [(edges[index], edges[index + 1]) for index in range(len(clauses))]


MIN_SHOT_SECONDS = 0.5  # every visual shot must stay on screen longer than this


def _merge_short_shots(clauses: list[str], ranges: list[tuple[float, float]],
                       min_shot: float = MIN_SHOT_SECONDS) -> tuple[list[str], list[tuple[float, float]]]:
    """Merge visual clauses so every shot's audio span exceeds ``min_shot`` seconds.

    A shot shorter than the floor flashes on screen. Accumulate consecutive
    clauses until the running span clears the floor, then flush; any trailing
    remainder folds back into the previous shot so nothing under the floor ships.
    """
    if not ranges:
        return list(clauses), list(ranges)
    out_clauses: list[str] = []
    out_ranges: list[tuple[float, float]] = []
    buffer_text = ""
    buffer_start: float | None = None
    for clause, (start, end) in zip(clauses, ranges):
        if buffer_start is None:
            buffer_start = start
        buffer_text += clause
        if end - buffer_start >= min_shot:
            out_clauses.append(buffer_text)
            out_ranges.append((buffer_start, end))
            buffer_text, buffer_start = "", None
    if buffer_start is not None:
        if out_ranges:
            out_ranges[-1] = (out_ranges[-1][0], ranges[-1][1])
            out_clauses[-1] += buffer_text
        else:
            out_clauses.append(buffer_text)
            out_ranges.append((buffer_start, ranges[-1][1]))
    return out_clauses, out_ranges


def expand_narration_visual_shots(parents: list[NarrationSegment]) -> list[NarrationSegment]:
    """Split full-block TTS into visual clauses without synthesising the voice again."""
    children: list[NarrationSegment] = []
    for parent in parents:
        clauses = split_visual_clauses(parent.text) or [parent.text]
        pauses = _silence_boundaries(Path(parent.audio_file), parent.audio_duration)
        ranges = _clause_audio_ranges(clauses, parent.audio_duration, pauses)
        clauses, ranges = _merge_short_shots(clauses, ranges)
        for shot_index, (clause, (start, end)) in enumerate(zip(clauses, ranges), 1):
            children.append(NarrationSegment(
                segment_id=len(children) + 1,
                text=clause,
                source_chunk_ids=list(parent.source_chunk_ids),
                source_start=parent.source_start,
                source_end=parent.source_end,
                visual_intent=clause,
                importance=parent.importance,
                audio_file=parent.audio_file,
                audio_offset=start,
                audio_duration=max(0.01, end - start),
                row_type=parent.row_type,
                source_audio_mode=parent.source_audio_mode,
                insert_role_label=parent.insert_role_label,
                script_row_id=parent.script_row_id,
                shot_index=shot_index,
                shot_count=len(clauses),
                tts_parent_id=parent.segment_id,
            ))
        print(
            f"  配音段 {parent.segment_id}: {parent.audio_duration:.2f}s -> "
            f"{len(clauses)} 个画面节点（检测到 {len(pauses)} 个停顿）",
            flush=True,
        )
    return children


def _load_scene_map(folder: Path) -> tuple[list[dict], list[dict]]:
    """Load an optional per-episode 大镜头/场景段 map (`_scene_map.json`).

    Each scene defines source-time ranges plus keywords/characters. Narration
    classified to a scene draws its footage only from that scene's ranges — this
    keeps "开会" narration on the meeting footage, "活动现场" on the event, etc.
    Returns (scenes, overrides); overrides pin an exact narration snippet to a
    named scene (highest priority) for lines that carry no scene keyword.
    """
    path = folder / "_scene_map.json"
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    raw = data.get("scenes", []) if isinstance(data, dict) else data
    scenes: list[dict] = []
    for sc in raw:
        ranges = [(float(a), float(b)) for a, b in sc.get("ranges", []) if b > a]
        if not ranges and sc.get("start") is not None and sc.get("end") is not None:
            ranges = [(float(sc["start"]), float(sc["end"]))]
        if ranges:
            scenes.append({
                "name": sc.get("name", ""),
                "ranges": ranges,
                "keywords": [k for k in sc.get("keywords", []) if k],
                "characters": [c for c in sc.get("characters", []) if c],
            })
    overrides = [
        {"contains": o.get("contains", ""), "scene": o.get("scene", "")}
        for o in (data.get("overrides", []) if isinstance(data, dict) else [])
        if o.get("contains") and o.get("scene")
    ]
    return scenes, overrides


def _classify_scene(text: str, scenes: list[dict], overrides: list[dict] | None = None) -> dict | None:
    """Assign narration text to a scene. Overrides (exact snippet → scene) win
    first — use them for emotion/relationship lines that carry no scene keyword.
    Otherwise keywords are the strong discriminator (x2), characters a weak
    tiebreak (x1); requires >=1 keyword hit so only scene-explicit narration is
    locked, everything else falls through to the global semantic match."""
    if overrides:
        by_name = {sc["name"]: sc for sc in scenes}
        for ov in overrides:
            if ov["contains"] in text and ov["scene"] in by_name:
                return by_name[ov["scene"]]
    best: dict | None = None
    best_score = 0
    for sc in scenes:
        kw = sum(1 for k in sc["keywords"] if k in text)
        if kw < 1:
            continue
        ch = sum(1 for c in sc["characters"] if c in text)
        score = kw * 2 + ch
        if score > best_score:
            best_score = score
            best = sc
    return best


def allocate_visual_all(segments: list[NarrationSegment], source_clips: list[NarrationSegment],
                        video_duration: float, folder: Path,
                        usable_start: float = 0.0) -> VisualIntervalAllocator:
    """Allocate visual intervals for narration shots.

    Uses LLM text-embedding semantic matching when a DashScope key is available
    (frame vectors cached in the folder); falls back to n-gram matching otherwise.
    An optional `_scene_map.json` hard-locks scene-explicit narration to the
    matching 原片 场景段 (会议/病房/活动现场 …).
    """
    ad_intervals = detect_ad_intervals(folder)
    frames = load_visual_frames(folder)

    # --- embedding setup (optional, cached) --------------------------------------
    frame_vecs: dict[float, list[float]] = {}
    query_vecs: dict[int, list[float]] = {}
    narration_segments = [s for s in segments if s.row_type == "narration"]
    try:
        from backend.embed_match import dashscope_key, frame_embeddings, embed_texts
        api_key = dashscope_key()
        if api_key and narration_segments:
            frame_texts = [f.evidence for f in frames]
            print(f"语义匹配：嵌入 {len(frame_texts)} 帧（缓存复用）…", flush=True)
            vecs = frame_embeddings(folder, frame_texts, api_key)
            frame_vecs = {frames[i].time: vecs[i] for i in range(min(len(frames), len(vecs)))}
            queries = [(s.visual_intent or s.text) for s in narration_segments]
            print(f"语义匹配：嵌入 {len(queries)} 个解说分镜…", flush=True)
            qv = embed_texts(queries, api_key)
            for seg, vec in zip(narration_segments, qv):
                query_vecs[id(seg)] = vec
    except Exception as exc:
        print(f"语义嵌入不可用，回退 n-gram 匹配：{exc}", flush=True)
        frame_vecs = {}
        query_vecs = {}

    protagonist = dominant_character_group(
        [(s.visual_intent or s.text) for s in narration_segments]
    )
    allocator = VisualIntervalAllocator(
        video_duration, frames, usable_start=usable_start,
        blocked_intervals=ad_intervals, frame_vecs=frame_vecs,
        protagonist_group=protagonist,
    )
    if protagonist is not None:
        print(f"隐含主角回退：无点名解说默认偏向角色组 #{protagonist}", flush=True)
    if ad_intervals:
        print(f"已启用插片广告禁区 {len(ad_intervals)} 段，原片与解说画面均禁止使用", flush=True)
    for clip in source_clips:
        allocator.reserve_source_clip(clip.clip_start, clip.clip_end, f"原片行{clip.script_row_id}")

    scenes, overrides = _load_scene_map(folder)
    # Reconstruct each narration sentence's full text from its shots so every
    # shot of a sentence shares the sentence's scene classification (a clause
    # like "接连反对庄国栋" alone might miss the "会议上" keyword in a sibling clause).
    parent_text: dict = {}
    if scenes:
        for seg in segments:
            if seg.row_type == "narration":
                pid = getattr(seg, "tts_parent_id", None) or seg.script_row_id
                parent_text[pid] = parent_text.get(pid, "") + (seg.text or "")
        print(f"已加载场景段地图：{len(scenes)} 个场景（{len(overrides)} 条文本指定），解说按场景锁定匹配", flush=True)

    group_cursor: dict[int, float] = {}
    locked_log: list[str] = []
    for segment in sorted(segments, key=lambda item: (item.script_row_id, item.shot_index)):
        chronological_start = group_cursor.get(segment.script_row_id, segment.source_start)
        scene_ranges = None
        if scenes and segment.row_type == "narration":
            pid = getattr(segment, "tts_parent_id", None) or segment.script_row_id
            own = segment.visual_intent or segment.text or ""
            # 先按「本分镜自己的文字」分类（override 命中 or 关键词命中）——一句解说常
            # 跨多个场景（哥哥的食堂线 + 玫瑰的会议线写在同一句），整句级分类会把所有
            # 镜头钉到同一场景造成张冠李戴；按镜头各自内容锁定才能各归各位。
            sc = _classify_scene(own, scenes, overrides)
            if not sc:
                # 本镜头自己无场景信号（纯情绪/关系/旁白）→ 回退整句分类（保留原有 override 行为）
                ctext = parent_text.get(pid, "") or own
                sc = _classify_scene(ctext, scenes, overrides)
            if sc:
                scene_ranges = sc["ranges"]
                locked_log.append(
                    f"  [场景锁定] 行{segment.script_row_id}-镜{segment.shot_index} → 「{sc['name']}」"
                )
        start, end, score, evidence = allocator.allocate(
            segment.visual_intent or segment.text,
            segment.audio_duration,
            segment.source_start,
            segment.source_end,
            f"解说行{segment.script_row_id}-镜头{segment.shot_index}",
            chronological_start=chronological_start,
            query_vec=query_vecs.get(id(segment)),
            scene_ranges=scene_ranges,
        )
        segment.clip_start = start
        segment.clip_end = end
        segment.visual_match_score = round(score, 4)
        segment.visual_match_evidence = evidence
        segment.match_confidence = "A" if score >= 0.42 else ("B" if score >= 0.26 else "C")
        group_cursor[segment.script_row_id] = end + allocator.guard
    if locked_log:
        print(f"场景锁定命中 {len(locked_log)} 个解说镜头：", flush=True)
        for line in locked_log:
            print(line, flush=True)
    return allocator


def apply_hierarchical_takeover(segments: list[NarrationSegment], allocator: VisualIntervalAllocator,
                                folder: Path) -> None:
    path = folder / "★ 分层接管预演报告.json"
    if not path.exists():
        raise RuntimeError("缺少 ★ 分层接管预演报告.json，请先运行 dy shadow-match")
    payload = json.loads(path.read_text("utf-8"))
    scene_path = folder / "_scene_map.json"
    validate_scene_map(folder)
    if payload.get("scene_map_sha256") != scene_map_digest(scene_path):
        raise RuntimeError("场景地图已在预演后变化，请重新运行 dy shadow-match")
    if not payload.get("safe_to_render") or payload.get("planning_summary", {}).get("unresolved"):
        raise RuntimeError("分层接管预演未通过，禁止接管正式链路")
    planned = {(int(item["script_row_id"]), int(item["shot_index"])): item
               for item in payload.get("segments", [])}
    narration = [item for item in segments if item.row_type == "narration"]
    if len(planned) != len(narration):
        raise RuntimeError(f"接管预演分镜数不一致：预演 {len(planned)} / 当前 {len(narration)}")
    retained = [item for item in allocator.used if str(item[2]).startswith("原片行")]
    new_used = list(retained)
    narration_used: list[tuple[float, float, str]] = []
    for segment in narration:
        stable_key = (int(segment.script_row_id), int(segment.shot_index))
        item = planned.get(stable_key)
        if not item or str(item.get("text") or "") != str(segment.text or ""):
            raise RuntimeError(f"接管预演与当前文案不一致：分镜 {segment.segment_id}")
        start, end = float(item["clip_start"]), float(item["clip_end"])
        if abs((end - start) - float(segment.audio_duration)) > 0.01:
            raise RuntimeError(f"接管预演与当前配音时长不一致：分镜 {segment.segment_id}")
        is_reviewed_reuse = (item.get("planned_event_id") == "manual_override"
                             or ":plan" in str(item.get("continuity_group_id") or ""))
        unavailable = narration_used if is_reviewed_reuse else [*allocator.blocked, *new_used]
        if any(not (end + allocator.guard <= left or start >= right + allocator.guard)
               for left, right, _ in unavailable):
            raise RuntimeError(f"接管区间冲突或命中广告：分镜 {segment.segment_id}")
        segment.clip_start, segment.clip_end = start, end
        segment.visual_match_score = float(item.get("shadow_score") or 0)
        segment.visual_match_evidence = f"分层接管 {item.get('planned_event_id')} / {item.get('scene_hint')}"
        segment.match_confidence = "H"
        new_used.append((start, end, f"分层解说{segment.segment_id}"))
        narration_used.append((start, end, f"分层解说{segment.segment_id}"))
    allocator.used = new_used
    print(f"分层匹配正式接管：{len(narration)} 个解说分镜", flush=True)


def load_script_table_source_clips(folder: Path, usable_start: float, usable_end: float,
                                   clip_length: float) -> list[NarrationSegment]:
    table_path = folder / "_drama_script_table.json"
    if not table_path.exists():
        return []
    try:
        payload = json.loads(table_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    is_manual = payload.get("script_source") == "manual_upload"
    clips: list[NarrationSegment] = []
    for row in payload.get("rows", []):
        if row.get("row_type") != "source_clip":
            continue
        try:
            start = max(usable_start, float(row.get("source_start")))
            row_end = float(row.get("source_end"))
        except (TypeError, ValueError):
            continue
        if start >= usable_end:
            continue
        exact_duration = bool(row.get("use_exact_duration")) or is_manual
        desired = max(1.0, row_end - start) if exact_duration else (
            clip_length if clip_length > 0 else max(1.0, row_end - start)
        )
        duration = min(max(1.0, desired), usable_end - start)
        if duration <= 0.5:
            continue
        end = start + duration
        text = re.sub(r"^原片对白[:：]\s*", "", str(row.get("text", ""))).strip()
        clips.append(NarrationSegment(
            segment_id=-(len(clips) + 1),
            text=text or "原片对白",
            source_chunk_ids=[],
            source_start=start,
            source_end=end,
            visual_intent=str(row.get("visual_intent", "")),
            importance=str(row.get("insert_role", "source_clip")),
            audio_duration=duration,
            output_start=0.0,
            output_end=0.0,
            clip_start=start,
            clip_end=end,
            match_confidence="S",
            row_type="source_clip",
            source_audio_mode="keep_dialogue",
            insert_role_label=str(row.get("insert_role_label", "原片对白")),
            script_row_id=int(row.get("row_id", len(clips) + 1)),
        ))
    return clips


def is_manual_script_table(folder: Path) -> bool:
    table_path = folder / "_drama_script_table.json"
    if not table_path.exists():
        return False
    try:
        payload = json.loads(table_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("script_source") == "manual_upload"


def manual_narration_from_script_table(folder: Path) -> dict | None:
    table_path = folder / "_drama_script_table.json"
    if not table_path.exists():
        return None
    try:
        payload = json.loads(table_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("script_source") != "manual_upload":
        return None
    segments: list[dict] = []
    for row in payload.get("rows", []):
        if row.get("row_type") != "narration":
            continue
        try:
            start = float(row.get("source_start"))
            end = float(row.get("source_end"))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"^\s*解说\s*[:：]\s*", "", str(row.get("text", ""))).strip()
        if not text:
            continue
        segments.append({
            "segment_id": len(segments) + 1,
            "text": text,
            "source_chunk_ids": [int(row.get("row_id", 0))],
            "source_start": start,
            "source_end": end,
            "visual_intent": text,
            "importance": str(row.get("insert_role", "manual_narration")),
            "insert_role_label": str(row.get("insert_role_label", "解说")),
            "script_row_id": int(row.get("row_id", 0)),
            "tts_parent_id": len(segments) + 1,
        })
    if not segments:
        raise RuntimeError("手写文案脚本表没有可配音的“解说：”段落")
    return {
        "title": "手写文案成片",
        "segments": segments,
        "script_source": "manual_upload",
        "script_file": payload.get("script_file", ""),
    }


def build_timeline(source_clips: list[NarrationSegment],
                   narration_segments: list[NarrationSegment]) -> list[NarrationSegment]:
    if not source_clips:
        return list(narration_segments)

    source_items = list(source_clips)
    narration_items = list(narration_segments)
    timeline: list[NarrationSegment] = []

    manual_order = any(item.script_row_id for item in [*source_items, *narration_items])
    if manual_order:
        timeline = sorted(
            [*source_items, *narration_items],
            key=lambda item: (item.script_row_id, 0 if item.row_type == "source_clip" else item.shot_index),
        )
        cursor = 0.0
        for final_id, item in enumerate(timeline, 1):
            item.segment_id = final_id
            item.output_start = cursor
            cursor += item.audio_duration
            item.output_end = cursor
        return timeline

    if len(source_items) == len(narration_items):
        for source_item, narration_item in zip(source_items, narration_items):
            timeline.extend([source_item, narration_item])
        cursor = 0.0
        for final_id, item in enumerate(timeline, 1):
            item.segment_id = final_id
            item.output_start = cursor
            cursor += item.audio_duration
            item.output_end = cursor
        return timeline

    source_items = sorted(source_items, key=lambda item: (item.source_start, item.segment_id))
    narration_items = sorted(narration_items, key=lambda item: (item.source_start, item.segment_id))
    remaining = list(narration_items)
    max_pair_distance = 180.0

    for source_item in source_items:
        if not remaining:
            continue
        paired = min(
            remaining,
            key=lambda item: (abs(item.source_start - source_item.source_start), item.segment_id),
        )
        if abs(paired.source_start - source_item.source_start) > max_pair_distance:
            continue
        timeline.append(source_item)
        timeline.append(paired)
        remaining.remove(paired)

    cursor = 0.0
    for final_id, item in enumerate(timeline, 1):
        item.segment_id = final_id
        item.output_start = cursor
        cursor += item.audio_duration
        item.output_end = cursor
    return timeline


def _speech_keep_ranges(subtitles: list[tuple[float, float]], clip_start: float, clip_end: float,
                        max_pause: float = 1.0, pad: float = 0.15) -> list[tuple[float, float]]:
    """Keep speech, cut >max_pause pauses — driven by subtitle timings, not audio level.

    Drama clips carry background music/ambience, so acoustic silencedetect misses
    the real "nobody is speaking" gaps. Subtitle spans mark exactly when someone
    talks; a gap between consecutive subtitles longer than ``max_pause`` is the
    pause to remove. Returns absolute-source-time keep intervals (a single full
    span when nothing worth cutting), each padded by ``pad`` so speech isn't clipped.
    """
    span = clip_end - clip_start
    full = [(round(clip_start, 3), round(clip_end, 3))]
    spans = sorted(
        (max(clip_start, float(s)), min(clip_end, float(e)))
        for s, e in subtitles if float(e) > clip_start and float(s) < clip_end
    )
    spans = [(s, e) for s, e in spans if e - s > 0.05]
    if not spans:
        return full
    keeps: list[list[float]] = []
    prev_speech_end: float | None = None
    for s, e in spans:
        keep_start = max(clip_start, s - pad)
        keep_end = min(clip_end, e + pad)
        if keeps and prev_speech_end is not None and (s - prev_speech_end) <= max_pause:
            keeps[-1][1] = max(keeps[-1][1], keep_end)      # gap <= max_pause: keep rolling
        else:
            keeps.append([keep_start, keep_end])            # first span, or >max_pause gap: cut
        prev_speech_end = e if prev_speech_end is None else max(prev_speech_end, e)
    # Enforce the >0.5s minimum-shot rule on jump-cut sub-parts: grow any too-short
    # kept span into the surrounding trimmed pause instead of letting it flash.
    for idx, part in enumerate(keeps):
        start, end = part
        if end - start >= MIN_SHOT_SECONDS:
            continue
        need = MIN_SHOT_SECONDS - (end - start)
        forward_limit = keeps[idx + 1][0] if idx + 1 < len(keeps) else clip_end
        grow = min(need, max(0.0, forward_limit - end))
        end += grow
        need -= grow
        if need > 0:
            back_floor = keeps[idx - 1][1] if idx > 0 else clip_start
            start -= min(need, max(0.0, start - back_floor))
        part[0], part[1] = start, end
    ranges = [(round(a, 3), round(b, 3)) for a, b in keeps]
    if sum(b - a for a, b in ranges) >= span - 0.25:
        return full
    return ranges


def trim_source_clip_pauses(source: Path, source_clips: list[NarrationSegment],
                            subtitles: list[tuple[float, float]], max_pause: float = 1.0) -> None:
    """Cut >max_pause speech pauses inside each source clip in place.

    Sets ``keep_ranges`` on every clip and shrinks ``audio_duration`` to the kept
    total so the downstream timeline / SRT timing stays in sync.
    """
    for clip in source_clips:
        span = clip.clip_end - clip.clip_start
        ranges = _speech_keep_ranges(subtitles, clip.clip_start, clip.clip_end, max_pause)
        kept = sum(end - start for start, end in ranges)
        clip.keep_ranges = ranges
        if kept > 0.5 and kept < span - 0.25:
            clip.audio_duration = round(kept, 3)
            print(f"  原片 {clip.insert_role_label}: 剪除停顿 {span:.1f}s -> {kept:.1f}s"
                  f"（保留 {len(ranges)} 段）", flush=True)


def render_video(source: Path, narration: Path | None, segments: list[NarrationSegment], folder: Path,
                 target_seconds: float, include_source_audio: bool = False,
                 source_volume: float = 1.0,
                 narration_source_volume: float = 0.0) -> Path:
    # 硬性规范：配音与原片统一响度归一化到 -16 LUFS（手机播放标准），
    # 保证「配音和原片在正常听的时候音量一致」。纯增益(volume=)无法跨不同
    # 音源等响——克隆音色天然比原片轻近 19 LUFS，必须用 loudnorm 归一化。
    loudnorm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    clip_dir = folder / "_anchored_clips"
    if clip_dir.exists():
        shutil.rmtree(clip_dir)
    clip_dir.mkdir()

    def _cut_clip(index: int, segment: NarrationSegment) -> None:
        clip = clip_dir / f"clip_{index:04d}.mp4"
        vf = "scale=1920:1080:force_original_aspect_ratio=decrease," \
             "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25"
        if segment.row_type == "source_clip" and include_source_audio:
            ranges = segment.keep_ranges or [
                (segment.clip_start, segment.clip_start + segment.audio_duration)
            ]

            def _cut_range(dst: Path, start: float, end: float) -> None:
                run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
                     "-t", f"{max(0.05, end - start):.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                     "-vf", vf, "-af", f"{loudnorm},volume={source_volume:.4f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                     str(dst)], timeout=600)

            if len(ranges) == 1:
                _cut_range(clip, ranges[0][0], ranges[0][1])
            else:
                # >1s pauses removed → jump-cut: render each kept span, then concat.
                parts: list[Path] = []
                for part_index, (start, end) in enumerate(ranges):
                    part = clip_dir / f"clip_{index:04d}_p{part_index:02d}.mp4"
                    _cut_range(part, start, end)
                    parts.append(part)
                listfile = clip_dir / f"clip_{index:04d}_parts.txt"
                listfile.write_text(
                    "".join(f"file '{part.as_posix()}'\n" for part in parts), "utf-8"
                )
                run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                     "-c", "copy", str(clip)], timeout=600)
            return
        else:
            if not segment.audio_file:
                raise RuntimeError(f"第 {segment.segment_id} 句缺少配音文件")
            if narration_source_volume > 0:
                mix = (
                    f"[0:a:0]volume={narration_source_volume:.4f}[srca];"
                    f"[1:a:0]{loudnorm}[voice];"
                    "[srca][voice]amix=inputs=2:duration=shortest:normalize=0[aout]"
                )
                cmd = ["ffmpeg", "-y", "-ss", f"{segment.clip_start:.3f}", "-i", str(source),
                       "-ss", f"{segment.audio_offset:.3f}", "-i", segment.audio_file,
                       "-t", f"{segment.audio_duration:.3f}",
                       "-filter_complex", mix, "-map", "0:v:0", "-map", "[aout]",
                       "-vf", vf,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                       "-shortest", str(clip)]
            else:
                cmd = ["ffmpeg", "-y", "-ss", f"{segment.clip_start:.3f}", "-i", str(source),
                       "-ss", f"{segment.audio_offset:.3f}", "-i", segment.audio_file,
                       "-t", f"{segment.audio_duration:.3f}",
                       "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", loudnorm,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                       "-shortest", str(clip)]
        run(cmd, timeout=600)

    workers = get_concurrency()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_cut_clip, i, seg) for i, seg in enumerate(segments, 1)]
        for future in as_completed(futures):
            future.result()

    for i, segment in enumerate(segments, 1):
        print(f"  视频 {i}/{len(segments)} <- {segment.clip_start:.1f}-{segment.clip_end:.1f}s")
    concat = clip_dir / "concat.txt"
    concat.write_text("".join(f"file '{(clip_dir / f'clip_{i:04d}.mp4').as_posix()}'\n"
                              for i in range(1, len(segments) + 1)), "utf-8")
    silent = folder / "_anchored_silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-c", "copy", str(silent)], timeout=1200)
    raw_output = folder / "_anchored_muxed.mp4"
    shutil.copy2(silent, raw_output)
    output = folder / "★ 成片.mp4"
    shutil.copy2(raw_output, output)
    return output


def write_outputs(data: dict, segments: list[NarrationSegment], allocator: VisualIntervalAllocator,
                  folder: Path) -> None:
    narration_segments = [segment for segment in segments if segment.row_type == "narration"]
    manual_order = any(str(segment.importance).startswith("manual_") for segment in segments)
    occupied = [
        {"start": segment.clip_start, "end": segment.clip_end, "segment_id": segment.segment_id}
        for segment in segments
    ]
    source_order = sorted(segments, key=lambda item: (item.clip_start, item.clip_end))
    overlap_count = sum(
        source_order[index].clip_start < source_order[index - 1].clip_end - 1e-6
        for index in range(1, len(source_order))
    )
    manifest = {
        "title": data.get("title", ""),
        "segments": [asdict(x) for x in segments],
        "occupied_intervals": sorted(occupied, key=lambda item: (item["start"], item["end"])),
        "excluded_ad_intervals": [
            {"start": left, "end": right, "label": label}
            for left, right, label in allocator.blocked
        ],
        "validation": {
            "interval_overlap_count": overlap_count,
            "global_no_reuse": overlap_count == 0 and len(allocator.used) == len(segments),
            "occupied_interval_count": len(allocator.used),
            "excluded_ad_interval_count": len(allocator.blocked),
            "source_backtrack_count": sum(
                narration_segments[i].clip_start < narration_segments[i - 1].clip_end
                for i in range(1, len(narration_segments))
            ),
            "timeline_backtrack_count": 0 if manual_order else sum(
                segments[i].clip_start < segments[i - 1].clip_end
                for i in range(1, len(segments))
            ),
            "all_segments_anchored": all(x.source_chunk_ids or x.row_type == "source_clip" for x in segments),
            "confidence_counts": {grade: sum(x.match_confidence == grade for x in segments)
                                  for grade in "ABCS"},
            "source_clip_count": sum(x.row_type == "source_clip" for x in segments),
            "narration_count": len(narration_segments),
            "narration_block_count": len({x.script_row_id for x in narration_segments}),
        },
    }
    (folder / "★ 匹配报告.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    srt_lines = []
    for i, segment in enumerate(narration_segments, 1):
        srt_lines.extend([str(i), f"{format_srt_time(segment.output_start)} --> "
                                 f"{format_srt_time(segment.output_end)}", segment.text, ""])
    (folder / "★ 字幕.srt").write_text("\n".join(srt_lines), "utf-8")


def _natural_file_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.stem.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def _looks_like_source_file(path: Path) -> bool:
    name = path.stem.lower()
    if name.startswith("_") or name.startswith("★"):
        return False
    blocked = (
        "成片",
        "输出",
        "发布",
        "匹配报告",
        "发布信息",
        "配音",
        "封面",
        "anchored",
        "muxed",
        "silent",
        "tts",
        "final",
        "output",
        "result",
    )
    return not any(token in name for token in blocked)


def discover(folder: Path) -> tuple[Path, Path, Path]:
    all_videos = sorted([*folder.glob("*.mp4"), *folder.glob("*.mkv"), *folder.glob("*.mov")],
                        key=_natural_file_key)
    videos = [path for path in all_videos if _looks_like_source_file(path)] or all_videos
    all_subtitles = sorted(
        [path for path in [*folder.glob("*.srt"), *folder.glob("*.ass")] if _looks_like_source_file(path)],
        key=_natural_file_key,
    ) or sorted([*folder.glob("*.srt"), *folder.glob("*.ass")], key=_natural_file_key)
    zh = (
        sorted([*folder.glob("*.zh-Hans.srt"), *folder.glob("*.zh-Hans.ass")])
        or sorted([*folder.glob("*zh*.srt"), *folder.glob("*zh*.ass")])
        or all_subtitles
    )
    en = (
        sorted([*folder.glob("*.en-orig.srt"), *folder.glob("*.en-orig.ass")])
        or sorted([*folder.glob("*.en.srt"), *folder.glob("*.en.ass")])
        or all_subtitles
    )
    if not videos or not all_subtitles:
        raise RuntimeError("素材目录必须包含视频和至少一个 SRT/ASS 字幕")
    return videos[0], zh[0], en[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="来源锚定、原片/解说分轨的电视剧解说流水线")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--ratio", type=float, default=0.5)
    parser.add_argument("--target-seconds", type=float, default=None)
    parser.add_argument("--qwen-voice", default="")
    parser.add_argument("--qwen-model", default="")
    parser.add_argument("--qwen-reference-audio", default=DEFAULT_QWEN_REFERENCE_AUDIO)
    parser.add_argument("--qwen-reference-text-path", default=DEFAULT_QWEN_REFERENCE_TEXT_PATH)
    parser.add_argument("--qwen-volume", type=int, default=120)
    parser.add_argument("--qwen-pitch", type=float, default=1.0)
    parser.add_argument("--speech-rate", type=float, default=1.0)
    parser.add_argument("--trim-head", type=float, default=6.0)
    parser.add_argument("--trim-tail", type=float, default=15.0)
    parser.add_argument("--include-source-audio", action="store_true")
    parser.add_argument("--source-volume", type=float, default=0.5)
    parser.add_argument("--narration-source-volume", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--no-render", action="store_true",
                        help="只做匹配并写 ★ 匹配报告.json / ★ 字幕.srt，跳过成片渲染")
    parser.add_argument("--hierarchical-match", action="store_true",
                        help="使用已验证的分层接管预演覆盖正式解说画面")
    args = parser.parse_args()

    folder = args.folder.resolve()
    source, zh_subtitle, _ = discover(folder)
    duration = probe_duration(source)
    target_seconds = args.target_seconds if args.target_seconds is not None else duration * args.ratio
    target_seconds = max(30.0, min(target_seconds, duration))
    print(f"原片 {duration:.1f}s，目标 {target_seconds:.1f}s ({args.ratio:.0%})")

    usable_end = duration - args.trim_tail
    manual_script = is_manual_script_table(folder)
    if not manual_script:
        raise RuntimeError("只支持用户上传的“原片/解说”手写文案，请先点击生成脚本表")
    env = {**load_env(ROOT / ".env"), **os.environ}
    source_clips = load_script_table_source_clips(
        folder, args.trim_head, usable_end, 20.0
    ) if args.include_source_audio else []
    if source_clips:
        srt_spans = [(float(item.start), float(item.end)) for item in parse_srt(zh_subtitle)]
        trim_source_clip_pauses(source, source_clips, srt_spans, max_pause=1.0)
    source_insert_seconds = sum(item.audio_duration for item in source_clips)
    narration_target_seconds = max(30.0, target_seconds - source_insert_seconds)
    if source_clips:
        print(f"原片对白段 {len(source_clips)} 段，约 {source_insert_seconds:.1f}s；"
              f"解说目标约 {narration_target_seconds:.1f}s")

    narration_file = folder / "_narration_manifest.json"
    data = manual_narration_from_script_table(folder)
    if data is None:
        raise RuntimeError("手写文案脚本表损坏，请重新生成脚本表")
    temp_output = narration_file.with_suffix(".tmp")
    temp_output.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    temp_output.replace(narration_file)
    print("已按用户上传文案生成整段配音任务，不调用任何生文案模型")
    write_plain_script(data, folder / "配音稿.txt")
    print(f"文案 {len(data['segments'])} 段，{sum(len(x['text']) for x in data['segments'])} 字")
    parent_segments = [NarrationSegment(**x) for x in data["segments"]]
    speech_texts = prepare_tts_speech_script(parent_segments, folder)
    profile_path = ROOT / "voice_dabao_bailian.json"
    profile = json.loads(profile_path.read_text("utf-8")) if profile_path.exists() else {}
    voice = args.qwen_voice or profile.get("voice", "")
    model = args.qwen_model or profile.get("target_model", DEFAULT_QWEN_CLONE_MODEL)
    reference_audio = args.qwen_reference_audio or profile.get("reference_audio", DEFAULT_QWEN_REFERENCE_AUDIO)
    reference_text_path = args.qwen_reference_text_path or profile.get("reference_text_path", DEFAULT_QWEN_REFERENCE_TEXT_PATH)
    if not voice:
        if is_qwen_realtime_model(model):
            raise RuntimeError("未配置 Qwen 复刻音色 ID")
        voice = profile.get("voice", "")
    synthesize_qwen_clone(parent_segments, folder, env.get("DASHSCOPE_API_KEY", ""),
                          model, voice, args.speech_rate,
                          volume=args.qwen_volume, pitch=args.qwen_pitch,
                          reference_audio=reference_audio,
                          reference_text_path=reference_text_path,
                          speech_texts=speech_texts)
    narration = concat_audio(parent_segments, folder)
    segments = expand_narration_visual_shots(parent_segments)
    data["tts_block_count"] = len(parent_segments)
    data["visual_shot_count"] = len(segments)
    allocator = allocate_visual_all(segments, source_clips, usable_end, folder, args.trim_head)
    if args.hierarchical_match:
        apply_hierarchical_takeover(segments, allocator, folder)
    timeline = build_timeline(source_clips, segments)

    if args.no_render:
        write_outputs(data, timeline, allocator, folder)
        print("--no-render：已生成 ★ 匹配报告.json / ★ 字幕.srt，跳过成片渲染", flush=True)
        return

    output = render_video(source, narration, timeline, folder, target_seconds,
                          args.include_source_audio, args.source_volume,
                          args.narration_source_volume)
    write_outputs(data, timeline, allocator, folder)
    print(f"完成：{output} ({probe_duration(output):.1f}s)")


if __name__ == "__main__":
    main()
