import unittest

from backend.narration_intent import parse_intent


class NarrationIntentTests(unittest.TestCase):
    def test_extracts_character_and_fall_action(self):
        value = parse_intent("玫瑰在山路上崴了脚")
        self.assertEqual(value["subject"], "玫瑰")
        self.assertIn("摔倒", value["actions"])

    def test_pronoun_inherits_previous_subject(self):
        self.assertEqual(parse_intent("她却没有开口", previous_subject="白晓荷")["subject"], "白晓荷")

    def test_extracts_fang_xiewen_and_cooking_action(self):
        value = parse_intent("方协文做饭，玫瑰等着被照顾")
        self.assertEqual(value["subject"], "方协文")
        self.assertIn("方协文", value["characters"])
        self.assertIn("玫瑰", value["characters"])
        self.assertIn("做饭", value["actions"])

    def test_builds_hard_visual_requirements_for_hut_scene(self):
        value = parse_intent("玫瑰和方协文在山间小屋里相处")
        self.assertEqual(value["hard_requirements"]["characters"], ["玫瑰", "方协文"])
        self.assertIn("小屋", value["locations"])
        self.assertTrue(value["requires_candidate_review"])

    def test_motion_action_requires_sequence_review(self):
        value = parse_intent("玫瑰把方协文推进泳池")
        self.assertIn("推入泳池", value["actions"])
        self.assertEqual(value["temporal_type"], "action_sequence")
        self.assertIn("泳池", value["locations"])

    def test_explicit_negative_is_not_turned_into_positive_requirement(self):
        value = parse_intent("这里不是办公室，而是小屋")
        self.assertIn("办公室", value["must_not_have"])
        self.assertIn("小屋", value["must_have"])

    def test_metaphorical_approach_is_not_a_hard_visible_action(self):
        value = parse_intent("两个人的关系正在一步步靠近")
        self.assertIn("拉手", value["actions"])
        self.assertNotIn("拉手", value["must_have"])
        self.assertEqual(value["hard_requirements"]["actions"], [])
        self.assertFalse(value["requires_candidate_review"])

    def test_object_character_is_not_forced_into_same_shot(self):
        value = parse_intent("黄振华没有忘记苏更生")
        self.assertEqual(value["subject"], "黄振华")
        self.assertEqual(value["hard_requirements"]["characters"], ["黄振华"])

    def test_first_mentioned_character_is_subject(self):
        value = parse_intent("自卑让苏更生，不敢依靠黄振华")
        self.assertEqual(value["subject"], "苏更生")
        self.assertEqual(value["hard_requirements"]["characters"], ["苏更生"])

    def test_explicit_pair_still_requires_both_characters(self):
        value = parse_intent("可看到玫瑰和方协文")
        self.assertEqual(value["hard_requirements"]["characters"], ["玫瑰", "方协文"])

    def test_context_character_is_not_required_in_explicit_pair_shot(self):
        value = parse_intent("苏更生出差期间，黄振华再次和白晓荷见面")
        self.assertEqual(value["hard_requirements"]["characters"], ["黄振华", "白晓荷"])

    def test_future_company_aspiration_is_not_current_location(self):
        value = parse_intent("他想开一家自己的公司", previous_subject="方协文")
        self.assertIn("办公室", value["locations"])
        self.assertEqual(value["hard_requirements"]["locations"], [])

    def test_explicit_arrival_at_company_remains_hard_location(self):
        value = parse_intent("再次来到蜻蜓公司谈合作")
        self.assertEqual(value["hard_requirements"]["locations"], ["办公室"])
