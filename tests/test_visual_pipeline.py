from __future__ import annotations

import threading
import time
from unittest.mock import patch

from backend.vision_api import _extract_frames_at_times


def test_sparse_extraction_is_parallel_and_keeps_timestamp_order(tmp_path):
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_run(command, timeout=0):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        output = command[-1]
        from pathlib import Path
        Path(output).write_bytes(b"jpg")
        with lock:
            active -= 1

    with patch("backend.vision_api._run", side_effect=fake_run):
        samples = _extract_frames_at_times(
            tmp_path / "episode.mp4", tmp_path / "frames", [9.0, 1.0, 5.0], "frame",
            workers=3,
        )

    assert peak > 1
    assert [sample.time for sample in samples] == [1.0, 5.0, 9.0]
