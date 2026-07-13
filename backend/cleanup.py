from __future__ import annotations

import shutil
from pathlib import Path


REBUILDABLE_NAMES = {
    "_anchored_audio_concat.txt",
    "_anchored_muxed.mp4",
    "_anchored_silent.mp4",
    "_narration_manifest.json",
    "配音.wav",
    "配音稿.txt",
    "配音稿_朗读版.txt",
}
REBUILDABLE_GLOBS = (
    "_anchored_tts_*",
    "_gpt_sovits_jobs*",
    "_run*.log",
    "_render*.log",
)
REBUILDABLE_DIRS = {"_anchored_clips", "_diagnostic_frames"}


def cleanup_render_artifacts(folder: Path) -> tuple[list[Path], int]:
    """Delete disposable render products; preserve sources, maps, indexes and finals."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise RuntimeError(f"素材文件夹不存在：{root}")

    candidates: set[Path] = {root / name for name in REBUILDABLE_NAMES}
    candidates.update(root / name for name in REBUILDABLE_DIRS)
    for pattern in REBUILDABLE_GLOBS:
        candidates.update(root.glob(pattern))

    removed: list[Path] = []
    reclaimed = 0
    for path in sorted(candidates, key=lambda value: str(value).lower()):
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved.parent != root:
            raise RuntimeError(f"拒绝清理素材目录之外的路径：{resolved}")
        if resolved.is_dir():
            reclaimed += sum(item.stat().st_size for item in resolved.rglob("*") if item.is_file())
            shutil.rmtree(resolved)
        else:
            reclaimed += resolved.stat().st_size
            resolved.unlink()
        removed.append(resolved)
    return removed, reclaimed
