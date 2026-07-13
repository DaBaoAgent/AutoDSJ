import json
import tempfile
import unittest
from pathlib import Path

from backend.selective_visual import build_selective_visual_plan, visual_index_matches_plan


class SelectiveVisualTests(unittest.TestCase):
    def test_plan_is_bounded_and_covers_each_macro_scene(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            scenes = [{"name": f"场景{i}", "ranges": [[i * 20, i * 20 + 18]]} for i in range(12)]
            (folder / "_scene_map.json").write_text(
                json.dumps({"scenes": scenes}, ensure_ascii=False), "utf-8")
            (folder / "_source_shot_index.json").write_text(
                json.dumps({"duration": 240, "shots": []}), "utf-8")
            plan = build_selective_visual_plan(folder, target=30, minimum=30, maximum=60)
            self.assertGreaterEqual(plan["frame_count"], 30)
            self.assertLessEqual(plan["frame_count"], 60)
            reasons = {point["reason"] for point in plan["points"]}
            for i in range(12):
                self.assertIn(f"scene-center:场景{i}", reasons)

    def test_visual_index_must_exactly_match_plan_and_finish_all_frames(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            times = [float(i) for i in range(30)]
            (folder / "_selective_visual_plan.json").write_text(
                json.dumps({"times": times}), "utf-8")
            signature = [{"source_index": 1, "time": value} for value in times]
            index = {"visual_schema": "v3-selective-face-720p", "frame_count": 30,
                     "success_count": 29, "status": "complete", "source_signature": signature}
            (folder / "_source_visual_index.json").write_text(json.dumps(index), "utf-8")
            self.assertFalse(visual_index_matches_plan(folder))
            index["success_count"] = 30
            (folder / "_source_visual_index.json").write_text(json.dumps(index), "utf-8")
            self.assertTrue(visual_index_matches_plan(folder))


if __name__ == "__main__":
    unittest.main()
