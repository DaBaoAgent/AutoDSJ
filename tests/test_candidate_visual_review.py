import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.candidate_visual_review import (
    _sanitize_review,
    apply_candidate_reviews,
    build_review_tasks,
    run_candidate_visual_review,
)
from backend.vision_api import FrameSample


class CandidateVisualReviewTests(unittest.TestCase):
    def _segment(self):
        return {
            "segment_id": "seg-18",
            "text": "玫瑰和方协文来到小屋",
            "scene_hint": "山间小屋",
            "intent": {
                "characters": ["玫瑰", "方协文"],
                "locations": ["小屋"],
                "objects": [],
                "actions": [],
                "temporal_type": "single_frame",
                "requires_candidate_review": True,
                "must_not_have": [],
                "hard_requirements": {
                    "characters": ["玫瑰", "方协文"], "locations": ["小屋"],
                    "objects": [], "actions": [],
                },
            },
            "candidate_events": [{"score": 0.51}, {"score": 0.49}],
            "_planning_candidates": [
                {"shot_id": "outside", "shot_start": 20, "shot_end": 24, "score": 9.0},
                {"shot_id": "hut-a", "shot_start": 105, "shot_end": 110, "score": 0.8},
                {"shot_id": "hut-b", "shot_start": 112, "shot_end": 118, "score": 0.7},
            ],
        }

    def test_tasks_never_include_candidate_outside_scene_map(self):
        scene_map = {"scenes": [{"name": "山间小屋", "ranges": [[100, 130]]}]}
        tasks = build_review_tasks([self._segment()], scene_map)
        self.assertEqual(len(tasks), 1)
        self.assertEqual([item["shot_id"] for item in tasks[0]["candidates"]], ["hut-a", "hut-b"])
        self.assertEqual(tasks[0]["candidates"][0]["times"], [106.0, 107.5, 109.0])

    def test_cloud_cannot_satisfy_character_gate_without_face_identity(self):
        task = build_review_tasks(
            [self._segment()], {"scenes": [{"name": "山间小屋", "ranges": [[100, 130]]}]})[0]
        parsed = {
            "selected_shot_id": "hut-a", "confidence": 0.99, "needs_review": False,
            "candidates": [{"shot_id": "hut-a", "score": 0.99, "location_match": True,
                            "action_match": True, "object_match": True,
                            "must_not_have_absent": True}],
        }
        review = _sanitize_review(task, parsed, {"hut-a": set()}, 0.72)
        self.assertFalse(review["accepted"])
        self.assertFalse(review["checks"]["characters"])

    def test_single_scene_bounded_candidate_still_gets_hard_review(self):
        segment = self._segment()
        segment["_planning_candidates"] = segment["_planning_candidates"][:2]
        tasks = build_review_tasks(
            [segment], {"scenes": [{"name": "山间小屋", "ranges": [[100, 130]]}]})
        self.assertEqual(len(tasks), 1)
        self.assertEqual([item["shot_id"] for item in tasks[0]["candidates"]], ["hut-a"])

    def test_face_identity_and_cloud_hard_facts_can_reorder_candidate(self):
        segment = self._segment()
        task = build_review_tasks(
            [segment], {"scenes": [{"name": "山间小屋", "ranges": [[100, 130]]}]})[0]
        parsed = {
            "selected_shot_id": "hut-b", "confidence": 0.91, "needs_review": False,
            "candidates": [{"shot_id": "hut-b", "score": 0.91, "location_match": True,
                            "action_match": True, "object_match": True,
                            "must_not_have_absent": True}],
        }
        review = _sanitize_review(
            task, parsed, {"hut-b": {"黄亦玫", "方协文"}}, 0.72)
        summary = apply_candidate_reviews([segment], {
            "status": "complete", "task_count": 1, "task_segment_ids": ["seg-18"],
            "reviews": [review], "errors": [],
        })
        self.assertEqual(summary["unresolved"], 0)
        self.assertEqual(segment["_planning_candidates"][0]["shot_id"], "hut-b")
        self.assertEqual([segment["clip_start"], segment["clip_end"]], [112.0, 118.0])

    def test_partial_cache_retries_only_missing_task(self):
        tasks = [
            {"segment_id": "done", "text": "已完成", "scene_hint": "场景",
             "intent": {"hard_requirements": {}, "must_not_have": []},
             "candidates": [{"shot_id": "a", "range": [1, 2], "matcher_score": 1,
                             "times": [1.5]}]},
            {"segment_id": "pending", "text": "待重试", "scene_hint": "场景",
             "intent": {"hard_requirements": {}, "must_not_have": []},
             "candidates": [{"shot_id": "b", "range": [2, 3], "matcher_score": 1,
                             "times": [2.5]}]},
        ]
        cached_review = {"segment_id": "done", "selected_shot_id": "a", "confidence": 0.9,
                         "accepted": True, "needs_review": False, "hard_requirements_met": True,
                         "checks": {}, "required_roles": [], "identity_roles": [], "candidates": []}
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            video = folder / "episode.mp4"
            video.write_bytes(b"video")
            (folder / "_candidate_visual_review.json").write_text(json.dumps({
                "source_signature": "sig", "status": "partial", "reviews": [cached_review]
            }), "utf-8")

            def extract(_video, out_dir, times, prefix, **_kwargs):
                image = out_dir / f"{prefix}_1.jpg"
                image.write_bytes(b"jpeg")
                return [FrameSample(times[0], 0, image.name, str(image))]

            cloud_result = {"selected_shot_id": "b", "confidence": 0.9, "needs_review": False,
                            "candidates": [{"shot_id": "b", "score": 0.9,
                                            "action_match": True, "location_match": True,
                                            "object_match": True, "must_not_have_absent": True}]}
            with patch("backend.candidate_visual_review.build_review_tasks", return_value=tasks), \
                    patch("backend.candidate_visual_review.detect_materials",
                          return_value=SimpleNamespace(video_path=str(video))), \
                    patch("backend.candidate_visual_review._signature", return_value="sig"), \
                    patch("backend.candidate_visual_review.dashscope_key", return_value="key"), \
                    patch("backend.candidate_visual_review._extract_frames_at_times", side_effect=extract), \
                    patch("backend.candidate_visual_review._build_identity_map", return_value={}), \
                    patch("backend.candidate_visual_review._call_bailian_multimodal_json",
                          return_value=cloud_result) as cloud:
                result = run_candidate_visual_review(
                    folder, [], {}, SimpleNamespace(candidate_review_workers=1),
                    SimpleNamespace(frame_width=1280, frame_height=720, jpeg_q=3), model="test")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["resumed_count"], 1)
            self.assertEqual(result["success_count"], 2)
            self.assertEqual(cloud.call_count, 1)


if __name__ == "__main__":
    unittest.main()
