import json
import tempfile
import unittest
from pathlib import Path

from backend.selective_visual import (
    build_selective_visual_plan,
    resolve_visual_target,
    visual_index_matches_plan,
)


class SelectiveVisualTests(unittest.TestCase):
    def test_adaptive_budget_expands_only_for_risk(self):
        low, low_meta = resolve_visual_target(
            requested=0, preferred=90, minimum=60, maximum=120,
            scene_count=4, segments=[],
        )
        high_segments = [{
            "intent": {"actions": ["摔倒"], "state": "acting"},
            "candidate_events": [{"score": 0.51}, {"score": 0.49}],
        } for _ in range(6)]
        high, high_meta = resolve_visual_target(
            requested=0, preferred=90, minimum=60, maximum=120,
            scene_count=12, segments=high_segments,
        )
        self.assertEqual(low, 60)
        self.assertEqual(low_meta["level"], "low")
        self.assertEqual(high, 120)
        self.assertEqual(high_meta["level"], "critical")

    def test_explicit_budget_override_is_preserved(self):
        target, meta = resolve_visual_target(
            requested=74, preferred=90, minimum=60, maximum=120,
            scene_count=20, segments=[],
        )
        self.assertEqual(target, 74)
        self.assertEqual(meta["mode"], "fixed")

    def test_critical_risk_can_use_240_frame_ceiling(self):
        segments = [{
            "intent": {"actions": ["摔倒"], "state": "acting"},
            "candidate_events": [{"score": 0.51}, {"score": 0.49}],
        } for _ in range(12)]
        target, meta = resolve_visual_target(
            requested=0, preferred=120, minimum=60, maximum=240,
            scene_count=25, segments=segments,
        )
        self.assertEqual(target, 240)
        self.assertEqual(meta["level"], "critical")

    def test_plan_is_bounded_and_covers_each_macro_scene(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            scenes = [{"name": f"场景{i}", "ranges": [[i * 20, i * 20 + 18]]} for i in range(12)]
            (folder / "_scene_map.json").write_text(
                json.dumps({"scenes": scenes}, ensure_ascii=False), "utf-8")
            (folder / "_source_shot_index.json").write_text(
                json.dumps({"duration": 240, "shots": []}), "utf-8")
            plan = build_selective_visual_plan(folder, target=60, minimum=60, maximum=120)
            self.assertGreaterEqual(plan["frame_count"], 60)
            self.assertLessEqual(plan["frame_count"], 120)
            reasons = {point["reason"] for point in plan["points"]}
            for i in range(12):
                self.assertIn(f"scene-center:场景{i}", reasons)

    def test_visual_index_must_exactly_match_plan_and_finish_all_frames(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            times = [float(i) for i in range(60)]
            (folder / "_selective_visual_plan.json").write_text(
                json.dumps({"times": times}), "utf-8")
            signature = [{"source_index": 1, "time": value} for value in times]
            index = {"visual_schema": "v3-selective-face-720p", "frame_count": 60,
                     "success_count": 59, "status": "complete", "source_signature": signature}
            (folder / "_source_visual_index.json").write_text(json.dumps(index), "utf-8")
            self.assertFalse(visual_index_matches_plan(folder))
            index["success_count"] = 60
            (folder / "_source_visual_index.json").write_text(json.dumps(index), "utf-8")
            self.assertTrue(visual_index_matches_plan(folder))

    def test_action_candidates_get_burst_frames_inside_scene(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            (folder / "_scene_map.json").write_text(json.dumps({
                "scenes": [{"name": "泳池", "ranges": [[100, 130]]}]
            }, ensure_ascii=False), "utf-8")
            (folder / "_source_shot_index.json").write_text(
                json.dumps({"duration": 140, "shots": []}), "utf-8")
            segments = [{
                "segment_id": "seg-1",
                "scene_hint": "泳池",
                "intent": {"actions": ["推入泳池"], "temporal_type": "action_sequence",
                           "requires_candidate_review": True},
                "candidate_events": [{"score": 0.51}, {"score": 0.49}],
                "candidate_shots": [{"shot_id": "shot-1", "range": [105, 115]}],
            }]
            plan = build_selective_visual_plan(
                folder, target=60, minimum=60, maximum=120, segments=segments)
            burst = [point for point in plan["points"] if point["reason"].startswith("candidate-burst:")]
            self.assertEqual([point["time"] for point in burst], [107.0, 110.0, 113.0])
            self.assertTrue(all(100 <= point["time"] <= 130 for point in burst))

    def test_plan_does_not_sample_outside_configured_source_trim(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            (folder / "_scene_map.json").write_text(json.dumps({
                "scenes": [{"name": "片头", "ranges": [[0, 200]]},
                           {"name": "正片", "ranges": [[200, 500]]}]
            }, ensure_ascii=False), "utf-8")
            (folder / "_source_shot_index.json").write_text(
                json.dumps({"duration": 500, "shots": []}), "utf-8")
            (folder / "_source_subtitle_index.json").write_text(json.dumps({
                "sources": [{"trim_start": 144, "trim_end": 460}]
            }), "utf-8")
            plan = build_selective_visual_plan(folder, target=60, minimum=60, maximum=120)
            self.assertTrue(all(144 <= value <= 460 for value in plan["times"]))


if __name__ == "__main__":
    unittest.main()
