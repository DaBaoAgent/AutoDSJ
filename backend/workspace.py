from __future__ import annotations

import shutil
from pathlib import Path


WORKSPACE_NAME = "_DY工作文件"
FINAL_KEEP_NAMES = {"★ 成片.mp4", "★ 发布信息.txt", "★ 剪映字幕导入.txt"}
SOURCE_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".m4v"}
SOURCE_SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
SCRIPT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".rtf", ".docx"}
GENERATED_TEXT_TOKENS = ("配音稿", "发布信息", "匹配报告", "交付清单", "剪映字幕")


def workspace_path(folder: Path) -> Path:
    return Path(folder).resolve() / WORKSPACE_NAME


def artifact_path(folder: Path, name: str) -> Path:
    root_path = Path(folder).resolve() / name
    if root_path.exists():
        return root_path
    return workspace_path(folder) / name


def _is_delivery_root_item(path: Path) -> bool:
    if path.name == WORKSPACE_NAME or path.name in FINAL_KEEP_NAMES:
        return True
    if path.name.startswith("_") or path.name.startswith("★"):
        return False
    suffix = path.suffix.lower()
    if suffix in SOURCE_VIDEO_EXTENSIONS or suffix in SOURCE_SUBTITLE_EXTENSIONS:
        return True
    if suffix in SCRIPT_EXTENSIONS:
        return not any(token in path.stem for token in GENERATED_TEXT_TOKENS)
    return False


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def organize_episode_folder(folder: Path) -> dict[str, Path]:
    """Leave only source/delivery files at episode root and archive all work products."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise RuntimeError(f"素材文件夹不存在：{root}")
    workspace = workspace_path(root)
    workspace.mkdir(exist_ok=True)
    moved: dict[str, Path] = {}
    for source in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if _is_delivery_root_item(source):
            continue
        target = workspace / source.name
        if target.exists():
            _remove(target)
        shutil.move(str(source), str(target))
        moved[source.name] = target
    return moved


def restore_episode_workspace(folder: Path) -> list[Path]:
    """Restore archived work products before another processing command runs."""
    root = Path(folder).resolve()
    workspace = workspace_path(root)
    if not workspace.is_dir():
        return []
    restored: list[Path] = []
    for source in sorted(workspace.iterdir(), key=lambda item: item.name.lower()):
        target = root / source.name
        if target.exists():
            # A root copy was created after archiving and is therefore authoritative.
            _remove(source)
            continue
        shutil.move(str(source), str(target))
        restored.append(target)
    try:
        workspace.rmdir()
    except OSError:
        pass
    return restored
