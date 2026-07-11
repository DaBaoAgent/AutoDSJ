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
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
import wave

from backend.concurrency import get_concurrency
from backend.ad_filter import detect_ad_intervals
from backend.media_tools import ffmpeg, ffprobe, gpt_sovits_python
from backend.qwen_voice import (
    DEFAULT_QWEN_CLONE_MODEL,
    DEFAULT_QWEN_REFERENCE_AUDIO,
    DEFAULT_QWEN_REFERENCE_TEXT_PATH,
    ensure_bailian_clone_voice,
    is_qwen_realtime_model,
    read_reference_text,
    synthesize_bailian_http_to_file,
)
from backend.visual_matcher import VisualIntervalAllocator, load_visual_frames, split_visual_clauses

ROOT = Path(__file__).resolve().parent
def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


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


def write_plain_script(data: dict, output: Path) -> None:
    output.write_text("\n".join(x["text"] for x in data["segments"]), "utf-8")


def synthesize_cosyvoice(segments: list[NarrationSegment], folder: Path,
                         api_key: str, model: str, voice: str, rate: float,
                         speech_texts: dict[int, str] | None = None) -> None:
    force_ipv4()
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
    except ImportError as exc:
        raise RuntimeError("缺少 dashscope；请先 pip install dashscope") from exc
    dashscope.api_key = api_key
    seg_dir = folder / "_anchored_tts"
    seg_dir.mkdir(exist_ok=True)
    for i, segment in enumerate(segments, 1):
        tts_text = (speech_texts or {}).get(segment.segment_id, segment.text)
        digest = hashlib.sha1(tts_text.encode("utf-8")).hexdigest()[:10]
        target = seg_dir / f"tts_{i:04d}_{digest}.mp3"
        if not target.exists() or target.stat().st_size < 1000:
            syn = SpeechSynthesizer(model=model, voice=voice,
                                    format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
                                    speech_rate=rate)
            target.write_bytes(syn.call(tts_text))
        segment.audio_file = str(target)
        segment.audio_duration = probe_duration(target)
        print(f"  TTS {i}/{len(segments)} {segment.audio_duration:.2f}s")


