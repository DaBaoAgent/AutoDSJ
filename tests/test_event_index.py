import unittest

from backend.event_index import group_event_shots


def shot(n, start, end, text=""):
    return {"shot_id": f"s{n}", "start": start, "end": end, "subtitle_text": text}


class EventIndexTests(unittest.TestCase):
    def test_scene_boundary_is_hard_split(self):
        scenes = [{"name": "A", "ranges": [[0, 10]]}, {"name": "B", "ranges": [[10, 20]]}]
        groups = group_event_shots([shot(1, 1, 5), shot(2, 11, 15)], scenes)
        self.assertEqual([[x["shot_id"] for x in g] for g in groups], [["s1"], ["s2"]])

    def test_long_event_splits_near_subtitle_free_cut(self):
        scenes = [{"name": "A", "ranges": [[0, 100]]}]
        shots = [shot(i, i * 5, (i + 1) * 5, "对白" if i < 4 else "") for i in range(7)]
        groups = group_event_shots(shots, scenes, target=18, maximum=30)
        self.assertGreaterEqual(len(groups), 2)
