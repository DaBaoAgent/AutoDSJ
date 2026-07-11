from __future__ import annotations

import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _candidate_dirs() -> list[Path]:
    values: list[Path] = []
    for key in ("FFMPEG_HOME", "FFMPEG_DIR", "IMAGEIO_FFMPEG_EXE"):
        raw = os.environ.get(key)
        if not raw:
            continue
        path = Path(raw).expanduser()
        values.append(path.parent if path.suffix.lower() == ".exe" else path)
    values.extend([
        Path(r"D:\BaiduSyncdisk\2 @AI编程\@开源模型\ffmpeg-8.1.2-release-essentials\ffmpeg-8.1.2-essentials_build\bin"),
        ROOT / "tools" / "ffmpeg" / "bin",
        ROOT / "ffmpeg" / "bin",
        Path(r"D:\video-use"),
        Path(r"D:\video-use\bin"),
        Path(r"D:\ffmpeg\bin"),
        Path(r"C:\ffmpeg\bin"),
        Path(r"C:\Program Files\ffmpeg\bin"),
    ])
    return values


def media_tool(name: str) -> str:
    exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"
    explicit = os.environ.get(f"{name.upper()}_PATH")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)

    found = shutil.which(name) or shutil.which(exe_name)
    if found:
        return found

    for folder in _candidate_dirs():
        candidate = folder / exe_name
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        f"找不到 {name}。请安装 FFmpeg，并把 ffmpeg.exe / ffprobe.exe 所在目录加入 PATH，"
        "或设置 FFMPEG_DIR / FFMPEG_HOME / FFPROBE_PATH。"
    )


def ffmpeg() -> str:
    return media_tool("ffmpeg")


def ffprobe() -> str:
    return media_tool("ffprobe")


def gpt_sovits_python(engine: Path) -> Path:
    candidates = [
        engine / ".venv" / "Scripts" / "python.exe",
        engine / "runtime" / "python310" / "python.exe",
        engine / "runtime" / "python.exe",
        engine / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    import sys

    return Path(sys.executable)