def synthesize_gpt_sovits(segments: list[NarrationSegment], folder: Path, engine: Path,
                          reference: Path, prompt_text: str, rate: float,
                          seed: int = 20260711,
                          text_split_method: str = "cut0",
                          temperature: float = 0.75,
                          top_p: float = 0.9,
                          top_k: int = 10,
                          repetition_penalty: float = 1.3,
                          polish: bool = False,
                          speech_texts: dict[int, str] | None = None) -> None:
    if not engine.exists():
        raise RuntimeError(f"GPT-SoVITS 引擎不存在: {engine}")
    jobs = folder / "_gpt_sovits_jobs.json"
    device = os.environ.get("DABAOAI_GPT_SOVITS_DEVICE", "auto").strip().lower() or "auto"
    voice_digest = hashlib.sha1(
        (
            f"{reference.resolve()}|{prompt_text}|{rate:.3f}|{device}|{seed}|"
            f"{text_split_method}|{temperature:.3f}|{top_p:.3f}|{top_k}|"
            f"{repetition_penalty:.3f}|polish={polish}"
        ).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()[:10]
    seg_dir = folder / f"_anchored_tts_gpt_sovits_{voice_digest}"
    seg_dir.mkdir(exist_ok=True)
    items = []
    targets = []
    for i, segment in enumerate(segments, 1):
        tts_text = (speech_texts or {}).get(segment.segment_id, segment.text)
        digest = hashlib.sha1(tts_text.encode("utf-8")).hexdigest()[:10]
        filename = f"tts_{i:04d}_{digest}.wav"
        items.append({"text": tts_text, "filename": filename})
        targets.append(seg_dir / filename)
    chunk_size = _env_int("DABAOAI_GPT_SOVITS_CHUNK_SIZE", 16, 1, 64)
    chunk_cooldown = _env_float("DABAOAI_GPT_SOVITS_CHUNK_COOLDOWN_SECONDS", 8.0, 0.0, 60.0)
    fallback_to_cpu = os.environ.get("DABAOAI_GPT_SOVITS_FALLBACK_CPU", "1").strip() != "0"
    payload = {
        "engine": str(engine), "reference": str(reference), "prompt_text": prompt_text,
        "output_dir": str(seg_dir), "items": items, "speed": rate, "device": device,
        "seed": int(seed), "text_split_method": text_split_method,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "top_k": int(top_k),
        "repetition_penalty": float(repetition_penalty),
        "max_workers": 1,
        "cpu_threads": _env_int("DABAOAI_GPT_SOVITS_CPU_THREADS", 2, 1, 4),
        "cooldown_seconds": _env_float("DABAOAI_GPT_SOVITS_COOLDOWN_SECONDS", 0.8, 0.0, 5.0),
        "clear_cuda_cache": True,
        "chunk_size": chunk_size,
        "chunk_cooldown_seconds": chunk_cooldown,
    }
    jobs.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    runner = ROOT / "gpt_sovits_batch.py"
    local_python = gpt_sovits_python(engine)
    if not local_python.exists():
        raise RuntimeError(f"GPT-SoVITS 独立运行环境不存在: {local_python}")
    print(
        f"  GPT-SoVITS 整段稳定模式：device={device}, seed={seed}, "
        f"split={text_split_method}, temp={temperature:.2f}, top_p={top_p:.2f}, "
        f"top_k={top_k}, repeat={repetition_penalty:.2f}, "
        f"每批{chunk_size}段, 批间隔{chunk_cooldown:.1f}s"
    )
    for chunk_start in range(0, len(items), chunk_size):
        chunk_items = items[chunk_start:chunk_start + chunk_size]
        chunk_targets = targets[chunk_start:chunk_start + chunk_size]
        chunk_index = chunk_start // chunk_size + 1
        chunk_count = math.ceil(len(items) / chunk_size)
        if all(target.exists() and target.stat().st_size > 1000 for target in chunk_targets):
            print(f"  GPT-SoVITS 分批 {chunk_index}/{chunk_count} 已存在，跳过")
            continue

        part_payload = dict(payload)
        part_payload["items"] = chunk_items
        part_payload["start_index"] = chunk_start + 1
        part_payload["total_items"] = len(items)
        part_jobs = folder / f"_gpt_sovits_jobs_part_{chunk_index:03d}.json"
        part_jobs.write_text(json.dumps(part_payload, ensure_ascii=False, indent=2), "utf-8")
        try:
            run([str(local_python), str(runner), str(part_jobs)], timeout=12 * 3600, capture=False)
        except subprocess.CalledProcessError:
            if device in ("auto", "cuda", "gpu") and fallback_to_cpu:
                print(f"  [WARN] GPT-SoVITS CUDA 分批 {chunk_index}/{chunk_count} 失败，改用 CPU 重试该批")
                part_payload["device"] = "cpu"
                part_jobs.write_text(json.dumps(part_payload, ensure_ascii=False, indent=2), "utf-8")
                run([str(local_python), str(runner), str(part_jobs)], timeout=12 * 3600, capture=False)
            else:
                raise
        if chunk_cooldown > 0 and chunk_start + chunk_size < len(items):
            time.sleep(chunk_cooldown)
    for segment, target in zip(segments, targets):
        if not target.exists():
            raise RuntimeError(f"GPT-SoVITS 未生成 {target.name}")
        if polish:
            polished = target.with_stem(target.stem + "_polished")
            run(["ffmpeg", "-y", "-i", str(target),
                 "-af", "highpass=f=80,equalizer=f=3000:t=q:w=1:g=2,"
                        "compand=attacks=0.005:decays=0.05:points=-80/-80|-30/-10|0/-3:gain=2,"
                        "loudnorm=I=-19:TP=-1.5:LRA=7",
                 "-ar", "48000", "-ac", "1", str(polished)], timeout=300)
            target.unlink()
            polished.rename(target)
        segment.audio_file = str(target)
        segment.audio_duration = probe_duration(target)


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
        voice, _, _ = ensure_bailian_clone_voice(
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
            target = seg_dir / f"tts_{index:04d}_{digest}.wav"
            if not target.exists() or target.stat().st_size < 1000:
                synthesize_bailian_http_to_file(api_key, model, voice, tts_text, target)
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
    volume = max(0, min(100, int(volume)))
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


def expand_narration_visual_shots(parents: list[NarrationSegment]) -> list[NarrationSegment]:
    """Split full-block TTS into visual clauses without synthesising the voice again."""
    children: list[NarrationSegment] = []
    for parent in parents:
        clauses = split_visual_clauses(parent.text) or [parent.text]
        pauses = _silence_boundaries(Path(parent.audio_file), parent.audio_duration)
        ranges = _clause_audio_ranges(clauses, parent.audio_duration, pauses)
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


def allocate_visual_all(segments: list[NarrationSegment], source_clips: list[NarrationSegment],
                        video_duration: float, folder: Path,
                        usable_start: float = 0.0) -> VisualIntervalAllocator:
    ad_intervals = detect_ad_intervals(folder)
    allocator = VisualIntervalAllocator(
        video_duration, load_visual_frames(folder), usable_start=usable_start,
        blocked_intervals=ad_intervals,
    )
    if ad_intervals:
        print(f"已启用插片广告禁区 {len(ad_intervals)} 段，原片与解说画面均禁止使用", flush=True)
    for clip in source_clips:
        allocator.reserve(clip.clip_start, clip.clip_end, f"原片行{clip.script_row_id}")

    group_cursor: dict[int, float] = {}
    for segment in sorted(segments, key=lambda item: (item.script_row_id, item.shot_index)):
        chronological_start = group_cursor.get(segment.script_row_id, segment.source_start)
        start, end, score, evidence = allocator.allocate(
            segment.visual_intent or segment.text,
            segment.audio_duration,
            segment.source_start,
            segment.source_end,
            f"解说行{segment.script_row_id}-镜头{segment.shot_index}",
            chronological_start=chronological_start,
        )
        segment.clip_start = start
        segment.clip_end = end
        segment.visual_match_score = round(score, 4)
        segment.visual_match_evidence = evidence
        segment.match_confidence = "A" if score >= 0.42 else ("B" if score >= 0.26 else "C")
        group_cursor[segment.script_row_id] = end + allocator.guard
    return allocator


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


def render_video(source: Path, narration: Path | None, segments: list[NarrationSegment], folder: Path,
                 target_seconds: float, include_source_audio: bool = False,
                 source_volume: float = 1.0,
                 narration_source_volume: float = 0.0) -> Path:
    clip_dir = folder / "_anchored_clips"
    if clip_dir.exists():
        shutil.rmtree(clip_dir)
    clip_dir.mkdir()

    def _cut_clip(index: int, segment: NarrationSegment) -> None:
        clip = clip_dir / f"clip_{index:04d}.mp4"
        vf = "scale=1920:1080:force_original_aspect_ratio=decrease," \
             "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25"
        if segment.row_type == "source_clip" and include_source_audio:
            cmd = ["ffmpeg", "-y", "-ss", f"{segment.clip_start:.3f}", "-i", str(source),
                   "-t", f"{segment.audio_duration:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                   "-vf", vf, "-af", f"volume={source_volume:.4f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                   str(clip)]
        else:
            if not segment.audio_file:
                raise RuntimeError(f"第 {segment.segment_id} 句缺少配音文件")
            if narration_source_volume > 0:
                mix = (
                    f"[0:a:0]volume={narration_source_volume:.4f}[srca];"
                    "[1:a:0]volume=1.0[voice];"
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
                       "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", "volume=1.0",
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
    parser.add_argument("--tts-backend", choices=["gpt-sovits", "cosyvoice", "qwen-clone"],
                        default="gpt-sovits")
    parser.add_argument("--gpt-sovits", type=Path, default=Path(r"D:\GPT-SoVITS"))
    parser.add_argument("--reference", type=Path,
                        default=Path(r"D:\BaiduSyncdisk\18 艾伦全自动解说\克隆音色\yatou2.wav"))
    parser.add_argument(
        "--prompt-text",
        default="",
        help="必须与参考音频逐字一致",
    )
    parser.add_argument("--qwen-voice", default="")
    parser.add_argument("--qwen-model", default="")
    parser.add_argument("--qwen-reference-audio", default=DEFAULT_QWEN_REFERENCE_AUDIO)
    parser.add_argument("--qwen-reference-text-path", default=DEFAULT_QWEN_REFERENCE_TEXT_PATH)
    parser.add_argument("--qwen-volume", type=int, default=55)
    parser.add_argument("--qwen-pitch", type=float, default=1.0)
    parser.add_argument("--speech-rate", type=float, default=1.0)
    parser.add_argument("--gpt-sovits-seed", type=int, default=20260711)
    parser.add_argument("--gpt-sovits-text-split-method", default="cut0")
    parser.add_argument("--gpt-sovits-temperature", type=float, default=0.75)
    parser.add_argument("--gpt-sovits-top-p", type=float, default=0.9)
    parser.add_argument("--gpt-sovits-top-k", type=int, default=10)
    parser.add_argument("--gpt-sovits-repetition-penalty", type=float, default=1.3)
    parser.add_argument("--trim-head", type=float, default=6.0)
    parser.add_argument("--trim-tail", type=float, default=15.0)
    parser.add_argument("--include-source-audio", action="store_true")
    parser.add_argument("--source-volume", type=float, default=1.0)
    parser.add_argument("--narration-source-volume", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--polish", action="store_true")
    args = parser.parse_args()

    folder = args.folder.resolve()
    source, _, _ = discover(folder)
    duration = probe_duration(source)
    target_seconds = args.target_seconds if args.target_seconds is not None else duration * args.ratio
    target_seconds = max(30.0, min(target_seconds, duration))
    print(f"原片 {duration:.1f}s，目标 {target_seconds:.1f}s ({args.ratio:.0%})")

    usable_end = duration - args.trim_tail
    manual_script = is_manual_script_table(folder)
    if not manual_script:
        raise RuntimeError("只支持用户上传的“原片/解说”手写文案，请先点击生成脚本表")
    env = {**load_env(ROOT / ".env"), **os.environ}
    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    source_clips = load_script_table_source_clips(
        folder, args.trim_head, usable_end, 20.0
    ) if args.include_source_audio else []
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
    if args.tts_backend == "gpt-sovits":
        synthesize_gpt_sovits(parent_segments, folder, args.gpt_sovits, args.reference,
                              args.prompt_text, args.speech_rate, seed=args.gpt_sovits_seed,
                              text_split_method=args.gpt_sovits_text_split_method,
                              temperature=args.gpt_sovits_temperature,
                              top_p=args.gpt_sovits_top_p,
                              top_k=args.gpt_sovits_top_k,
                              repetition_penalty=args.gpt_sovits_repetition_penalty,
                              polish=args.polish,
                              speech_texts=speech_texts)
    elif args.tts_backend == "cosyvoice":
        cosy = config.get("cosyvoice", {})
        synthesize_cosyvoice(parent_segments, folder, env.get("DASHSCOPE_API_KEY", ""),
                             cosy.get("model", "cosyvoice-v3.5-plus"), cosy.get("voice_id", ""),
                             args.speech_rate, speech_texts=speech_texts)
    else:
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
    timeline = build_timeline(source_clips, segments)

    output = render_video(source, narration, timeline, folder, target_seconds,
                          args.include_source_audio, args.source_volume,
                          args.narration_source_volume)
    write_outputs(data, timeline, allocator, folder)
    print(f"完成：{output} ({probe_duration(output):.1f}s)")


if __name__ == "__main__":
    main()
