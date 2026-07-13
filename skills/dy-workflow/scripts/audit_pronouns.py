#!/usr/bin/env python3
"""
文案「他/她」指代审核辅助——抽取所有含单字人称代词「他」「她」的行 + 上下文，
供 agent 逐句对照角色性别判断是否标错（male→她 / female→他 = 错）。

本脚本只做**抽取**，不做判断：指代消解需要结合剧情/知识库，由 agent 推理。
自动过滤「其他/其它」这类非代词的假阳性；「他们/她们」保留（复数也可能标错）。

用法：
  python audit_pronouns.py "<文案.txt 路径>"
输出：每个代词行打印 [行号] 本行 | 上一行 / 下一行，方便判断指代对象。
"""
import re
import sys
from pathlib import Path

# 「其他」「其它」里的「他/它」不是人称代词，先挖掉再判断
FALSE_POS = re.compile(r"其[他它]")


def find_pronoun_lines(src: Path):
    lines = src.read_text(encoding="utf-8").splitlines()
    hits = []
    for i, ln in enumerate(lines):
        probe = FALSE_POS.sub("", ln)  # 去掉「其他/其它」再探测
        if "他" in probe or "她" in probe:
            prev = lines[i - 1].strip() if i > 0 else ""
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            hits.append((i + 1, ln.strip(), prev, nxt))
    return hits


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"文案文件不存在: {src}")
        sys.exit(1)

    hits = find_pronoun_lines(src)
    print(f"共 {len(hits)} 行含人称代词「他/她」，逐句核对指代性别：\n")
    for lineno, text, prev, nxt in hits:
        print(f"[{lineno:3d}] {text}")
        print(f"      上文: {prev}")
        print(f"      下文: {nxt}")
    print(f"\n审核要点：male 角色→写「他」，female 角色→写「她」。")
    print(f"玫瑰的故事性别表：黄亦玫/玫瑰=女 白晓荷=女 姜雪琼=女 韩鹦=女 苏更生=女；")
    print(f"                  庄国栋=男 黄振华(哥)=男 方协文=男 傅家明=男 何西=男 滕先生=男。")


if __name__ == "__main__":
    main()
