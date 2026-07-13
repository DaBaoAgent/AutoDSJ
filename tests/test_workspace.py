from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.workspace import organize_episode_folder, restore_episode_workspace


class WorkspaceTests(unittest.TestCase):
    def test_organize_keeps_only_sources_deliverables_and_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "第10集"
            folder.mkdir()
            keep = ["原片.mkv", "字幕.srt", "文案.txt", "★ 成片.mp4", "★ 发布信息.txt", "★ 剪映字幕导入.txt"]
            for name in keep:
                (folder / name).write_text("keep", encoding="utf-8")
            (folder / "_scene_map.json").write_text("{}", encoding="utf-8")
            (folder / "★ 字幕.srt").write_text("generated", encoding="utf-8")
            (folder / "_anchored_clips").mkdir()

            moved = organize_episode_folder(folder)

            self.assertEqual(set(moved), {"_scene_map.json", "★ 字幕.srt", "_anchored_clips"})
            self.assertEqual(
                {item.name for item in folder.iterdir()},
                set(keep) | {"_DY工作文件"},
            )

    def test_restore_makes_archived_indexes_available_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "第10集"
            folder.mkdir()
            (folder / "原片.mp4").write_text("source", encoding="utf-8")
            (folder / "_scene_map.json").write_text("{}", encoding="utf-8")
            organize_episode_folder(folder)

            restored = restore_episode_workspace(folder)

            self.assertEqual([item.name for item in restored], ["_scene_map.json"])
            self.assertTrue((folder / "_scene_map.json").exists())
            self.assertFalse((folder / "_DY工作文件").exists())


if __name__ == "__main__":
    unittest.main()
