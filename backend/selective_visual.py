from __future__ import annotations

import json
import math
from pathlib import Path

PLAN_FILE = "_selective_visual_plan.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _add(points: list[dict], value: float, reason: str, *, priority: int = 0) -> None:
    if value < 0:
        return
    for item in points:
        if abs(float(item["time"]) - value) < 0.45:
            if priority > int(item.get("priority", 0)):
                item.update({"time": round(value, 3), "reason": reason, "priority": priority})
            return
    points.append({"time": round(value, 3), "reason": reason, "priority": priority})


def _uniform(left: float, right: float, count: int) -> list[float]:
    if count <= 0 or right <= left:
        return []
    step = (right - left) / count
    return [left + step * (index + 0.5) for index in range(count)]


def _review_priority(segment: dict) -> float:
    intent = segment.get("intent") or {}
    candidates = segment.get("candidate_events") or []
    margin = 1.0
    if len(candidates) >= 2:
        margin = float(candidates[0].get("score", 0)) - float(candidates[1].get("score", 0))
    return (
        6.0 * int(intent.get("temporal_type") == "action_sequence")
        + 3.0 * int(bool(intent.get("requires_candidate_review")))
        + 1.5 * min(2, len(intent.get("characters") or []))
        + 1.5 * int(bool(intent.get("locations")))
        + 1.0 * int(bool(intent.get("objects")))
        + 2.0 * int(margin < 0.08)
    )


def _inside_scene(start: float, end: float, scene_hint: str | None,
                  scene_ranges: list[tuple[str, float, float]]) -> bool:
    if not scene_ranges:
        return True
    midpoint = (start + end) / 2.0
    allowed = [item for item in scene_ranges if not scene_hint or item[0] == scene_hint]
    return any(left <= midpoint <= right for _, left, right in allowed)


def _script_segments(folder: Path) -> list[dict]:
    table = _load(folder / "_drama_script_table.json")
    if not table:
        return []
    from backend.narration_intent import parse_intent
    return [
        {
            "segment_id": f"script-{row.get('row_id')}",
            "intent": parse_intent(str(row.get("text") or "")),
            "candidate_events": [],
            "candidate_shots": [],
        }
        for row in table.get("rows", [])
        if row.get("row_type") == "narration"
    ]


def resolve_visual_target(*, requested: int, preferred: int, minimum: int, maximum: int,
                          scene_count: int, segments: list[dict]) -> tuple[int, dict]:
    """Choose a bounded visual budget from scene complexity and match risk."""
    minimum = max(1, min(int(minimum), 120))
    maximum = max(minimum, min(int(maximum), 120))
    preferred = max(minimum, min(int(preferred), maximum))
    if int(requested or 0) > 0:
        fixed = max(minimum, min(int(requested), maximum))
        return fixed, {"mode": "fixed", "score": None, "level": "manual"}

    action_count = acting_count = ambiguous_count = 0
    for segment in segments:
        intent = segment.get("intent") or {}
        action_count += int(bool(intent.get("actions")))
        acting_count += int(intent.get("state") not in {None, "speaking"})
        candidates = segment.get("candidate_events") or []
        if len(candidates) >= 2:
            margin = float(candidates[0].get("score", 0)) - float(candidates[1].get("score", 0))
            ambiguous_count += int(margin < 0.08)
    score = (max(0, scene_count - 6) * 0.45
             + min(8, action_count) * 0.9
             + min(6, ambiguous_count) * 1.3
             + min(8, acting_count) * 0.25)
    if score < 3.0 and scene_count <= 8:
        target, level = minimum, "low"
    elif score < 9.0 and scene_count <= 15:
        target, level = preferred, "medium"
    else:
        target, level = maximum, "high"
    return target, {
        "mode": "adaptive", "score": round(score, 3), "level": level,
        "scene_count": scene_count, "action_segments": action_count,
        "ambiguous_segments": ambiguous_count, "acting_segments": acting_count,
    }


