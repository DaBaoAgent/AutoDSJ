from __future__ import annotations


def overlaps(start: float, end: float, intervals: list[tuple[float, float]], guard: float = 0.18) -> bool:
    return any(not (end + guard <= left or start >= right + guard) for left, right in intervals)


def fit_window(point_start: float, point_end: float, need: float, event_start: float,
               event_end: float, unavailable: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Expand an action shot to narration duration and slide inside its event."""
    if need <= 0 or event_end - event_start < need:
        return None
    center = (point_start + point_end) / 2
    ideal = max(event_start, min(event_end - need, center - need / 2))
    starts = [ideal]
    step = max(0.25, min(1.0, need / 3))
    distance = step
    while ideal - distance >= event_start or ideal + distance <= event_end - need:
        if ideal - distance >= event_start:
            starts.append(ideal - distance)
        if ideal + distance <= event_end - need:
            starts.append(ideal + distance)
        distance += step
    for start in starts:
        end = start + need
        if not overlaps(start, end, unavailable):
            return round(start, 3), round(end, 3)
    return None


def plan_timeline(segments: list[dict], blocked: list[tuple[float, float]]) -> dict:
    used = list(blocked)
    narration_used: list[tuple[float, float]] = []
    previous_by_parent: dict[object, dict] = {}
    failures = []
    for segment in segments:
        parent = segment.get("continuity_group_id") or segment.get("tts_parent_id") or segment.get("script_row_id")
        previous = previous_by_parent.get(parent)
        ranked = []
        for candidate in segment.pop("_planning_candidates", []):
            score = float(candidate.get("score", 0))
            if previous:
                if candidate.get("event_id") == previous.get("event_id"):
                    score += 0.12
                if float(candidate["shot_start"]) >= float(previous.get("start", 0)):
                    score += 0.05
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        chosen = None
        need = float(segment.get("audio_duration") or 0)
        for _, candidate in ranked:
            # Reviewed scene overrides may intentionally reuse a source moment
            # already present as dialogue elsewhere in the edit.  It is the
            # only case allowed to bypass the anti-repeat material guard.
            if candidate.get("allow_reuse"):
                unavailable = narration_used
            elif candidate.get("allow_source_reuse"):
                unavailable = narration_used
            else:
                unavailable = used
            fitted = fit_window(float(candidate["shot_start"]), float(candidate["shot_end"]), need,
                                float(candidate["event_start"]), float(candidate["event_end"]), unavailable)
            if fitted:
                chosen = {**candidate, "start": fitted[0], "end": fitted[1]}
                break
        if chosen:
            segment["planned_clip_start"], segment["planned_clip_end"] = chosen["start"], chosen["end"]
            segment["planned_event_id"] = chosen["event_id"]
            segment["planning_status"] = "ready"
            used.append((chosen["start"], chosen["end"]))
            narration_used.append((chosen["start"], chosen["end"]))
            previous_by_parent[parent] = chosen
        else:
            segment["planning_status"] = "unresolved"
            failures.append(segment.get("segment_id"))
    return {"ready": len(segments) - len(failures), "unresolved": len(failures),
            "unresolved_segment_ids": failures}
