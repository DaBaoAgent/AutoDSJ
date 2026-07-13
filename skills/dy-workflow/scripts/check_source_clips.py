#!/usr/bin/env python3
"""诊断 DY 脚本表 _drama_script_table.json 的 source_clip 区间。

渲染报「素材区间重复或命中广告禁区：X-Y (原片行N)」时先跑它定位：
列出所有原片(source_clip)区间、检出重叠、检出多行塌缩到同一时间戳。

用法:
    python check_source_clips.py "<素材文件夹路径>"

无重叠 => 渲染的 source_clip 分配不会崩；仍崩需查 narration 分配或广告禁区。
"""
import json
import sys
from pathlib import Path

GUARD = 0.18  # 与 VisualIntervalAllocator.guard 一致


def main() -> int:
    if len(sys.argv) < 2:
        print('用法: python check_source_clips.py "<素材文件夹>"')
        return 1
    folder = Path(sys.argv[1].strip().strip('"'))
    table = folder / "_drama_script_table.json"
    if not table.exists():
        print(f"找不到脚本表: {table}  (先跑 dy.py script 生成)")
        return 1
    data = json.loads(table.read_text("utf-8"))
    clips = [
        (r.get("row_id"), float(r["source_start"]), float(r["source_end"]),
         r.get("insert_role_label", ""))
        for r in data.get("rows", [])
        if r.get("row_type") == "source_clip"
    ]
    print(f"script_source = {data.get('script_source')}  ·  source_clip 段数 = {len(clips)}")
    if not clips:
        print("没有 source_clip 行——纯解说文案？")
        return 0

    ordered = sorted(clips, key=lambda x: x[1])
    prev = None
    overlaps = 0
    seen: dict[tuple, int] = {}
    for rid, start, end, label in ordered:
        flag = ""
        if prev and start < prev[2] - GUARD:
            flag = f"  <<< 与 row{prev[0]} 重叠!"
            overlaps += 1
        key = (round(start, 1), round(end, 1))
        seen[key] = seen.get(key, 0) + 1
        print(f"row{rid:<3} {start:9.3f} - {end:9.3f}  {label}{flag}")
        prev = (rid, start, end)

    dupes = {k: c for k, c in seen.items() if c > 1}
    print("---")
    if dupes:
        print(f"⚠ 塌缩: {len(dupes)} 组区间被多行共用 -> {dupes}")
        print("  典型症状：多段原片对白匹配失败，fallback 到同一条末尾字幕。")
        print("  修在 backend/manual_script.py::match_source_block（前向游标改软惩罚）。")
    print(f"重叠数 = {overlaps} => " + ("仍有重叠，渲染会报错" if overlaps else "OK，无重叠"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
