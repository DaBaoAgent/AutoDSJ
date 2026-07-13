import json
import tempfile
import unittest
from pathlib import Path

from backend.text_retriever import HybridTextRetriever, build_text_event_index


class TextRetrieverTests(unittest.TestCase):
    def test_subtitle_scene_and_reviewed_script_are_combined(self):
        with tempfile.TemporaryDirectory() as value:
            folder = Path(value)
            (folder / "_scene_map.json").write_text(json.dumps({"scenes": [{
                "name": "公司谈话", "characters": ["玫瑰", "庄国栋"],
                "keywords": ["办公室", "对话"], "ranges": [[10, 20]],
            }]}, ensure_ascii=False), "utf-8")
            (folder / "_drama_script_table.json").write_text(json.dumps({"rows": [{
                "row_type": "source_clip", "source_start": 12, "source_end": 16,
                "text": "庄国栋向玫瑰解释", "visual_intent": "两人在公司说话",
            }]}, ensure_ascii=False), "utf-8")
            payload = build_text_event_index(folder, [{
                "event_id": "e1", "scene": "公司谈话", "start": 10, "end": 20,
                "subtitle_text": "你听我解释", "people_evidence": [], "visual_evidence": [],
            }])
            text = payload["events"][0]["text"]
            self.assertIn("庄国栋向玫瑰解释", text)
            self.assertIn("办公室", text)

    def test_bm25_ranks_exact_character_action_event(self):
        docs = [
            {"event_id": "e1", "text": "玫瑰和庄国栋在公司说话"},
            {"event_id": "e2", "text": "黄振华独自在家吃饭"},
        ]
        retriever = HybridTextRetriever(docs)
        left = retriever.score("玫瑰庄国栋公司说话", "e1", characters=["玫瑰"], actions=["说话"])
        right = retriever.score("玫瑰庄国栋公司说话", "e2", characters=["玫瑰"], actions=["说话"])
        self.assertGreater(left["total"], right["total"])


if __name__ == "__main__":
    unittest.main()
