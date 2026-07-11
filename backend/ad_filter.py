from __future__ import annotations

import json
import re
from pathlib import Path

from .vision_api import parse_srt

AD_INDEX_FILE = "_source_ad_intervals.json"

_VISUAL_AD_MARKERS = (
    "广告", "品牌植入", "广告宣传", "广告牌", "商业宣传", "赞助", "冠名",
    "二维码", "logo展示", "产品展示",
)

_SUBTITLE_AD_MARKERS = (
    "唯品会", "邀您", "邀请您", "冠名", "销量第一", "免费上门换", "搜玫瑰",
    "精彩继续", "广告之后", "马上回来", "奶粉", "阿胶浆", "本节目由",
)

_INVITE_PATTERN = re.compile(r"邀(?:请)?(?:你|您|我们)?.{0,12}(?:观看|收看|继续|入夏)")


def _source_subtitles(folder: Path) -> list[Path]:
    return sorted(
        path for path in [*folder.glob("*.srt"), *folder.glob("*.ass")]
        if not path.name.startswith("★") and not path.name.startswith("_")
    )


def _subtitle_signals(folder: Path) -> list[dict]:
    signals: list[dict] = []
    for path in _source_subtitles(folder):
        try:
            entries = parse_srt(path)
        except RuntimeError:
            continue
        for entry in entries:
            body = entry.text
            compact = re.sub(r"\s+", "", body)
            marker = next((item for item in _SUBTITLE_AD_MARKERS if item.lower() in compact.lower()), "")
            if not marker and _INVITE_PATTERN.search(compact):
                marker = "商业邀请话术"
            if marker:
                signals.append({
                    "start": max(0.0, entry.start - 2.5),
                    "end": entry.end + 2.5,
                    "reason": f"字幕广告信号：{marker}（{body}）",
                    "source": "subtitle",
                })
    return signals


def _visual_signals(folder: Path) -> list[dict]:
    path = folder / "_source_visual_index.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("frames") or payload.get("records") or []
    signals: list[dict] = []
    for record in records:
        evidence = " ".join(str(value) for value in record.values() if value is not None)
        marker = next((item for item in _VISUAL_AD_MARKERS if item.lower() in evidence.lower()), "")
        if not marker:
            continue
        try:
            timestamp = float(record.get("time", record.get("timestamp")))
            interval = max(2.0, float(record.get("interval", 10.0) or 10.0))
        except (TypeError, ValueError):
            continue
        padding = interval / 2.0 + 2.0
        signals.append({
            "start": max(0.0, timestamp - padding),
            "end": timestamp + padding,
            "reason": f"视觉广告信号：{marker}（{record.get('caption', '')}）",
            "source": "vision",
        })
    return signals


def _merge_signals(signals: list[dict], gap: float = 8.0) -> list[dict]:
    merged: list[dict] = []
    for signal in sorted(signals, key=lambda item: (float(item["start"]), float(item["end"]))):
        start, end = float(signal["start"]), float(signal["end"])
        if merged and start <= float(merged[-1]["end"]) + gap:
            merged[-1]["end"] = max(float(merged[-1]["end"]), end)
            if signal["reason"] not in merged[-1]["reasons"]:
                merged[-1]["reasons"].append(signal["reason"])
            merged[-1]["sources"] = sorted(set([*merged[-1]["sources"], signal["source"]]))
            continue
        merged.append({
            "start": start,
            "end": end,
            "reasons": [signal["reason"]],
            "sources": [signal["source"]],
        })
    for index, item in enumerate(merged, 1):
        item["ad_id"] = index
        item["start"] = round(float(item["start"]), 3)
        item["end"] = round(float(item["end"]), 3)
        item["duration"] = round(float(item["end"]) - float(item["start"]), 3)
    return merged


def detect_ad_intervals(folder: Path, *, write_index: bool = True) -> list[dict]:
    intervals = _merge_signals([*_subtitle_signals(folder), *_visual_signals(folder)])
    if write_index:
        payload = {
            "version": 1,
            "method": "visual_and_subtitle_commercial_signals",
            "interval_count": len(intervals),
            "intervals": intervals,
        }
        (folder / AD_INDEX_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return intervals


def overlaps_ad(start: float, end: float, intervals: list[dict]) -> bool:
    return any(float(item["end"]) > start and float(item["start"]) < end for item in intervals)
