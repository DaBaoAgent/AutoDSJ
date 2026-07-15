from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .event_index import build_event_index
from .manual_script import generate_manual_script_table
from .scene_draft import SCENE_DRAFT_FILE, build_scene_map_draft
from .selective_visual import build_selective_visual_plan
from .shot_index import build_shot_index


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def prepare_core_indexes(folder: Path, settings, *, force: bool = False,
                         target_frames: int = 0) -> dict:
    """Build independent first-run indexes concurrently, then their dependants."""
    folder = Path(folder).resolve()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dy-index") as executor:
        shot_future = executor.submit(build_shot_index, folder, force=force)
        script_future = executor.submit(generate_manual_script_table, settings)
        shot_index = shot_future.result()
        script_table = script_future.result()

    formal_map = _read_json(folder / "_scene_map.json")
    if formal_map:
        scene_payload = formal_map
        scene_source = "_scene_map.json"
    else:
        scene_payload = build_scene_map_draft(
            folder, settings, shot_index=shot_index, script_table=script_table, force=force,
        )
        scene_source = SCENE_DRAFT_FILE

    event_index = build_event_index(
        folder,
        force=force,
        shot_index=shot_index,
        scenes=scene_payload.get("scenes", []),
    )
    visual = settings.visual
    visual_plan = build_selective_visual_plan(
        folder,
        target=target_frames,
        preferred=visual.selective_target_frames,
        minimum=visual.selective_min_frames,
        maximum=visual.selective_max_frames,
    )
    return {
        "shot_index": shot_index,
        "script_table": script_table,
        "scene_map": scene_payload,
        "scene_source": scene_source,
        "event_index": event_index,
        "visual_plan": visual_plan,
    }


def refresh_visual_evidence(folder: Path, scenes: list[dict]) -> dict:
    """Attach newly generated visual records without rerunning shot detection."""
    folder = Path(folder).resolve()
    shot_index = build_shot_index(folder, force=False)
    event_index = build_event_index(folder, shot_index=shot_index, scenes=scenes)
    return {"shot_index": shot_index, "event_index": event_index}
