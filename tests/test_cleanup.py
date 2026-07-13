from backend.cleanup import cleanup_render_artifacts


def test_cleanup_preserves_sources_maps_indexes_and_finals(tmp_path):
    keep = ["source.mkv", "_scene_map.json", "_source_visual_index.json", "★ 成片.mp4"]
    for name in keep:
        (tmp_path / name).write_bytes(b"keep")
    (tmp_path / "配音.wav").write_bytes(b"old-audio")
    clips = tmp_path / "_anchored_clips"
    clips.mkdir()
    (clips / "clip.mp4").write_bytes(b"old-clip")

    removed, reclaimed = cleanup_render_artifacts(tmp_path)

    assert len(removed) == 2
    assert reclaimed == len(b"old-audio") + len(b"old-clip")
    assert all((tmp_path / name).exists() for name in keep)
