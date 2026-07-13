import unittest

from backend.narration_intent import parse_intent


class NarrationIntentTests(unittest.TestCase):
    def test_extracts_character_and_fall_action(self):
        value = parse_intent("玫瑰在山路上崴了脚")
        self.assertEqual(value["subject"], "玫瑰")
        self.assertIn("摔倒", value["actions"])

    def test_pronoun_inherits_previous_subject(self):
        self.assertEqual(parse_intent("她却没有开口", previous_subject="白晓荷")["subject"], "白晓荷")
