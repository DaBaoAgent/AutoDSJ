from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.export_dy_portable import export_package


class PortableExportTests(unittest.TestCase):
    def test_export_contains_skill_bundle_and_installer_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "package"
            result = export_package(output)

            self.assertTrue((result["skill"] / "SKILL.md").is_file())
            self.assertTrue(result["bundle"].is_file())
            self.assertTrue(result["installer"].is_file())
            self.assertFalse((result["skill"] / "config" / "secrets.json").exists())


if __name__ == "__main__":
    unittest.main()
