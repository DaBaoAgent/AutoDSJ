import unittest

from backend.voice_index import voice_event_score


class VoiceIndexTests(unittest.TestCase):
    def test_target_character_voice_beats_wrong_character(self):
        index = {"segments": [
            {"start": 10, "end": 14, "character": "苏更生", "similarity": 0.81},
            {"start": 14, "end": 15, "character": "黄振华", "similarity": 0.76},
        ]}
        target = voice_event_score(index, 10, 15, ["苏更生"], speaking=True)
        wrong = voice_event_score(index, 10, 15, ["庄国栋"], speaking=True)
        self.assertGreater(target["total"], wrong["total"])
        self.assertGreater(target["identity"], 0.7)

    def test_nickname_maps_to_canonical_voice_folder(self):
        index = {"segments": [
            {"start": 1, "end": 4, "character": "黄亦玫", "similarity": 0.84},
        ]}
        score = voice_event_score(index, 1, 4, ["玫瑰"], speaking=True)
        self.assertGreater(score["identity"], 0.9)


if __name__ == "__main__":
    unittest.main()
