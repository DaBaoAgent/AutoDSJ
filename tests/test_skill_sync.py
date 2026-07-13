from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.sync_dy_skill import compare_skill, is_in_sync, sync_skill


class SkillSyncTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source" / "dy-workflow"
        (source / "references").mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: dy-workflow\n---\n", "utf-8")
        (source / "references" / "rules.md").write_text("v1\n", "utf-8")
        return source

    def test_sync_copies_changed_files_and_removes_extras(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source(root)
            target = root / "target" / "dy-workflow"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("old\n", "utf-8")
            (target / "obsolete.md").write_text("delete me\n", "utf-8")

            diff = sync_skill(source, target)

            self.assertTrue(is_in_sync(diff))
            self.assertFalse((target / "obsolete.md").exists())
            self.assertEqual(
                (target / "references" / "rules.md").read_text("utf-8"), "v1\n"
            )

    def test_compare_reports_drift_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source(root)
            target = root / "target" / "dy-workflow"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("different\n", "utf-8")

            diff = compare_skill(source, target)

            self.assertEqual(diff["changed"], ["SKILL.md"])
            self.assertEqual(diff["missing"], ["references/rules.md"])
            self.assertFalse(is_in_sync(diff))


if __name__ == "__main__":
    unittest.main()
