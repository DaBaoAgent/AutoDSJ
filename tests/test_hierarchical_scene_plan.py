import unittest

from backend.hierarchical_matcher import _parent_scene_hint


class HierarchicalScenePlanTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