def build_selective_visual_plan(folder: Path, *, target: int = 0, preferred: int = 90,
                                minimum: int = 60, maximum: int = 120,
                                segments: list[dict] | None = None) -> dict:
    """Choose a bounded set of frames after text/scene narrowing.

    High-priority points come from ambiguous/action candidate shots in the
    latest shadow report. Remaining budget is distributed across every reviewed
    macro scene, so silent events still retain visual coverage.
    """
    folder = folder.resolve()
    minimum = max(1, min(int(minimum), 120))
    maximum = max(minimum, min(int(maximum), 120))
    scene_map_path = folder / "_scene_map.json"
    if not scene_map_path.exists():
        scene_map_path = folder / "_scene_map.draft.json"
    scene_map = _load(scene_map_path)
    shot_index = _load(folder / "_source_shot_index.json")
    subtitle_index = _load(folder / "_source_subtitle_index.json")
    report = _load(folder / "★ 分层影子匹配报告.json")
    points: list[dict] = []
    selected_segments = segments if segments is not None else report.get("segments", [])
    if not selected_segments:
        selected_segments = _script_segments(folder)
    target, risk = resolve_visual_target(
        requested=target, preferred=preferred, minimum=minimum, maximum=maximum,
        scene_count=len(scene_map.get("scenes", [])), segments=selected_segments,
    )

    # Reviewed manual ranges and action/ambiguous top shots are the most useful
    # places to spend expensive visual calls.
    sources = subtitle_index.get("sources") or []
    trim_left = float(sources[0].get("trim_start", 0)) if sources else 0.0
    trim_right = float(sources[0].get("trim_end", shot_index.get("duration") or 0)) if sources else 0.0
    scene_ranges = [(str(scene.get("name")), float(left), float(right))
                    for scene in scene_map.get("scenes", []) for left, right in scene.get("ranges", [])
                    if float(right) > float(left)]
    # Reserve one center for every macro scene before boundary detail. This
    # prevents the bounded frame cap from starving scenes late in an episode.
    for name, left, right in scene_ranges:
        _add(points, (left + right) / 2, f"scene-center:{name}", priority=3)
    for name, left, right in scene_ranges:
            _add(points, left + min(0.6, max(0.1, (right - left) * 0.08)),
                 f"scene-start:{name}", priority=2)
            _add(points, right - min(0.6, max(0.1, (right - left) * 0.08)),
                 f"scene-end:{name}", priority=2)
    review_segments = sorted(
        enumerate(selected_segments), key=lambda pair: (-_review_priority(pair[1]), pair[0])
    )[:12]
    for _, segment in review_segments:
        intent = segment.get("intent") or {}
        candidates = segment.get("candidate_shots") or []
        event_candidates = segment.get("candidate_events") or []
        margin = 1.0
        if len(event_candidates) >= 2:
            margin = float(event_candidates[0].get("score", 0)) - float(event_candidates[1].get("score", 0))
        needs_review = bool(intent.get("actions")) or intent.get("state") != "speaking" or margin < 0.08
        if not needs_review:
            continue
        for candidate in candidates[:2]:
            span = candidate.get("range") or []
            if len(span) == 2:
                left, right = float(span[0]), float(span[1])
                if right <= left or not _inside_scene(left, right, segment.get("scene_hint"), scene_ranges):
                    continue
                if intent.get("temporal_type") == "action_sequence":
                    for label, ratio in (("pre", 0.2), ("mid", 0.5), ("post", 0.8)):
                        _add(points, left + (right - left) * ratio,
                             f"candidate-burst:{segment.get('segment_id')}:{candidate.get('shot_id')}:{label}",
                             priority=5)
                else:
                    _add(points, (left + right) / 2,
                         f"candidate:{segment.get('segment_id')}:{candidate.get('shot_id')}", priority=5)

    # Give every macro scene a proportional share of the remaining budget.
    total_scene_duration = sum(right - left for _, left, right in scene_ranges)
    remaining = max(0, target - len(points))
    allocated = 0
    for index, (name, left, right) in enumerate(scene_ranges):
        if index == len(scene_ranges) - 1:
            count = remaining - allocated
        else:
            count = int(round(remaining * (right - left) / max(1.0, total_scene_duration)))
            allocated += count
        for value in _uniform(left, right, max(0, count)):
            _add(points, value, f"scene-coverage:{name}", priority=1)

    # The formal source index trims opening/ending material.  Preserve the
    # reviewed scene allocation, but discard points the extractor is forbidden
    # to emit; this also keeps existing valid cloud-frame cache IDs stable.
    if sources:
        points = [item for item in points if trim_left <= float(item["time"]) <= trim_right]
    cached_visual = _load(folder / "_source_visual_index.json")
    if cached_visual.get("visual_schema") == "v3-selective-face-720p":
        for item in cached_visual.get("source_signature", []):
            value = float(item.get("time", -1))
            if (not sources or trim_left <= value <= trim_right):
                _add(points, value, "cached-coverage", priority=0)

    # If no scene map exists yet, use physical-shot centers, then uniform source
    # coverage. Formal rendering still requires the complete scene map gate.
    if len(points) < target:
        shots = shot_index.get("shots", [])
        stride = max(1, math.ceil(len(shots) / max(1, target - len(points))))
        for shot in shots[::stride]:
            midpoint = (float(shot["start"]) + float(shot["end"])) / 2
            if sources and not trim_left <= midpoint <= trim_right:
                continue
            _add(points, midpoint, f"shot-coverage:{shot.get('shot_id')}", priority=0)
            if len(points) >= target:
                break
    if len(points) < target:
        duration = float(shot_index.get("duration") or 0)
        left = trim_left if sources else 0.0
        right = trim_right if sources else duration
        for value in _uniform(left, right, max(target * 2, 1)):
            _add(points, value, "source-coverage", priority=0)
            if len(points) >= target:
                break
    if len(points) < minimum:
        duration = float(shot_index.get("duration") or 0)
        left = trim_left if sources else 0.0
        right = trim_right if sources else duration
        for value in _uniform(left, right, minimum - len(points)):
            _add(points, value, "source-coverage", priority=0)

    points.sort(key=lambda item: (-int(item.get("priority", 0)), float(item["time"])))
    points = points[:maximum]
    points.sort(key=lambda item: float(item["time"]))
    payload = {"schema": "v1-selective-visual-plan", "mode": "candidate-driven",
               "target": target, "minimum": minimum, "maximum": maximum,
               "budget": risk, "scene_map_source": scene_map_path.name,
               "frame_count": len(points), "points": points,
               "times": [item["time"] for item in points]}
    (folder / PLAN_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return payload


def visual_index_matches_plan(folder: Path) -> bool:
    """Return True only when the completed visual index represents this plan."""
    plan = _load(folder / PLAN_FILE)
    index = _load(folder / "_source_visual_index.json")
    times = [round(float(value), 3) for value in plan.get("times", [])]
    indexed = [round(float(item.get("time", -1)), 3)
               for item in index.get("source_signature", [])
               if int(item.get("source_index", 1)) == 1]
    frame_count = int(index.get("frame_count") or len(index.get("frames", [])) or 0)
    minimum = max(1, int(plan.get("minimum") or 60))
    maximum = min(120, max(minimum, int(plan.get("maximum") or 120)))
    return bool(
        minimum <= len(times) <= maximum
        and times == indexed
        and index.get("visual_schema") == "v3-selective-face-720p"
        and frame_count == len(times)
        and int(index.get("success_count") or 0) == frame_count
        and index.get("status") not in {"extracting_frames", "recognizing_frames"}
    )
