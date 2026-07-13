import unittest

from backend.shot_index import _key_times, parse_scene_times


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
