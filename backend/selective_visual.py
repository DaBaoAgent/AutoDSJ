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


def build_selective_visual_plan(folder: Path, *, target: int = 45, minimum: int = 30,
                                maximum: int = 60, segments: list[dict] | None = None) -> dict:
    """Choose a bounded set of frames after text/scene narrowing.

    High-priority points come from ambiguous/action candidate shots in the
    latest shadow report. Remaining budget is distributed across every reviewed
    macro scene, so silent events still retain visual coverage.
    """
    folder = folder.resolve()
    minimum = max(1, min(int(minimum), 60))
    maximum = max(minimum, min(int(maximum), 60))
    target = max(minimum, min(int(target), maximum))
    scene_map = _load(folder / "_scene_map.json")
    shot_index = _load(folder / "_source_shot_index.json")
    report = _load(folder / "★ 分层影子匹配报告.json")
    points: list[dict] = []

    # Reviewed manual ranges and action/ambiguous top shots are the most useful
    # places to spend expensive visual calls.
    scene_ranges = [(str(scene.get("name")), float(left), float(right))
                    for scene in scene_map.get("scenes", []) for left, right in scene.get("ranges", [])
                    if float(right) > float(left)]
    # Reserve one center for every macro scene before boundary detail. This
    # prevents the 60-frame cap from starving scenes late in an episode.
    for name, left, right in scene_ranges:
        _add(points, (left + right) / 2, f"scene-center:{name}", priority=3)
    for name, left, right in scene_ranges:
            _add(points, left + min(0.6, max(0.1, (right - left) * 0.08)),
                 f"scene-start:{name}", priority=2)
            _add(points, right - min(0.6, max(0.1, (right - left) * 0.08)),
                 f"scene-end:{name}", priority=2)
    for segment in segments if segments is not None else report.get("segments", []):
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
                _add(points, (float(span[0]) + float(span[1])) / 2,
                     f"candidate:{segment.get('segment_id')}", priority=5)

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

    # If no scene map exists yet, use physical-shot centers, then uniform source
    # coverage. Formal rendering still requires the complete scene map gate.
    if len(points) < target:
        shots = shot_index.get("shots", [])
        stride = max(1, math.ceil(len(shots) / max(1, target - len(points))))
        for shot in shots[::stride]:
            _add(points, (float(shot["start"]) + float(shot["end"])) / 2,
                 f"shot-coverage:{shot.get('shot_id')}", priority=0)
            if len(points) >= target:
                break
    if len(points) < minimum:
        duration = float(shot_index.get("duration") or 0)
        for value in _uniform(0.0, duration, minimum - len(points)):
            _add(points, value, "source-coverage", priority=0)

    points.sort(key=lambda item: (-int(item.get("priority", 0)), float(item["time"])))
    points = points[:maximum]
    points.sort(key=lambda item: float(item["time"]))
    payload = {"schema": "v1-selective-visual-plan", "mode": "candidate-driven",
               "target": target, "minimum": minimum, "maximum": maximum,
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
    return bool(
        30 <= len(times) <= 60
        and times == indexed
        and index.get("visual_schema") == "v3-selective-face-720p"
        and frame_count == len(times)
        and int(index.get("success_count") or 0) == frame_count
        and index.get("status") not in {"extracting_frames", "recognizing_frames"}
    )
