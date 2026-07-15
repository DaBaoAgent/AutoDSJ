import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.shot_index import _fingerprint, _key_times, build_shot_index, parse_scene_times


class ShotIndexTests(unittest.TestCase):
    def test_parse_scene_times_deduplicates_transition_frames(self):
        self.assertEqual(parse_scene_times("pts_time:1.20 x pts_time:1.28 x pts_time:4.75"), [1.2, 4.75])

    def test_short_shot_gets_one_interior_key(self):
        values = _key_times(10.0, 10.8)
        self.assertEqual(len(values), 1)
        self.assertTrue(10.0 < values[0] < 10.8)

    def test_long_shot_gets_temporal_coverage(self):
        values = _key_times(0.0, 10.0)
        self.assertEqual(len(values), 5)
        self.assertLess(values[0], 1.0)
        self.assertGreater(values[-1], 9.0)

    def test_new_visual_index_rehydrates_shots_without_redetecting_boundaries(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            video = folder / "episode.mp4"
            video.write_bytes(b"video")
            subtitle = folder / "episode.srt"
            subtitle.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n对白\n", "utf-8",
            )
            media = SimpleNamespace(
                video_path=str(video), subtitle_paths=[str(subtitle)], duration=20.0,
            )
            signature = _fingerprint(video)
            (folder / "_source_shot_boundaries.json").write_text(json.dumps({
                "signature": signature, "threshold": 8.0, "boundaries": [0.0, 10.0, 20.0],
            }), "utf-8")
            with patch("backend.shot_index.detect_materials", return_value=media):
                first = build_shot_index(folder)
                self.assertFalse(first["shots"][0]["nearest_visual_frames"])
                (folder / "_source_visual_index.json").write_text(json.dumps({
                    "frames": [{
                        "frame_id": "f1", "time": 5.0, "caption": "黄亦玫在公司开会",
                        "people": "黄亦玫",
                    }]
                }, ensure_ascii=False), "utf-8")
                with patch("backend.shot_index.detect_shot_boundaries",
                           side_effect=AssertionError("must reuse boundary cache")):
                    refreshed = build_shot_index(folder)
            self.assertEqual(
                refreshed["shots"][0]["nearest_visual_frames"][0]["frame_id"], "f1",
            )
