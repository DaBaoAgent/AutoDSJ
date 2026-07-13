from __future__ import annotations

import hashlib
import json
import re
import subprocess
from bisect import bisect_left
from pathlib import Path

from backend.media import detect_materials
from backend.media_tools import ffmpeg
from backend.vision_api import parse_srt

SHOT_SCHEMA = "v3-cpu-shot-index"
_PTS_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")


def parse_scene_times(output: str) -> list[float]:
    values: list[float] = []
    for match in _PTS_RE.finditer(output or ""):
        value = float(match.group(1))
        if not values or value - values[-1] >= 0.18:
            values.append(value)
    return values


def _fingerprint(video: Path) -> str:
    stat = video.stat()
    raw = f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{SHOT_SCHEMA}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def detect_shot_boundaries(video: Path, duration: float, *, threshold: float = 8.0) -> list[float]:
    command = [ffmpeg(), "-hide_banner", "-nostdin", "-i", str(video),
               "-vf", f"scale=320:-2:flags=fast_bilinear,scdet=t={threshold}:s=1,showinfo",
               "-an", "-sn", "-f", "null", "NUL"]
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=max(900, int(duration * 1.5)))
    if proc.returncode:
        raise RuntimeError(f"FFmpeg 镜头检测失败：{proc.stderr[-1600:]}")
    middle = [t for t in parse_scene_times(proc.stderr) if 0.20 < t < duration - 0.20]
    return [0.0, *middle, duration]


def _key_times(start: float, end: float, max_keys: int = 5) -> list[float]:
    duration = end - start
    if duration <= 0:
        return []
    margin = min(0.18, duration * 0.08)
    if duration < 1.2:
        ratios = (0.5,)
    elif duration < 3.0:
        ratios = (0.25, 0.65)
    elif duration < 8.0:
        ratios = (0.12, 0.38, 0.68, 0.90)
    else:
        ratios = (0.08, 0.27, 0.50, 0.73, 0.92)
    return [round(max(start + margin, min(end - margin, start + duration * ratio)), 3)
            for ratio in ratios[:max_keys]]


def _load_visual_frames(folder: Path) -> list[dict]:
    path = folder / "_source_visual_index.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text("utf-8"))
        return sorted(payload.get("frames") or payload.get("records") or [],
                      key=lambda item: float(item.get("time", 0)))
    except (OSError, ValueError, TypeError):
        return []


def _nearest_frames(frames: list[dict], times: list[float], tolerance: float = 5.0) -> list[dict]:
    if not frames:
        return []
    frame_times = [float(item.get("time", 0)) for item in frames]
    found, seen = [], set()
    for value in times:
        pos = bisect_left(frame_times, value)
        choices = [i for i in (pos - 1, pos) if 0 <= i < len(frames)]
        if not choices:
            continue
        index = min(choices, key=lambda i: abs(frame_times[i] - value))
        if abs(frame_times[index] - value) > tolerance:
            continue
        frame = frames[index]
        key = str(frame.get("frame_id") or frame_times[index])
        if key not in seen:
            found.append(frame)
            seen.add(key)
    return found


def build_shot_index(folder: Path, *, threshold: float = 8.0, force: bool = False) -> dict:
    folder = folder.resolve()
    media = detect_materials(str(folder), 1)
    video = Path(media.video_path)
    output = folder / "_source_shot_index.json"
    signature = _fingerprint(video)
    if output.exists() and not force:
        try:
            cached = json.loads(output.read_text("utf-8"))
            if cached.get("schema") == SHOT_SCHEMA and cached.get("signature") == signature:
                return cached
        except (OSError, ValueError):
            pass
    boundary_cache = folder / "_source_shot_boundaries.json"
    boundaries = None
    if boundary_cache.exists() and not force:
        try:
            cached_boundaries = json.loads(boundary_cache.read_text("utf-8"))
            if (cached_boundaries.get("signature") == signature
                    and float(cached_boundaries.get("threshold", -1)) == float(threshold)):
                boundaries = [float(value) for value in cached_boundaries.get("boundaries", [])]
        except (OSError, ValueError, TypeError):
            boundaries = None
    if not boundaries:
        boundaries = detect_shot_boundaries(video, media.duration, threshold=threshold)
        boundary_cache.write_text(json.dumps({"schema": SHOT_SCHEMA, "signature": signature,
                                  "threshold": threshold, "boundaries": boundaries},
                                  ensure_ascii=False, indent=2), "utf-8")
    visual_frames = _load_visual_frames(folder)
    subtitles = parse_srt(Path(media.subtitle_paths[0])) if media.subtitle_paths else []
    shots = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        if end - start < 0.12:
            continue
        keys = _key_times(start, end)
        evidence = _nearest_frames(visual_frames, keys)
        cues = [cue for cue in subtitles if cue.end >= start and cue.start <= end]
        shots.append({"shot_id": f"shot_{index:05d}", "start": round(start, 3),
                      "end": round(end, 3), "duration": round(end - start, 3),
                      "key_times": keys, "subtitle_text": " ".join(cue.text for cue in cues),
                      "subtitle_indices": [cue.idx for cue in cues],
                      "nearest_visual_frames": [
                          {key: frame.get(key) for key in ("frame_id", "time", "caption", "people",
                           "scene", "action", "props", "identified")} for frame in evidence]})
    payload = {"schema": SHOT_SCHEMA, "signature": signature, "video": video.name,
               "duration": media.duration, "threshold": threshold,
               "shot_count": len(shots), "shots": shots}
    temp = output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(output)
    return payload
