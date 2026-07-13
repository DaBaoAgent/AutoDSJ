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


def plan_timeline(segments: list[dict], blocked: list[tuple[float, float]], *,
                  hard_blocked: list[tuple[float, float]] | None = None) -> dict:
    """Allocate narration clips with strict global de-duplication first.

    ``blocked`` contains already quoted source material. Reviewed scene plans may
    reuse it only as a second-pass fallback. ``hard_blocked`` contains ads and
    can never be bypassed, including by manual overrides.
    """
    hard = list(hard_blocked or [])
    used = [*blocked, *hard]
    narration_used: list[tuple[float, float]] = []
    previous_by_parent: dict[object, dict] = {}
    failures = []
    for segment in segments:
        parent = segment.get("continuity_group_id") or segment.get("tts_parent_id") or segment.get("script_row_id")
        previous = previous_by_parent.get(parent)
        ranked = []
        for candidate in segment.pop("_planning_candidates", []):
            score = float(candidate.get("score", 0))
            # Parent-level Viterbi is the primary continuity signal. The fit
            # pass may still fall back when the decoded shot cannot fit.
            if candidate.get("sequence_selected"):
                score += 1.45
            if previous:
                if candidate.get("event_id") == previous.get("event_id"):
                    score += 0.28
                delta = float(candidate["shot_start"]) - float(previous.get("start", 0))
                if delta >= -0.15:
                    score += 0.10
                else:
                    score -= 0.85 + min(0.65, abs(delta) / 90.0)
                previous_scene = previous.get("scene")
                current_scene = candidate.get("scene")
                if previous_scene and current_scene and current_scene != previous_scene:
                    score -= 1.20
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        chosen = None
        reuse_mode = "strict"
        need = float(segment.get("audio_duration") or 0)
        # First pass: try every ranked candidate against all material already
        # used anywhere in the edit. A lower-ranked fresh shot is preferable to
        # a high-ranked repeated shot.
        for _, candidate in ranked:
            fitted = fit_window(float(candidate["shot_start"]), float(candidate["shot_end"]), need,
                                float(candidate["event_start"]), float(candidate["event_end"]), used)
            if fitted:
                chosen = {**candidate, "start": fitted[0], "end": fitted[1]}
                break
        # Second pass: a reviewed scene/manual plan may reuse quoted source
        # footage only when no fresh candidate fits. Ads and narration footage
        # remain unavailable in all cases.
        if chosen is None:
            fallback_unavailable = [*hard, *narration_used]
            for _, candidate in ranked:
                if not (candidate.get("allow_reuse") or candidate.get("allow_source_reuse")):
                    continue
                fitted = fit_window(float(candidate["shot_start"]), float(candidate["shot_end"]), need,
                                    float(candidate["event_start"]), float(candidate["event_end"]),
                                    fallback_unavailable)
                if fitted:
                    chosen = {**candidate, "start": fitted[0], "end": fitted[1]}
                    reuse_mode = "source_fallback"
                    break
        if chosen:
            segment["planned_clip_start"], segment["planned_clip_end"] = chosen["start"], chosen["end"]
            segment["planned_event_id"] = chosen["event_id"]
            segment["planned_reuse_mode"] = reuse_mode
            segment["planning_status"] = "ready"
            used.append((chosen["start"], chosen["end"]))
            narration_used.append((chosen["start"], chosen["end"]))
            previous_by_parent[parent] = chosen
        else:
            segment["planning_status"] = "unresolved"
            failures.append(segment.get("segment_id"))
    return {"ready": len(segments) - len(failures), "unresolved": len(failures),
            "unresolved_segment_ids": failures,
            "strict_fresh": sum(item.get("planned_reuse_mode") == "strict" for item in segments),
            "source_reuse_fallback": sum(item.get("planned_reuse_mode") == "source_fallback"
                                         for item in segments)}
