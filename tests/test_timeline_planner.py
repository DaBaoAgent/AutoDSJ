import unittest

from backend.timeline_planner import fit_window, plan_timeline


class TimelinePlannerTests(unittest.TestCase):
    def test_expands_to_exact_duration_without_overlap(self):
        value = fit_window(5, 6, 4, 0, 10, [(0, 2)])
        self.assertAlmostEqual(value[1] - value[0], 4)

    def test_tries_next_candidate_when_first_is_blocked(self):
        segment = {"segment_id": 1, "audio_duration": 2, "_planning_candidates": [
            {"score": 2, "event_id": "a", "shot_start": 1, "shot_end": 2, "event_start": 0, "event_end": 3},
            {"score": 1, "event_id": "b", "shot_start": 6, "shot_end": 7, "event_start": 5, "event_end": 9}]}
        result = plan_timeline([segment], [(0, 4)])
        self.assertEqual(result["ready"], 1)
        self.assertEqual(segment["planned_event_id"], "b")

    def test_reviewed_scene_can_reuse_source_but_not_other_narration(self):
        segments = [
            {"segment_id": 1, "audio_duration": 2, "_planning_candidates": [
                {"score": 2, "event_id": "a", "shot_start": 1, "shot_end": 2,
                 "event_start": 0, "event_end": 6, "allow_source_reuse": True}]},
            {"segment_id": 2, "audio_duration": 2, "_planning_candidates": [
                {"score": 2, "event_id": "a", "shot_start": 1, "shot_end": 2,
                 "event_start": 0, "event_end": 6, "allow_source_reuse": True}]},
        ]
        result = plan_timeline(segments, [(0, 6)])
        self.assertEqual(result["ready"], 2)
        self.assertNotEqual(segments[0]["planned_clip_start"], segments[1]["planned_clip_start"])
