from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.delivery import build_jianying_lines, run_delivery
from backend.schemas import AppSettings


class DeliveryTests(unittest.TestCase):
    def test_jianying_removes_labels_and_keeps_inline_text(self):
        text = "原片：\n你好\n\n解说：她转身离开。\n下一句\n"
        self.assertEqual(build_jianying_lines(text), ["你好", "她转身离开。", "下一句"])

    @patch("backend.delivery.probe_video")
    def test_run_delivery_builds_and_validates_release_package(self, probe):
        probe.return_value = {"duration": 12.0, "width": 1920, "height": 1080}
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "示例剧 第3集"
            folder.mkdir()
            (folder / "文案.txt").write_text(
                "原片：\n你来了\n解说：\n她没想到，对方却隐瞒了真相。\n",
                encoding="utf-8",
            )
            (folder / "★ 成片.mp4").write_bytes(b"video")
            (folder / "★ 字幕.srt").write_text(
                "1\n00:00:00,500 --> 00:00:05,000\n她没想到。\n",
                encoding="utf-8",
            )
            (folder / "★ 匹配报告.json").write_text("{}\n", encoding="utf-8")
            settings = AppSettings(material_folder=str(folder))

            report = run_delivery(settings)

            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["subtitle"]["entry_count"], 1)
            self.assertEqual(report["publish"]["tag_count"], 5)
            self.assertEqual(
                (folder / "★ 剪映字幕导入.txt").read_text("utf-8").splitlines(),
                ["你来了", "她没想到，对方却隐瞒了真相。"],
            )
            saved = json.loads((folder / "_DY工作文件" / "★ 交付清单.json").read_text("utf-8"))
            self.assertEqual(saved["status"], "ready")
            self.assertFalse((folder / "★ 字幕.srt").exists())
            self.assertTrue((folder / "_DY工作文件" / "★ 字幕.srt").exists())

    @patch("backend.delivery.probe_video")
    def test_delivery_rejects_subtitle_past_video_end(self, probe):
        probe.return_value = {"duration": 2.0, "width": 1280, "height": 720}
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "示例剧 第1集"
            folder.mkdir()
            (folder / "文案.txt").write_text("原片：你好\n解说：发生了什么。\n", "utf-8")
            (folder / "★ 成片.mp4").write_bytes(b"video")
            (folder / "★ 匹配报告.json").write_text("{}", "utf-8")
            (folder / "★ 字幕.srt").write_text(
                "1\n00:00:01,000 --> 00:00:03,000\n越界\n", "utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "超过成片时长"):
                run_delivery(AppSettings(material_folder=str(folder)))


if __name__ == "__main__":
    unittest.main()
