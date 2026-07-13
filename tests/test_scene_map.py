import json

import pytest

from backend.scene_map import validate_scene_map


def _map() -> dict:
    return {
        "coverage_reviewed": True,
        "coverage_ranges": [[10, 30]],
        "scene_count": 2,
        "scenes": [
            {"name": "A", "ranges": [[10, 20]]},
            {"name": "B", "ranges": [[20, 30]]},
        ],
        "parent_scene_plans": {
            "1": [
                {"from_shot": 1, "to_shot": 3, "scene": "A"},
                {"from_shot": 4, "to_shot": 4, "scene": "B"},
            ]
        },
    }


def test_complete_map_and_short_tail_pass(tmp_path):
    (tmp_path / "_scene_map.json").write_text(json.dumps(_map()), "utf-8")
    segments = [
        {"row_type": "narration", "tts_parent_id": 1, "shot_index": shot}
        for shot in range(1, 5)
    ]
    assert validate_scene_map(tmp_path, segments)["scene_count"] == 2


def test_coverage_gap_is_rejected(tmp_path):
    payload = _map()
    payload["scenes"][0]["ranges"] = [[10, 18]]
    (tmp_path / "_scene_map.json").write_text(json.dumps(payload), "utf-8")
    with pytest.raises(RuntimeError, match="空洞"):
        validate_scene_map(tmp_path)


def test_long_tail_or_three_scene_jump_is_rejected(tmp_path):
    payload = _map()
    payload["parent_scene_plans"]["1"] = [
        {"from_shot": 1, "to_shot": 1, "scene": "A"},
        {"from_shot": 2, "to_shot": 4, "scene": "B"},
    ]
    (tmp_path / "_scene_map.json").write_text(json.dumps(payload), "utf-8")
    with pytest.raises(RuntimeError, match="更短"):
        validate_scene_map(tmp_path)
