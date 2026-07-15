#!/usr/bin/env python3
"""审「解说→画面」身份命中质量：跑完 `dy run [--no-render]` 后量化验收。

对每个**点名了角色**的解说分镜，判定匹配到的画面证据里是：
  命中(该角色出现) / 张冠李戴(出现别的主角) / 无角色标注(空镜·侧脸·背影)。
并打印置信度分布 + 身份注入覆盖率 + 疑似张冠李戴样本，供逐句排查。

用法（在项目根，用项目 .venv）：
    .venv/Scripts/python.exe <此脚本> "D:/自动剪辑/玫瑰的故事/玫瑰的故事 第4集"
不带参数则读 config/user_config.json 里保存的 material_folder。

依赖 backend.visual_matcher（复用管线自身的角色建组+命中判定，口径与匹配一致）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import backend.visual_matcher as vm

REPORT_NAME = "\u2605 \u5339\u914d\u62a5\u544a.json"   # ★ 匹配报告.json
VISUAL_NAME = "_source_visual_index.json"


def _folder() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1].strip().strip('"'))
    cfg = json.loads(Path("config/user_config.json").read_text("utf-8"))
    return Path(cfg["material_folder"])


def main() -> None:
    root = _folder()
    vi = json.loads((root / VISUAL_NAME).read_text("utf-8"))
    frames = vi.get("frames", [])
    vm.register_character_aliases(frames)          # 与匹配管线相同的角色建组
    print("角色组:", ["/".join(sorted(g)) for g in vm._CHAR_GROUPS])

    rep = json.loads((root / REPORT_NAME).read_text("utf-8"))
    segs = [s for s in rep["segments"] if s.get("row_type") == "narration"]
    ided = sum(1 for f in frames if f.get("identified"))
    print(f"解说分镜: {len(segs)} | 身份注入帧: {ided}/{len(frames)} "
          f"({100 * ided / max(1, len(frames)):.0f}%)")
    print("置信度分布:", dict(Counter(s.get("match_confidence") for s in segs)))

    named = hit = cross = none = 0
    misses = []
    for s in segs:
        q = s.get("visual_intent") or s.get("text") or ""
        ev = s.get("visual_match_evidence") or ""
        qh = vm._character_hits(q)
        if not qh:
            continue                                # 没点名角色的分镜不计入身份命中口径
        named += 1
        eh = vm._character_hits(ev)
        if qh.intersection(eh):
            hit += 1
        elif eh:
            cross += 1
            misses.append((s.get("segment_id"), s.get("match_confidence"), q[:26], ev[:44]))
        else:
            none += 1

    print(f"\n点名角色的分镜: {named}")
    if named:
        print(f"  OK 命中该角色      : {hit} ({100 * hit / named:.0f}%)")
        print(f"  XX 张冠李戴(别的主角): {cross} ({100 * cross / named:.0f}%)")
        print(f"  .. 无角色标注       : {none} ({100 * none / named:.0f}%)")
    if misses:
        print("\n疑似张冠李戴（逐条排查是否要加 _scene_map override）:")
        for sid, conf, q, ev in misses:
            print(f"  #{sid}[{conf}] 解说“{q}” -> 画面“{ev}”")


if __name__ == "__main__":
    main()
