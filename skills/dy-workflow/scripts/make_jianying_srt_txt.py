#!/usr/bin/env python3
"""
从 DY 文案（含「原片：」「解说：」分段标签）生成剪映可导入的字幕 txt。

规则（用户硬要求）：
  - 同时包含「原片」对白 + 「解说」文字
  - 删掉「原片：」「解说：」标签行（兼容全角/半角冒号、首尾空格）
  - 不要空行
  - 保持文案原格式：一句一行，顺序不变

用法：
  python make_jianying_srt_txt.py "<文案.txt 路径>" [输出路径]
  不给输出路径时，默认写到文案同目录的「★ 剪映字幕导入.txt」。
"""
import re
import sys
from pathlib import Path

LABEL_RE = re.compile(r"^\s*(原片|解说)\s*[:：]\s*(.*)$")


def convert(src_path: Path) -> list[str]:
    lines = src_path.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:              # 去空行
            continue
        match = LABEL_RE.match(s)
        if match:              # 去标签；兼容“解说：正文”写在同一行
            tail = match.group(2).strip()
            if tail:
                out.append(tail)
            continue
        out.append(s)
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"文案文件不存在: {src}")
        sys.exit(1)
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / "★ 剪映字幕导入.txt"

    out = convert(src)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✅ 写入 {dst}")
    print(f"   共 {len(out)} 行")


if __name__ == "__main__":
    main()
