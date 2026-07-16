# Contributing to AutoDSJ

Thanks for improving AutoDSJ. Please keep the single supported CLI path (`autodsj.py`) and its scene-map, hierarchical-match, and delivery gates intact.

## Development

1. Create a focused branch from `master`.
2. Run `python -m unittest discover -s tests -v`.
3. If you change `skills/autodsj`, validate it and install/check it locally:

```powershell
python C:\Users\xxx13\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\autodsj
python scripts\install_autodsj_skill.py --agent all
python scripts\install_autodsj_skill.py --agent all --check
```

4. Keep secrets, source media, generated videos, and local `config/` out of commits.
5. Open a pull request that explains user-facing behavior and verification.

## Skill contributions

`skills/autodsj` is the canonical, cross-agent source. Do not hand-edit an installed copy under Codex, Hermes, OpenCode, or OpenClaw. The installer mirrors the canonical source to each agent root.

## Security

Use `SECURITY.md` for private vulnerability reports. Never include API keys, session tokens, customer media, or personal data in issues, pull requests, or benchmark fixtures.
