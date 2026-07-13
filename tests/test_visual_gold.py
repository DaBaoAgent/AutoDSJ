import unittest

from scripts.evaluate_visual_gold import evaluate


class VisualGoldTests(unittest.TestCase):
    def test_evaluate_interval_overlap(self):
        report = {"segments": [{"row_type": "narration", "text": "人物摔倒了",
                                "clip_start": 10, "clip_end": 12}]}
        gold = {"cases": [{"id": "fall", "contains": "摔倒", "accepted_ranges": [[9, 13]]}]}
        result = evaluate(report, gold)
        self.assertEqual(result["accuracy"], 1.0)
