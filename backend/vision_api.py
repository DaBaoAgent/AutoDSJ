from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .media_tools import ffmpeg, ffprobe


@dataclass
class SubtitleEntry:
    idx: int
    start: float
    end: float
    text: str


@dataclass
class FrameSample:
    time: float
    hash_value: int
    file: str
    path: str


def _run(command: list[str], timeout: int = 2400) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _probe_duration(path: Path) -> float:
    result = _run([
        ffprobe(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    return float((result.stdout or "0").strip() or 0)


def _format_time(value: float) -> str:
    milliseconds = round(max(0.0, float(value)) * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _subtitle_time(value: str) -> float:
    hours, minutes, rest = value.strip().replace(",", ".").split(":")
    if "." in rest:
        seconds, fraction = rest.split(".", 1)
    else:
        seconds, fraction = rest, "0"
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + float(f"0.{fraction}")


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def _clean_subtitle_text(value: str) -> str:
    value = value.replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", " ")
    value = re.sub(r"\{[^{}]*}", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_ass(path: Path) -> list[SubtitleEntry]:
    text = _read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    format_fields: list[str] = []
    entries: list[SubtitleEntry] = []
    in_events = False
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            format_fields = [part.strip().lower() for part in line.split(":", 1)[1].split(",")]
            continue
        if not line.lower().startswith("dialogue:"):
            continue
        payload = line.split(":", 1)[1].lstrip()
        if format_fields:
            fields = payload.split(",", max(0, len(format_fields) - 1))
            field_map = {name: fields[index].strip() for index, name in enumerate(format_fields) if index < len(fields)}
            start_raw = field_map.get("start", "")
            end_raw = field_map.get("end", "")
            body_raw = field_map.get("text", "")
        else:
            fields = payload.split(",", 9)
            if len(fields) < 10:
                continue
            start_raw, end_raw, body_raw = fields[1].strip(), fields[2].strip(), fields[9]
        try:
            start = _subtitle_time(start_raw)
            end = _subtitle_time(end_raw)
        except (ValueError, IndexError):
            continue
        body = _clean_subtitle_text(body_raw)
        if body and end > start:
            entries.append(SubtitleEntry(len(entries) + 1, start, end, body))
    return entries


def parse_srt(path: Path) -> list[SubtitleEntry]:
    if path.suffix.lower() == ".ass":
        entries = _parse_ass(path)
        if not entries:
            raise RuntimeError(f"无法解析字幕：{path}")
        return entries

    text = _read_text(path).replace("\r\n", "\n")
    pattern = re.compile(
        r"(?:^|\n)(\d+)\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,.]\d+)\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d+)[^\n]*\n"
        r"(.*?)(?=\n\s*\n|\Z)", re.S,
    )
    entries: list[SubtitleEntry] = []
    for match in pattern.finditer(text):
        body = _clean_subtitle_text(match.group(4))
        if body:
            entries.append(SubtitleEntry(
                int(match.group(1)), _subtitle_time(match.group(2)), _subtitle_time(match.group(3)), body,
            ))
    if not entries:
        raise RuntimeError(f"无法解析字幕：{path}")
    return entries


def _subtitle_json(entries: Iterable[SubtitleEntry]) -> list[dict]:
    return [{
        "idx": item.idx,
        "start": round(item.start, 3),
        "end": round(item.end, 3),
        "start_text": _format_time(item.start),
        "end_text": _format_time(item.end),
        "duration": round(item.end - item.start, 3),
        "text": item.text,
    } for item in entries]


def _extract_frames(video: Path, out_dir: Path, interval: float, prefix: str) -> list[FrameSample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{prefix}_%06d.jpg"
    video_filter = (
        f"fps=1/{interval:.3f},scale=480:270:force_original_aspect_ratio=decrease,"
        "pad=480:270:(ow-iw)/2:(oh-ih)/2"
    )
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", video_filter, "-q:v", "5", str(pattern),
    ])
    return [
        FrameSample(round(index * interval, 3), 0, image.name, str(image))
        for index, image in enumerate(sorted(out_dir.glob(f"{prefix}_*.jpg")))
    ]


def _clean_model_json(text: str) -> object:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    candidates = [
        text,
        re.sub(r"}\s*{", "},{", text),
        re.sub(r",\s*([}\]])", r"\1", re.sub(r"}\s*{", "},{", text)),
    ]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    for left_token, right_token in (("{", "}"), ("[", "]")):
        left = text.find(left_token)
        right = text.rfind(right_token)
        if left >= 0 and right > left:
            return json.loads(text[left:right + 1])
    raise RuntimeError("视觉模型没有返回合法 JSON")


def _vision_prompt(frame_count: int) -> str:
    return (
        f"你是电视剧短视频剪辑画面分析师。请逐张识别下面 {frame_count} 张视频帧。"
        "准确描述可见人物、外观线索、地点、正在发生的动作、情绪和镜头景别。"
        "如果画面或字幕能确认人物姓名，请写姓名；不能确认时只写可见外观，不要猜测。"
        "请只返回 JSON，不要解释，格式为："
        "{\"frames\":[{\"frame_id\":\"输入ID\",\"caption\":\"画面描述\","
        "\"people\":\"人物及外观\",\"scene\":\"地点\",\"action\":\"动作\","
        "\"emotion\":\"情绪\",\"shot_scale\":\"景别\"}]}"
    )


def _dashscope_compatible_url() -> str:
    base = (
        os.environ.get("DABAOAI_DASHSCOPE_COMPATIBLE_BASE_URL")
        or os.environ.get("DASHSCOPE_COMPATIBLE_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _parse_vision_content(content: str, batch: list[dict]) -> list[dict]:
    try:
        parsed = _clean_model_json(content)
        frames = parsed.get("frames", []) if isinstance(parsed, dict) else parsed
    except Exception:
        frames = []
    if not isinstance(frames, list):
        frames = []

    by_id = {}
    for raw in frames:
        item = raw if isinstance(raw, dict) else {"caption": str(raw)}
        frame_id = str(item.get("frame_id") or "").strip()
        if frame_id:
            by_id[frame_id] = item

    output = []
    for index, source in enumerate(batch):
        source_id = str(source["frame_id"])
        item = by_id.get(source_id)
        if item is None and index < len(frames):
            raw = frames[index]
            item = raw if isinstance(raw, dict) else {"caption": str(raw)}
        if item is None:
            item = {"caption": content[:500]}
        caption = str(item.get("caption") or item.get("visual_caption") or item.get("description") or "").strip()
        if not caption:
            caption = " ".join(str(item.get(key, "")).strip() for key in ("people", "scene", "action", "emotion") if item.get(key)).strip()
        item["caption"] = caption or content[:500]
        item.setdefault("frame_id", source_id)
        item["video_role"] = source["video_role"]
        item["source_index"] = source.get("source_index")
        item["source_file"] = source.get("source_file")
        item["time"] = source["time"]
        item["time_text"] = source["time_text"]
        output.append(item)
    return output


def _call_bailian_vision(api_key: str, model: str, api_url: str,
                         batch: list[dict], timeout: int = 240) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": _vision_prompt(len(batch))}]
    for item in batch:
        content.append({
            "type": "text",
            "text": f"frame_id={item['frame_id']}; time={item['time_text']}",
        })
        image_data = b64encode(Path(item["image_path"]).read_bytes()).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + image_data},
        })
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.05,
        "max_tokens": 8000,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url or _dashscope_compatible_url(), data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    return _parse_vision_content(body["choices"][0]["message"]["content"], batch)


def _call_siliconflow_vision(api_key: str, model: str, api_url: str,
                             batch: list[dict], timeout: int = 240) -> list[dict]:
    return _call_bailian_vision(api_key, model, api_url, batch, timeout)
