from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .media_tools import ffprobe
from .schemas import MaterialInfo


def _natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.stem.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def _looks_like_source(path: Path) -> bool:
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


def _probe_video(path: Path) -> dict:
    try:
        probe = subprocess.run(
            [
                ffprobe(),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-1200:]
        raise RuntimeError(f"ffprobe 读取视频失败：{detail}") from exc
    return json.loads(probe.stdout)


def detect_materials(folder_value: str, max_videos: int = 10) -> MaterialInfo:
    folder = Path(folder_value.strip().strip('"')).expanduser()
    if not folder.is_dir():
        raise ValueError(f"素材文件夹不存在：{folder}")

    max_videos = max(1, min(10, int(max_videos or 10)))
    all_videos = sorted(
        [*folder.glob("*.mp4"), *folder.glob("*.mkv"), *folder.glob("*.mov")],
        key=_natural_key,
    )
    all_subtitles = sorted([*folder.glob("*.srt"), *folder.glob("*.ass")], key=_natural_key)
    source_videos = [path for path in all_videos if _looks_like_source(path)] or all_videos
    source_subtitles = [path for path in all_subtitles if _looks_like_source(path)] or all_subtitles

    if not source_videos:
        raise ValueError("素材文件夹内没有 MP4/MKV/MOV 视频")
    if not source_subtitles:
        raise ValueError("素材文件夹内没有 SRT/ASS 字幕")

    selected_videos = source_videos[:max_videos]
    video = selected_videos[0]
    data = _probe_video(video)

    total_duration = 0.0
    for selected in selected_videos:
        selected_data = data if selected == video else _probe_video(selected)
        total_duration += float(selected_data["format"].get("duration", 0) or 0)

    vstream = next(x for x in data["streams"] if x.get("codec_type") == "video")
    astream = next((x for x in data["streams"] if x.get("codec_type") == "audio"), None)
    warnings: list[str] = []
    ignored_outputs = [path.name for path in [*all_videos, *all_subtitles] if not _looks_like_source(path)]
    if ignored_outputs:
        warnings.append(f"已忽略疑似成片/输出文件：{', '.join(ignored_outputs[:4])}")
    if len(source_videos) > max_videos:
        warnings.append(f"检测到 {len(source_videos)} 个原片，当前选择前 {max_videos} 个")
    elif len(source_videos) > 1:
        warnings.append(f"检测到 {len(source_videos)} 个原片，当前选择 {len(selected_videos)} 个")
    if len(source_subtitles) == 1:
        warnings.append("仅检测到一个字幕文件，将作为主要原片字幕使用")

    return MaterialInfo(
        folder=str(folder.resolve()),
        video_path=str(video.resolve()),
        video_paths=[str(x.resolve()) for x in selected_videos],
        subtitle_paths=[str(x.resolve()) for x in source_subtitles],
        duration=float(data["format"]["duration"]),
        total_duration=total_duration or float(data["format"]["duration"]),
        selected_video_count=len(selected_videos),
        total_video_count=len(source_videos),
        width=int(vstream["width"]),
        height=int(vstream["height"]),
        video_codec=vstream.get("codec_name", "unknown"),
        audio_codec=astream.get("codec_name") if astream else None,
        warnings=warnings,
    )
