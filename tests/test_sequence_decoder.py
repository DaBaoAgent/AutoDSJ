import unittest

from backend.sequence_decoder import decode_parent_sequences


class SequenceDecoderTests(unittest.TestCase):
    def test_parent_path_prefers_continuous_event_over_greedy_jump(self):
        segments = [
            {"continuity_group_id": "p:1", "_planning_candidates": [
                {"event_id": "event_01", "shot_id": "a", "shot_start": 10, "scene": "公司", "score": 0.90},
                {"event_id": "event_08", "shot_id": "x", "shot_start": 80, "scene": "餐厅", "score": 0.91},
            ]},
            {"continuity_group_id": "p:1", "_planning_candidates": [
                {"event_id": "event_01", "shot_id": "b", "shot_start": 14, "scene": "公司", "score": 0.84},
                {"event_id": "event_03", "shot_id": "y", "shot_start": 30, "scene": "街道", "score": 0.95},
            ]},
        ]
        summary = decode_parent_sequences(segments)
        self.assertEqual(summary["decoded_groups"], 1)
        selected = [[c for c in row["_planning_candidates"] if c.get("sequence_selected")][0]
                    for row in segments]
        self.assertEqual([item["event_id"] for item in selected], ["event_01", "event_01"])


if __name__ == "__main__":
    unittest.main()
