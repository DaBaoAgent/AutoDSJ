from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .media_tools import ffmpeg, ffprobe
from .schemas import AppSettings


RESOLUTIONS = {
    "720P": (1280, 720),
    "1080P": (1920, 1080),
    "2K": (2560, 1440),
    "4K": (3840, 2160),
}


def _srt_seconds(value: str) -> float:
    hours, minutes, tail = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(tail)


def _srt_time(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def shift_timing_artifacts(folder: Path, padding_head: float) -> None:
    """Keep exported SRT/report timings aligned with the padded final video."""
    report_path = folder / "★ 匹配报告.json"
    report: dict = {}
    previous_padding = 0.0
    if report_path.exists():
        report = json.loads(report_path.read_text("utf-8"))
        previous_padding = float(report.get("output_padding_head", 0.0) or 0.0)
    delta = float(padding_head) - previous_padding
    if abs(delta) <= 1e-9:
        return

    subtitle_path = folder / "★ 字幕.srt"
    if subtitle_path.exists():
        content = subtitle_path.read_text("utf-8")

        def shift_match(match: re.Match) -> str:
            return (
                f"{_srt_time(_srt_seconds(match.group(1)) + delta)} --> "
                f"{_srt_time(_srt_seconds(match.group(2)) + delta)}"
            )

        shifted = re.sub(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
            shift_match,
            content,
        )
        subtitle_path.write_text(shifted, "utf-8")

    if report:
        for segment in report.get("segments", []):
            segment["output_start"] = round(float(segment.get("output_start", 0.0)) + delta, 6)
            segment["output_end"] = round(float(segment.get("output_end", 0.0)) + delta, 6)
        report["output_padding_head"] = float(padding_head)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")


def _duration(path: Path) -> float:
    result = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def render_final(settings: AppSettings, folder: Path, work_dir: Path) -> Path:
    source = folder / "★ 成片.mp4"
    if not source.exists():
        raise RuntimeError("缺少核心成片")
    work_dir.mkdir(parents=True, exist_ok=True)
    temp = work_dir / "final_clean.mp4"
    width, height = RESOLUTIONS[settings.video.resolution]
    head = float(settings.video.padding_head)
    tail = float(settings.video.padding_tail)
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    if head or tail:
        video_filter += (
            f",tpad=start_duration={head:.3f}:stop_duration={tail:.3f}:"
            "start_mode=clone:stop_mode=clone"
        )
    audio_filter = "anull"
    if head or tail:
        audio_filter = f"adelay={round(head * 1000)}:all=1,apad=pad_dur={tail:.3f}"
    output_duration = _duration(source) + head + tail
    command = [
        ffmpeg(), "-y", "-i", str(source),
        "-vf", video_filter, "-af", audio_filter,
        "-c:v", "libx264", "-preset", settings.video.preset,
        "-crf", str(settings.video.video_crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{output_duration:.3f}", "-movflags", "+faststart", str(temp),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    shutil.move(str(temp), str(source))
    return source


def run_postprocess(settings: AppSettings, folder: Path, work_dir: Path) -> dict:
    video = render_final(settings, folder, work_dir)
    shift_timing_artifacts(folder, float(settings.video.padding_head))
    return {"video": str(video)}
