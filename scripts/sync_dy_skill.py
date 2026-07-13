from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


SKILL_NAME = "dy-workflow"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "skills" / SKILL_NAME
DEFAULT_TARGET = (
    Path.home() / "AppData" / "Local" / "hermes" / "skills" / "media" / SKILL_NAME
)
IGNORED_DIRS = {".git", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _validate_skill_path(path: Path, *, source: bool) -> Path:
    path = path.expanduser().resolve()
    if path.name != SKILL_NAME:
        raise ValueError(f"技能目录名必须是 {SKILL_NAME}: {path}")
    if source and not (path / "SKILL.md").is_file():
        raise ValueError(f"技能源缺少 SKILL.md: {path}")
    return path


def _iter_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_file() and path.suffix.lower() not in IGNORED_SUFFIXES:
            yield path, relative


def file_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path, relative in _iter_files(root):
        manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def tree_digest(manifest: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_hash in sorted(manifest.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def compare_skill(source: Path, target: Path) -> dict[str, list[str]]:
    source_manifest = file_manifest(source)
    target_manifest = file_manifest(target)
    source_names, target_names = set(source_manifest), set(target_manifest)
    return {
        "missing": sorted(source_names - target_names),
        "extra": sorted(target_names - source_names),
        "changed": sorted(
            name for name in source_names & target_names
            if source_manifest[name] != target_manifest[name]
        ),
    }


def is_in_sync(diff: dict[str, list[str]]) -> bool:
    return not any(diff.values())


def sync_skill(source: Path, target: Path) -> dict[str, list[str]]:
    source = _validate_skill_path(source, source=True)
    target = _validate_skill_path(target, source=False)
    target.mkdir(parents=True, exist_ok=True)

    source_manifest = file_manifest(source)
    target_manifest = file_manifest(target)

    for relative in sorted(set(target_manifest) - set(source_manifest)):
        destination = target / Path(relative)
        destination.unlink()

    for source_file, relative in _iter_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)

    for directory in sorted(
        (path for path in target.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if directory.name in IGNORED_DIRS:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass

    return compare_skill(source, target)


def _print_diff(diff: dict[str, list[str]]) -> None:
    for label in ("missing", "extra", "changed"):
        values = diff[label]
        if values:
            print(f"{label}: {len(values)}")
            for value in values:
                print(f"  {value}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将 Git 中的 DY 技能源单向同步到 Hermes，并按 SHA-256 校验。"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true", help="镜像技能源到 Hermes 目录")
    mode.add_argument("--check", action="store_true", help="只检查两边是否完全一致")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()

    source = _validate_skill_path(args.source, source=True)
    target = _validate_skill_path(args.target, source=False)
    diff = sync_skill(source, target) if args.sync else compare_skill(source, target)
    if not is_in_sync(diff):
        print("DY 技能未同步。")
        _print_diff(diff)
        return 1

    manifest = file_manifest(source)
    print(
        f"DY 技能已同步：{len(manifest)} 个文件，tree_sha256={tree_digest(manifest)}"
    )
    print(f"source={source}")
    print(f"target={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
