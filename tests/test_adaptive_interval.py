from __future__ import annotations

import unittest

from backend.drama_source_index import adaptive_frame_interval


class AdaptiveFrameIntervalTests(unittest.TestCase):
    def test_clamped_to_lower_bound_for_short_video(self) -> None:
        # 短视频：duration/target 很小，被下限 4.0 兜住
        self.assertEqual(adaptive_frame_interval(300.0, target_frames=320, lo=4.0, hi=12.0), 4.0)

    def test_clamped_to_upper_bound_for_long_video(self) -> None:
        # 长视频：duration/target 很大，被上限 12.0 兜住
        self.assertEqual(adaptive_frame_interval(9000.0, target_frames=320, lo=4.0, hi=12.0), 12.0)

    def test_scales_within_bounds(self) -> None:
        value = adaptive_frame_interval(2560.0, target_frames=320, lo=4.0, hi=12.0)
        self.assertAlmostEqual(value, 8.0, places=3)

    def test_zero_or_negative_duration_falls_back(self) -> None:
        self.assertEqual(adaptive_frame_interval(0.0), 6.0)
        self.assertEqual(adaptive_frame_interval(-5.0), 6.0)

    def test_result_always_within_bounds(self) -> None:
        for duration in (1, 60, 600, 1500, 3600, 7200):
            value = adaptive_frame_interval(float(duration))
            self.assertGreaterEqual(value, 4.0)
            self.assertLessEqual(value, 12.0)


if __name__ == "__main__":
    unittest.main()
