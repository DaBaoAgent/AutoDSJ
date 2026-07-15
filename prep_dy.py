#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DaobaoAI-DY 前置脚本 —— 每次跑管线前运行，确保 _DY工作文件/ 干净有最新索引"""

import os, sys, json, shutil
from pathlib import Path

WORKSPACE = "_DY工作文件"
DY_INDEX_FILES = [
    "_source_shot_index.json", "_source_shot_boundaries.json",
    "_source_event_index.json", "_source_visual_index.json",
    "_source_subtitle_index.json", "_source_ad_intervals.json",
    "_source_clip_candidates.json", "_source_voice_index.json",
    "_selective_visual_plan.json", "_scene_map.json",
    "_drama_script_table.json", "_narration_manifest.json",
]
DY_REPORT_FILES = [
    "★ 匹配报告.json", "★ 分层接管预演报告.json",
    "★ 新旧匹配并排对比.json", "★ 字幕.srt",
]

def main(folder: str):
    root = Path(folder).resolve()
    ws = root / WORKSPACE
    ws.mkdir(parents=True, exist_ok=True)

    actions = []

    # 1. 搬所有 _*.json 索引文件
    for fname in DY_INDEX_FILES:
        src = root / fname
        dst = ws / fname
        if src.exists():
            shutil.copy2(src, dst)
            actions.append(f"  cp {fname} → _DY工作文件/")

    # 2. 搬报告文件（有就搬）
    for fname in DY_REPORT_FILES:
        src = root / fname
        dst = ws / fname
        if src.exists():
            shutil.copy2(src, dst)
            actions.append(f"  cp {fname} → _DY工作文件/")

    # 3. 删根目录散落的临时文件（保持整洁）
    for fname in DY_INDEX_FILES + DY_REPORT_FILES:
        src = root / fname
        if src.exists():
            src.unlink()
            actions.append(f"  rm root/{fname}")

    print(f"✅ prep_dy: {len(actions)} 操作")
    for a in actions:
        print(a)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python prep_dy.py <集数文件夹>")
        sys.exit(1)
    main(sys.argv[1])
