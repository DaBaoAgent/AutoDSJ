# 为 AutoDSJ 贡献 / Contributing to AutoDSJ

AutoDSJ 由 **Dabao** 发起并维护。感谢你帮助改善项目。请保持唯一 CLI 入口 `autodsj.py`，以及场景地图、分层匹配和交付门禁完整。

AutoDSJ was created and is maintained by **Dabao**. Thanks for helping improve it. Keep the single supported CLI path (`autodsj.py`) and its scene-map, hierarchical-match, and delivery gates intact.

## Development

1. Create a focused branch from `master`.
2. Run `python -m unittest discover -s tests -v`.
3. Run the tests. If you change `skills/autodsj`, install and verify the canonical Skill locally:

```powershell
python -m unittest discover -s tests -v
python scripts\install_autodsj_skill.py --agent all
python scripts\install_autodsj_skill.py --agent all --check
```

4. Keep secrets, source media, generated videos, and local `config/` out of commits.
5. Open a pull request that explains user-facing behavior and verification.

## Skill contributions

`skills/autodsj` is the canonical, cross-agent source. Do not hand-edit an installed copy under Claude Code, Codex, Hermes, OpenCode, or OpenClaw. The installer mirrors the canonical source to each agent root.

## Security

Use `SECURITY.md` for private vulnerability reports. Never include API keys, session tokens, customer media, or personal data in issues, pull requests, or benchmark fixtures.
