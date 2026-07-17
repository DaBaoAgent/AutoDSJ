import tempfile
import unittest
from pathlib import Path

from backend.hierarchical_matcher import (
    _load_optional,
    _parent_scene_hint,
    _resolve_scene_hint,
    _scene_hint,
)


class HierarchicalScenePlanTests(unittest.TestCase):
    def test_missing_optional_cache_loads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(_load_optional(Path(temp_dir) / "missing.json"), {})

    def setUp(self):
        self.scene_map = {
            "scenes": [
                {"name": "主场景", "ranges": [[10, 20]]},
                {"name": "承上启下场景", "ranges": [[30, 40]]},
            ],
            "parent_scene_plans": {
                "7": [
                    {"from_shot": 1, "to_shot": 4, "scene": "主场景"},
                    {"from_shot": 5, "to_shot": 6, "scene": "承上启下场景"},
                ]
            },
        }

    def test_paragraph_stays_in_main_scene_until_reviewed_tail(self):
        for shot in range(1, 5):
            scene, group = _parent_scene_hint(7, shot, self.scene_map)
            self.assertEqual(scene["name"], "主场景")
            self.assertEqual(group, "plan1")

    def test_reviewed_tail_can_bridge_to_next_scene(self):
        for shot in (5, 6):
            scene, group = _parent_scene_hint("7", shot, self.scene_map)
            self.assertEqual(scene["name"], "承上启下场景")
            self.assertEqual(group, "plan2")

    def test_sentence_override_is_marked_as_reviewed_evidence(self):
        scene_map = {
            **self.scene_map,
            "overrides": [{"contains": "回忆山间小屋", "scene": "承上启下场景"}],
        }
        scene = _scene_hint("她忽然回忆山间小屋的生活", scene_map)
        self.assertEqual(scene["name"], "承上启下场景")
        self.assertTrue(scene["reviewed_override"])
        resolved, reviewed = _resolve_scene_hint(
            {"name": "主场景", "ranges": [[10, 20]]},
            scene,
        )
        self.assertEqual(resolved["name"], "承上启下场景")
        self.assertTrue(reviewed)


if __name__ == "__main__":
    unittest.main()
