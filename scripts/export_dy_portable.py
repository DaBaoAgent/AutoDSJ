from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "dy-workflow"
DEFAULT_OUTPUT = Path(r"E:\Codex-DY全自动剪辑复用包")


def export_package(output: Path) -> dict:
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    skill_target = output / "dy-workflow"
    if skill_target.exists():
        shutil.rmtree(skill_target)
    shutil.copytree(SKILL, skill_target)

    bundle = output / "DaobaoAI-DY.bundle"
    if bundle.exists():
        bundle.unlink()
    subprocess.run(
        ["git", "bundle", "create", str(bundle), "--all"],
        cwd=ROOT, check=True,
    )
    installer = output / "安装到另一台电脑.ps1"
    shutil.copy2(SKILL / "assets" / "install_portable.ps1", installer)
    return {"output": output, "skill": skill_target, "bundle": bundle, "installer": installer}


def main() -> int:
    parser = argparse.ArgumentParser(description="导出不含密钥和素材的 DY Codex 跨电脑复用包")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = export_package(args.output)
    print(f"复用包：{result['output']}")
    print(f"技能：{result['skill']}")
    print(f"Git bundle：{result['bundle']}")
    print(f"安装脚本：{result['installer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
