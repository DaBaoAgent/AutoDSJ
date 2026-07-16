from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.ad_filter import detect_ad_intervals, overlaps_ad
from backend.visual_matcher import VisualFrame, VisualIntervalAllocator


class AdFilterTests(unittest.TestCase):
    def test_merges_subtitle_and_visual_ad_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "episode.srt").write_text(
                "1\n00:01:40,000 --> 00:01:42,000\n唯品会搜玫瑰\n\n"
                "2\n00:01:44,000 --> 00:01:46,000\n邀请您精彩继续\n",
                "utf-8",
            )
            (folder / "_source_visual_index.json").write_text(
                json.dumps({
                    "frames": [{
                        "time": 103.0,
                        "interval": 10.0,
                        "caption": "品牌广告牌展示",
                        "scene": "广告宣传场景",
                    }]
                }, ensure_ascii=False),
                "utf-8",
            )

            intervals = detect_ad_intervals(folder, write_index=False)

            self.assertEqual(len(intervals), 1)
            self.assertTrue(overlaps_ad(100.0, 101.0, intervals))
            self.assertIn("subtitle", intervals[0]["sources"])
            self.assertIn("vision", intervals[0]["sources"])

    def test_reads_ass_subtitle_ad_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "episode.ass").write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:01:40.00,0:01:42.00,Default,,0,0,0,,唯品会搜玫瑰\n",
                "utf-8",
            )

            intervals = detect_ad_intervals(folder, write_index=False)

            self.assertEqual(len(intervals), 1)
            self.assertIn("subtitle", intervals[0]["sources"])

    def test_allocator_never_uses_blocked_ad_interval(self) -> None:
        frames = [
            VisualFrame(12.0, "目标人物", "广告中的目标人物"),
            VisualFrame(30.0, "目标人物", "正常剧情中的目标人物"),
        ]
        allocator = VisualIntervalAllocator(
            40.0,
            frames,
            blocked_intervals=[{"start": 10.0, "end": 20.0, "ad_id": 1}],
        )

        start, end, _, _ = allocator.allocate("目标人物", 3.0, 0.0, 40.0, "测试")

        self.assertFalse(start < 20.0 and end > 10.0)

    def test_product_display_visual_is_an_ad_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "_source_visual_index.json").write_text(
                json.dumps({"frames": [{
                    "time": 1488.0,
                    "interval": 8.0,
                    "caption": "一只手拿着易拉罐，展示产品细节",
                    "props": "品牌饮料易拉罐",
                }]}, ensure_ascii=False),
                "utf-8",
            )

            intervals = detect_ad_intervals(folder, write_index=False)

            self.assertEqual(len(intervals), 1)
            self.assertEqual(intervals[0]["start"], 1484.0)
            self.assertEqual(intervals[0]["end"], 1492.0)
            self.assertIn("vision", intervals[0]["sources"])

    def test_wall_notices_are_not_treated_as_inserted_ads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "_source_visual_index.json").write_text(
                json.dumps({"frames": [
                    {
                        "time": 188.833,
                        "interval": 8.78,
                        "caption": "男子走进昏暗的老式房间走廊深处",
                        "props": "墙上的小广告（办证刻章、搬家保洁）",
                    },
                    {
                        "time": 950.56,
                        "interval": 8.78,
                        "caption": "方协文在夜晚室外抬头仰望",
                        "props": "背景墙上的红色办证广告字迹",
                    },
                ]}, ensure_ascii=False),
                "utf-8",
            )

            intervals = detect_ad_intervals(folder, write_index=False)

            self.assertEqual(intervals, [])

    def test_explicit_inserted_commercial_remains_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "_source_visual_index.json").write_text(
                json.dumps({"frames": [{
                    "time": 1039.559,
                    "interval": 8.78,
                    "caption": "男子仰头喝饮料，这是电视剧播放过程中的广告插播画面",
                    "scene": "广告画面",
                }]}, ensure_ascii=False),
                "utf-8",
            )

            intervals = detect_ad_intervals(folder, write_index=False)

            self.assertEqual(len(intervals), 1)
            self.assertIn("vision", intervals[0]["sources"])


if __name__ == "__main__":
    unittest.main()
