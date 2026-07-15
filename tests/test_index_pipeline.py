from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from backend.index_pipeline import prepare_core_indexes


def test_independent_shot_and_script_indexes_start_in_parallel(tmp_path):
    barrier = threading.Barrier(2, timeout=1.0)

    def build_shots(folder, force=False):
        barrier.wait()
        return {"shots": [], "shot_count": 0}

    def build_script(settings):
        barrier.wait()
        return {"rows": [], "narration_count": 0}

    scene_map = {"scene_count": 1, "scenes": [{"name": "A", "ranges": [[0, 10]]}]}
    (tmp_path / "_scene_map.json").write_text(json.dumps(scene_map), "utf-8")
    settings = SimpleNamespace(visual=SimpleNamespace(
        selective_target_frames=45, selective_min_frames=30, selective_max_frames=60,
    ))
    with patch("backend.index_pipeline.build_shot_index", side_effect=build_shots), \
            patch("backend.index_pipeline.generate_manual_script_table", side_effect=build_script), \
            patch("backend.index_pipeline.build_event_index", return_value={"event_count": 0}), \
            patch("backend.index_pipeline.build_selective_visual_plan",
                  return_value={"frame_count": 30, "budget": {"level": "low"}}):
        result = prepare_core_indexes(tmp_path, settings)

    assert result["scene_source"] == "_scene_map.json"
    assert result["visual_plan"]["frame_count"] == 30
