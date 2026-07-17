"""Install the repository's canonical AutoDSJ skill into supported agent roots."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from .sync_autodsj_skill import compare_skill, is_in_sync, sync_skill
except ImportError:  # Running as `python scripts/install_autodsj_skill.py`.
    from sync_autodsj_skill import compare_skill, is_in_sync, sync_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "skills" / "autodsj"
SKILL_NAME = "autodsj"


def _home() -> Path:
    return Path.home()


def default_target(agent: str) -> Path:
    home = _home()
    if os.name == "nt":
        config_root = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    else:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    targets = {
        "claude": home / ".claude" / "skills" / SKILL_NAME,
        "codex": Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills" / SKILL_NAME,
        "hermes": home / ".hermes" / "skills" / SKILL_NAME,
        "opencode": config_root / "opencode" / "skills" / SKILL_NAME,
        "openclaw": home / ".openclaw" / "skills" / SKILL_NAME,
        "shared": home / ".agents" / "skills" / SKILL_NAME,
        "legacy-hermes": local_app_data / "hermes" / "skills" / "media" / SKILL_NAME,
    }
    return targets[agent]


def install(agent: str, target: Path, check: bool) -> int:
    target = target.expanduser().resolve()
    if check:
        diff = compare_skill(SOURCE, target)
        if is_in_sync(diff):
            print(f"{agent}: synchronized: {target}")
            return 0
        print(f"{agent}: out of sync: {target}")
        for label, values in diff.items():
            if values:
                print(f"  {label}: {', '.join(values)}")
        return 1

    if target.name != SKILL_NAME:
        raise ValueError(f"target must end with {SKILL_NAME}: {target}")
    diff = sync_skill(SOURCE, target)
    if not is_in_sync(diff):
        raise RuntimeError(f"failed to synchronize {agent}: {diff}")
    print(f"{agent}: installed: {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or verify the AutoDSJ skill for AI coding agents.")
    parser.add_argument(
        "--agent",
        choices=("claude", "codex", "hermes", "opencode", "openclaw", "shared", "legacy-hermes", "all"),
        default="all",
    )
    parser.add_argument("--target", type=Path, help="Override the target skill directory (only with one agent).")
    parser.add_argument("--check", action="store_true", help="Only report drift; do not write files.")
    args = parser.parse_args()

    agents = ("claude", "codex", "hermes", "opencode", "openclaw", "shared") if args.agent == "all" else (args.agent,)
    if args.target and len(agents) != 1:
        parser.error("--target requires exactly one --agent")
    return max(install(agent, args.target or default_target(agent), args.check) for agent in agents)


if __name__ == "__main__":
    raise SystemExit(main())
