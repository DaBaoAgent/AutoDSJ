import json
from types import SimpleNamespace

import autodsj
from scripts.audit_quality import audit_episode


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_audit_episode_falls_back_to_legacy_workspace(tmp_path):
    legacy = tmp_path / "_DY工作文件"
    _write_json(
        legacy / "★ 匹配报告.json",
        {
            "segments": [
                {
                    "segment_id": 1,
                    "row_type": "narration",
                    "text": "普通解说",
                    "clip_start": 10.0,
                    "clip_end": 12.0,
                    "output_start": 0.0,
                    "match_confidence": "H",
                    "visual_match_score": 0.8,
                    "visual_match_evidence": "",
                }
            ]
        },
    )
    _write_json(legacy / "_source_visual_index.json", {"frames": []})

    report = audit_episode(tmp_path)

    assert report["critical_count"] == 0
    assert report["stats"]["total_narration"] == 1


def test_quality_command_writes_report(tmp_path, monkeypatch):
    _write_json(
        tmp_path / "★ 匹配报告.json",
        {
            "segments": [
                {
                    "segment_id": 1,
                    "row_type": "narration",
                    "text": "普通解说",
                    "clip_start": 10.0,
                    "clip_end": 12.0,
                    "output_start": 0.0,
                    "match_confidence": "H",
                    "visual_match_score": 0.8,
                    "visual_match_evidence": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        autodsj,
        "_resolve_settings",
        lambda _folder: SimpleNamespace(material_folder=str(tmp_path)),
    )

    autodsj.cmd_quality(SimpleNamespace(folder=str(tmp_path), fix=False))

    saved = json.loads((tmp_path / "_quality_audit.json").read_text(encoding="utf-8"))
    assert saved["stats"]["total_narration"] == 1
