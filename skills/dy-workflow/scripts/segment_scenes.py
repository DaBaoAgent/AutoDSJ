#!/usr/bin/env python3
"""Coarse scene segmentation of a DY 视觉索引 for building _scene_map.json.

Reads <素材夹>/_source_visual_index.json, buckets each frame by scene-type +
named characters, and merges consecutive frames of the same bucket (allowing
<=25s gaps) into candidate 大镜头 segments. Output is a rough cut — CONSOLIDATE
the candidates into meaningful named macro-scenes by hand using the drama's
per-episode plot (references/rose-story-episodes.json), then write _scene_map.json.

Usage:
    python segment_scenes.py "D:/自动剪辑/玫瑰的故事/玫瑰的故事 第4集"
"""
import json
import sys
from pathlib import Path

NAMES = ["黄亦玫", "玫瑰", "刘亦菲", "庄国栋", "彭冠英", "黄振华", "佟大为",
         "姜雪琼", "朱珠", "苏更生", "白晓荷", "陈瑶", "韩鹦", "吴月江", "黄剑知",
         "方协文", "林更新", "傅家明", "霍建华", "何西", "林一"]


def bucket(r: dict) -> str:
    s = (r.get("scene") or "") + (r.get("caption") or "")[:20]
    tests = [
        ("食堂", "食堂"), ("病房", "病房"), ("医院", "病房"), ("病号", "病房"),
        ("广告", "广告"), ("演播", "广告"), ("天台", "户外天台"), ("阳台", "户外天台"),
        ("宴会", "宴会/车"), ("酒店", "宴会/车"), ("车后座", "宴会/车"), ("车内", "宴会/车"),
        ("公寓", "公寓/卧室"), ("卧室", "公寓/卧室"), ("厨房", "公寓/卧室"),
        ("电脑", "电脑屏"), ("QQ", "电脑屏"), ("屏幕", "电脑屏"),
        ("客厅", "家/客厅"), ("家庭餐厅", "家/客厅"),
        ("甜品", "餐厅"), ("柜台", "餐厅"), ("餐厅", "餐厅"),
        ("会议", "会议室"), ("走廊", "走廊/大厅"), ("大厅", "走廊/大厅"),
        ("办公", "办公室"), ("户外", "户外/展"), ("水池", "户外/展"),
        ("建筑", "户外/展"), ("画廊", "户外/展"), ("展", "户外/展"), ("街", "户外/展"),
    ]
    for key, name in tests:
        if key in s:
            return name
    return "其他:" + (r.get("scene") or "")[:6]


def who(r: dict) -> list[str]:
    p = (r.get("people") or "") + (r.get("caption") or "")
    return list(dict.fromkeys(n for n in NAMES if n in p))


def main() -> None:
    folder = Path(sys.argv[1])
    d = json.loads((folder / "_source_visual_index.json").read_text("utf-8"))
    fr = sorted(d.get("frames") or d.get("records") or [],
                key=lambda r: float(r.get("time", r.get("timestamp", 0))))
    segs, cur = [], None
    for r in fr:
        t = float(r.get("time", r.get("timestamp", 0)))
        b = bucket(r)
        if cur and b == cur["b"] and t - cur["end"] <= 25:
            cur["end"] = t
            cur["who"].update(who(r))
            cur["n"] += 1
        else:
            if cur:
                segs.append(cur)
            cur = {"b": b, "start": t, "end": t, "who": set(who(r)), "n": 1}
    if cur:
        segs.append(cur)
    print(f"共 {len(segs)} 个候选大镜头段（细碎，需按剧情合并命名）：")
    for i, s in enumerate(segs, 1):
        w = ",".join(x for x in s["who"] if x)
        print(f'{i:3d}. {s["start"]:5.0f}-{s["end"]:5.0f}s [{s["b"]:8s}] {s["n"]:2d}帧 人物:{w or "-"}')


if __name__ == "__main__":
    main()
