from __future__ import annotations

import json
from pathlib import Path

from backend.shot_index import SHOT_SCHEMA, build_shot_index

EVENT_SCHEMA = "v1-scene-event-index"


def _load_scenes(folder: Path) -> list[dict]:
    path = folder / "_scene_map.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text("utf-8")).get("scenes", [])
    except (OSError, ValueError, TypeError):
        return []


def _scene_for_time(value: float, scenes: list[dict]) -> str:
    for scene in scenes:
        for start, end in scene.get("ranges", []):
            if float(start) <= value <= float(end):
                return str(scene.get("name") or "未命名场景")
    return "未归类场景"


def group_event_shots(shots: list[dict], scenes: list[dict], *, target: float = 18.0,
                      minimum: float = 7.0, maximum: float = 30.0) -> list[list[dict]]:
    """Group physical shots into laptop-friendly event candidates.

    Scene changes are hard boundaries. Inside a macro scene, prefer subtitle-free
    cuts near the target duration; maximum is a hard safety boundary.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_scene = ""
    for shot in shots:
        midpoint = (float(shot["start"]) + float(shot["end"])) / 2
        scene = _scene_for_time(midpoint, scenes)
        elapsed = float(shot["end"]) - float(current[0]["start"]) if current else 0.0
        scene_changed = bool(current) and scene != current_scene
        natural_cut = bool(current) and elapsed >= target and (
            not str(shot.get("subtitle_text") or "").strip()
            or not str(current[-1].get("subtitle_text") or "").strip())
        hard_cut = bool(current) and elapsed >= maximum
        if scene_changed or natural_cut or hard_cut:
            groups.append(current)
            current = []
        current.append(shot)
        current_scene = scene
        if (float(current[-1]["end"]) - float(current[0]["start"]) >= maximum
                and float(current[-1]["end"]) - float(current[0]["start"]) >= minimum):
            groups.append(current)
            current = []
            current_scene = ""
    if current:
        groups.append(current)
    return groups


def build_event_index(folder: Path, *, force: bool = False, shot_index: dict | None = None,
                      scenes: list[dict] | None = None) -> dict:
    folder = folder.resolve()
    shot_index = shot_index or build_shot_index(folder, force=False)
    scenes = _load_scenes(folder) if scenes is None else scenes
    groups = group_event_shots(shot_index.get("shots", []), scenes)
    events = []
    for index, shots in enumerate(groups, 1):
        start, end = float(shots[0]["start"]), float(shots[-1]["end"])
        scene = _scene_for_time((start + end) / 2, scenes)
        subtitles, frames, people = [], [], []
        for shot in shots:
            text = str(shot.get("subtitle_text") or "").strip()
            if text and (not subtitles or subtitles[-1] != text):
                subtitles.append(text)
            for frame in shot.get("nearest_visual_frames", []):
                fid = frame.get("frame_id")
                if fid not in {item.get("frame_id") for item in frames}:
                    frames.append(frame)
                value = str(frame.get("people") or "").strip()
                if value and value not in people:
                    people.append(value)
        events.append({"event_id": f"event_{index:04d}", "scene": scene,
                       "start": round(start, 3), "end": round(end, 3),
                       "duration": round(end - start, 3),
                       "shot_ids": [shot["shot_id"] for shot in shots],
                       "subtitle_text": " ".join(subtitles),
                       "people_evidence": people[:8], "visual_evidence": frames[:12]})
    payload = {"schema": EVENT_SCHEMA, "source_schema": SHOT_SCHEMA,
               "shot_signature": shot_index.get("signature"),
               "event_count": len(events), "events": events}
    output = folder / "_source_event_index.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return payload
