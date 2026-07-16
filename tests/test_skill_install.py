from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_autodsj_skill import default_target


class SkillInstallTests(unittest.TestCase):
    def test_all_supported_agent_targets_end_with_skill_name(self):
        with patch.dict(os.environ, {}):
            for agent in ("codex", "hermes", "opencode", "openclaw", "shared", "legacy-hermes"):
                self.assertEqual(default_target(agent).name, "autodsj")

    def test_codex_respects_codex_home(self):
        with patch.dict(os.environ, {"CODEX_HOME": r"C:\\custom-codex"}):
            self.assertEqual(
                default_target("codex"),
                Path(r"C:\\custom-codex") / "skills" / "autodsj",
            )


if __name__ == "__main__":
    unittest.main()
