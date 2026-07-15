from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from backend.scene_draft import build_scene_map_draft


def test_scene_draft_is_complete_but_never_self_approves(tmp_path):
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")
    subtitle = tmp_path / "episode.srt"
    subtitle.write_text(
        "1\n00:00:10,000 --> 00:00:20,000\n黄亦玫来到公司开会\n\n"
        "2\n00:02:00,000 --> 00:02:10,000\n她回家和父母争吵\n\n"
        "3\n00:04:00,000 --> 00:04:10,000\n黄振华赶来劝说\n",
        "utf-8",
    )
    media = SimpleNamespace(
        video_path=str(video), subtitle_paths=[str(subtitle)], duration=300.0,
    )
    settings = SimpleNamespace(
        drama=SimpleNamespace(source_count=1),
        video=SimpleNamespace(trim_head=6, trim_tail=15),
    )
    shots = [{"shot_id": f"shot_{i}", "start": float(i), "end": float(i + 10)}
             for i in range(0, 300, 10)]
    shot_index = {"duration": 300.0, "shots": shots}
    script_table = {"rows": [{
        "row_id": 1, "row_type": "narration", "text": "黄亦玫开会后回家。",
        "source_start": 10.0, "source_end": 80.0,
    }]}

    with patch("backend.scene_draft.detect_materials", return_value=media), \
            patch("backend.scene_draft.detect_ad_intervals", return_value=[]):
        draft = build_scene_map_draft(
            tmp_path, settings, shot_index=shot_index, script_table=script_table, force=True,
        )

    assert draft["coverage_reviewed"] is False
    assert draft["review_required"] is True
    assert draft["scene_count"] == len(draft["scenes"])
    assert draft["parent_scene_plans"]["1"][0]["from_shot"] == 1
    assert (tmp_path / "_scene_map.draft.json").exists()
    saved = json.loads((tmp_path / "_scene_map.draft.json").read_text("utf-8"))
    assert saved["draft"] is True

