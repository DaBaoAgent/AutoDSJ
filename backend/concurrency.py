from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .media_tools import ffmpeg, ffprobe

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "concurrency_profile.json"
TEST_VIDEO = ROOT / "runtime" / "test_ffmpeg_concurrency.mp4"
RUNTIME_DIR = ROOT / "runtime"


def _ensure_test_video() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_VIDEO.exists():
        probe = subprocess.run([
            ffprobe(), "-v", "error", "-show_entries",
            "format=duration", "-of", "csv=p=0", str(TEST_VIDEO)
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            duration = float(probe.stdout.strip())
            if 29 <= duration <= 31:
                return TEST_VIDEO
        except (ValueError, subprocess.CalledProcessError):
            pass
    subprocess.run([
        ffmpeg(), "-y", "-f", "lavfi", "-i",
        "testsrc=size=1920x1080:rate=30:duration=30", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=30", "-shortest", "-c:v", "libx264",
        "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-b:a", "64k",
        str(TEST_VIDEO)
    ], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return TEST_VIDEO


def _benchmark(workers: int) -> float:
    video = _ensure_test_video()
    chunk_dir = RUNTIME_DIR / f"_concurrency_bench_{workers}"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    try:
        tasks = []
        for i in range(5):
            start_time = i * 2 + 1
            out_path = chunk_dir / f"clip_{i:02d}.mp4"
            tasks.append((i, start_time, out_path if workers == 1 else out_path))

        def _cut_one(clip_index: int, start_sec: float, out_file: Path) -> tuple[int, bool]:
            try:
                subprocess.run([
                    ffmpeg(), "-y", "-ss", str(start_sec), "-i", str(video),
                    "-t", "1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "28", "-an", str(out_file)
                ], check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
                return clip_index, True
            except subprocess.CalledProcessError:
                return clip_index, False

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_cut_one, idx, start, out)
                for idx, start, out in tasks
            ]
            for future in as_completed(futures):
                future.result()
        elapsed = time.perf_counter() - t0
        return elapsed
    finally:
        for f in chunk_dir.glob("clip_*.mp4"):
            f.unlink(missing_ok=True)
        try:
            chunk_dir.rmdir()
        except OSError:
            pass


def detect_optimal_concurrency() -> int:
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text("utf-8"))
            return int(data["optimal_workers"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    cpu_count = os.cpu_count() or 4
    candidates = [c for c in (2, 4, 6, 8) if c <= cpu_count]
    if not candidates:
        candidates = [max(1, cpu_count - 1)]

    best_workers = candidates[0]
    best_time = float("inf")

    for workers in candidates:
        try:
            elapsed = _benchmark(workers)
        except Exception:
            continue
        if elapsed < best_time:
            best_time = elapsed
            best_workers = workers

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps({"optimal_workers": best_workers, "elapsed_sec": round(best_time, 3)},
                   ensure_ascii=False, indent=2),
        "utf-8"
    )
    return best_workers


def get_concurrency() -> int:
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text("utf-8"))
            return int(data["optimal_workers"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    cpu_count = os.cpu_count() or 4
    return min(4, cpu_count - 1) if cpu_count > 1 else 1


def set_concurrency(workers: int) -> int:
    """手动固定并发数，写入 profile，后续 get_concurrency 直接复用（跳过基准）。"""
    workers = max(1, min(16, int(workers)))
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps({"optimal_workers": workers, "source": "manual"}, ensure_ascii=False, indent=2),
        "utf-8",
    )
    return workers
