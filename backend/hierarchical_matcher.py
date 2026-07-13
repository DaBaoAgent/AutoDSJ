from __future__ import annotations

import json
import math
import re
from pathlib import Path

from backend.event_index import build_event_index
from backend.narration_intent import ACTION_VOCAB, parse_intent
from backend.visual_matcher import _semantic_score
from backend.timeline_planner import plan_timeline
from backend.scene_map import validate_scene_map


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _event_text(event: dict) -> str:
    parts = [event.get("scene", ""), event.get("subtitle_text", ""),
             " ".join(event.get("people_evidence", []))]
    for frame in event.get("visual_evidence", []):
        parts.extend(str(frame.get(key) or "") for key in ("caption", "people", "scene", "action", "props"))
    return " ".join(parts)


def _shot_text(shot: dict) -> str:
    parts = [str(shot.get("subtitle_text") or "")]
    for frame in shot.get("nearest_visual_frames", []):
        parts.extend(str(frame.get(key) or "") for key in ("caption", "people", "scene", "action", "props"))
    return " ".join(parts)


def _action_score(intent: dict, evidence: str) -> float:
    if not intent.get("actions"):
        return 0.0
    terms = []
    for action in intent["actions"]:
        terms.extend(ACTION_VOCAB.get(action, "").split())
    hits = sum(1 for term in set(terms) if len(term) >= 2 and term in evidence)
    return min(1.0, hits / 2.0)


def _norm(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def _scene_hint(text: str, scene_map: dict, *, prefer_last: bool = False) -> dict | None:
    override_scene = None
    for item in scene_map.get("overrides", []):
        needle, hay = _norm(item.get("contains")), _norm(text)
        if needle in hay or (len(hay) >= 6 and hay in needle):
            override_scene = next((s for s in scene_map.get("scenes", [])
                                   if s.get("name") == item.get("scene")), override_scene)
            if override_scene and not prefer_last:
                # A reviewed override may also carry a narrow source range.  Keep
                # it with the scene hint so the planner cannot drift to a merely
                # related shot elsewhere in the same macro scene.
                hinted = dict(override_scene)
                if item.get("range"):
                    hinted["manual_range"] = item["range"]
                return hinted
    if override_scene:
        return override_scene
    best, best_score = None, 0
    for scene in scene_map.get("scenes", []):
        keyword_hits = sum(1 for word in scene.get("keywords", []) if word and word in text)
        # A lone name (for example “玫瑰”) is not enough to infer a macro
        # scene: it caused unrelated later shots to inherit the company scene.
        if keyword_hits < 2:
            continue
        score = keyword_hits * 2 + sum(1 for word in scene.get("characters", []) if word and word in text)
        if score > best_score:
            best, best_score = scene, score
    return best


def _parent_scene_hint(parent_id: object, shot_index: object, scene_map: dict) -> tuple[dict | None, str | None]:
    """Resolve a reviewed within-paragraph macro-scene plan.

    A narration paragraph normally stays inside one macro scene.  A plan may
    explicitly move only a trailing/bridging shot group into the next scene.
    This is stronger than per-sentence keywords, which are too easy to confuse
    when the same character appears in several locations.
    """
    plans = scene_map.get("parent_scene_plans", {})
    groups = plans.get(str(parent_id), plans.get(parent_id, [])) if isinstance(plans, dict) else []
    try:
        index = int(shot_index)
    except (TypeError, ValueError):
        return None, None
    for group_index, group in enumerate(groups, 1):
        left = int(group.get("from_shot", 1))
        right = int(group.get("to_shot", 10**6))
        if left <= index <= right:
            scene = next((item for item in scene_map.get("scenes", [])
                          if item.get("name") == group.get("scene")), None)
            return scene, f"plan{group_index}"
    return None, None


def _range_score(start: float, end: float, anchor_start: float, anchor_end: float) -> float:
    overlap = max(0.0, min(end, anchor_end) - max(start, anchor_start))
    if overlap:
        return min(1.0, 0.55 + overlap / max(1.0, end - start) * 0.45)
    gap = anchor_start - end if end < anchor_start else start - anchor_end
    return math.exp(-max(0.0, gap) / 55.0) * 0.48


def _scene_score(event: dict, hint: dict | None) -> float:
    if not hint:
        return 0.0
    if event.get("scene") == hint.get("name"):
        return 1.0
    start, end = float(event["start"]), float(event["end"])
    overlap = max((max(0.0, min(end, float(right)) - max(start, float(left)))
                   for left, right in hint.get("ranges", [])), default=0.0)
    return min(0.8, overlap / max(1.0, end - start))


def _containing_scene_range(start: float, end: float, hint: dict | None) -> tuple[float, float] | None:
    if not hint or not hint.get("ranges"):
        return None
    midpoint = (float(start) + float(end)) / 2.0
    for left, right in hint["ranges"]:
        if float(left) <= midpoint <= float(right):
            return float(left), float(right)
    return None


def build_shadow_report(folder: Path) -> dict:
    folder = folder.resolve()
    old = _load(folder / "★ 匹配报告.json")
    scene_map = validate_scene_map(folder, old.get("segments", []))
    event_index = build_event_index(folder)
    shot_index = _load(folder / "_source_shot_index.json")
    shots = {item["shot_id"]: item for item in shot_index.get("shots", [])}
    events = event_index.get("events", [])
    parent_text: dict[object, str] = {}
    for segment in old.get("segments", []):
        if segment.get("row_type") == "narration":
            pid = segment.get("tts_parent_id") or segment.get("script_row_id")
            parent_text[pid] = parent_text.get(pid, "") + str(segment.get("text") or "")

    output_segments, previous_subject, previous_event = [], "", None
    previous_hint_by_parent: dict[object, dict] = {}
    group_by_parent: dict[object, int] = {}
    for segment in old.get("segments", []):
        if segment.get("row_type") != "narration":
            continue
        pid = segment.get("tts_parent_id") or segment.get("script_row_id")
        own_text = segment.get("visual_intent") or segment.get("text") or ""
        inherited = previous_subject
        if "\u5979" in own_text and "\u73ab\u7470" in parent_text.get(pid, ""):
            inherited = "\u73ab\u7470"
        intent = parse_intent(own_text, previous_subject=inherited)
        if intent["subject"]:
            previous_subject = intent["subject"]
        planned_hint, planned_group = _parent_scene_hint(pid, segment.get("shot_index"), scene_map)
        text_hint = _scene_hint(intent["text"], scene_map)
        hint = planned_hint or text_hint
        if (planned_hint and text_hint and text_hint.get("manual_range")
                and text_hint.get("name") == planned_hint.get("name")):
            hint = {**planned_hint, "manual_range": text_hint["manual_range"]}
        if not hint:
            hint = previous_hint_by_parent.get(pid)
        if not hint:
            hint = _scene_hint(parent_text.get(pid, ""), scene_map, prefer_last=True)
        previous_hint = previous_hint_by_parent.get(pid)
        transition_words = ("\u4e0e\u6b64\u540c\u65f6", "\u800c\u6b64\u65f6", "\u53ef\u5c31\u5728", "\u5c31\u5728", "\u53e6\u4e00\u8fb9")
        scene_changed = bool(previous_hint and hint and previous_hint.get("name") != hint.get("name"))
        explicit_transition = any(word in intent["text"] for word in transition_words)
        if pid not in group_by_parent:
            group_by_parent[pid] = 1
        elif scene_changed or explicit_transition:
            group_by_parent[pid] += 1
            previous_event = None
        if hint:
            previous_hint_by_parent[pid] = hint
        continuity_group_id = f"{pid}:{planned_group or group_by_parent[pid]}"
        anchor_start = float(segment.get("source_start") or 0)
        anchor_end = float(segment.get("source_end") or anchor_start + 1)
        candidate_pool = events
        if hint and hint.get("ranges"):
            strict = [event for event in events if event.get("scene") == hint.get("name")]
            if not strict:
                strict = [event for event in events if _containing_scene_range(
                    float(event["start"]), float(event["end"]), hint)]
            if strict:
                candidate_pool = strict
        ranked = []
        for event in candidate_pool:
            semantic = _semantic_score(intent["expanded_query"], _event_text(event))
            action = _action_score(intent, _event_text(event))
            anchor = _range_score(float(event["start"]), float(event["end"]), anchor_start, anchor_end)
            scene = _scene_score(event, hint)
            chars = 1.0 if intent["subject"] and intent["subject"] in _event_text(event) else 0.0
            continuity = 1.0 if previous_event == event.get("event_id") else 0.0
            total = 0.40 * anchor + 0.18 * semantic + 0.10 * action + 0.25 * scene + 0.05 * chars + 0.02 * continuity
            ranked.append((total, event, {"anchor": round(anchor, 4), "semantic": round(semantic, 4),
                                          "action": round(action, 4), "scene": round(scene, 4), "character": chars,
                                          "continuity": continuity}))
        ranked.sort(key=lambda item: item[0], reverse=True)
        top_events = ranked[:5]
        chosen_event = top_events[0][1]
        event_shots = [shots[sid] for sid in chosen_event.get("shot_ids", []) if sid in shots]
        if hint and hint.get("ranges"):
            event_shots = [shot for shot in event_shots if _containing_scene_range(
                float(shot["start"]), float(shot["end"]), hint)]
        shot_ranked = []
        for shot in event_shots:
            evidence = _shot_text(shot)
            semantic = _semantic_score(intent["expanded_query"], evidence)
            action = _action_score(intent, evidence)
            anchor = _range_score(float(shot["start"]), float(shot["end"]), anchor_start, anchor_end)
            shot_ranked.append((0.42 * semantic + 0.43 * action + 0.15 * anchor,
                                shot, semantic, anchor, action))
        shot_ranked.sort(key=lambda item: item[0], reverse=True)
        chosen_shot = shot_ranked[0][1] if shot_ranked else {"start": chosen_event["start"], "end": chosen_event["end"]}
        manual_range = hint.get("manual_range") if hint else None
        if isinstance(manual_range, (list, tuple)) and len(manual_range) == 2:
            manual_start, manual_end = map(float, manual_range)
            if manual_end > manual_start:
                chosen_shot = {"start": manual_start, "end": manual_end, "shot_id": "manual_override"}
        previous_event = chosen_event.get("event_id")
        result = dict(segment)
        result.update({"old_clip_start": segment.get("clip_start"), "old_clip_end": segment.get("clip_end"),
                       "clip_start": float(chosen_shot["start"]), "clip_end": float(chosen_shot["end"]),
                       "intent": intent, "scene_hint": hint.get("name") if hint else None,
                       "continuity_group_id": continuity_group_id,
                       "shadow_event_id": chosen_event.get("event_id"),
                       "shadow_event_range": [chosen_event.get("start"), chosen_event.get("end")],
                       "shadow_score": round(top_events[0][0], 4),
                       "candidate_events": [{"event_id": event.get("event_id"), "scene": event.get("scene"),
                           "range": [event.get("start"), event.get("end")], "score": round(score, 4),
                           "scores": parts} for score, event, parts in top_events],
                       "candidate_shots": [{"shot_id": shot.get("shot_id"),
                           "range": [shot.get("start"), shot.get("end")], "score": round(score, 4),
                           "semantic": round(semantic, 4), "anchor": round(anchor, 4),
                           "action": round(action, 4)}
                           for score, shot, semantic, anchor, action in shot_ranked[:5]]})
        planning = []
        for event_score, event, _ in top_events:
            for sid in event.get("shot_ids", []):
                shot = shots.get(sid)
                if not shot:
                    continue
                scene_range = _containing_scene_range(
                    float(shot["start"]), float(shot["end"]), hint)
                if hint and hint.get("ranges") and not scene_range:
                    continue
                evidence = _shot_text(shot)
                semantic = _semantic_score(intent["expanded_query"], evidence)
                action = _action_score(intent, evidence)
                preferred_bonus = 0.30 if event["event_id"] == chosen_event.get("event_id") else 0.0
                event_start = max(float(event["start"]), scene_range[0]) if scene_range else event["start"]
                event_end = min(float(event["end"]), scene_range[1]) if scene_range else event["end"]
                planning.append({"score": event_score + 0.25 * semantic + 0.35 * action + preferred_bonus,
                    "event_id": event["event_id"], "event_start": event_start, "event_end": event_end,
                    "shot_id": sid, "shot_start": shot["start"], "shot_end": shot["end"],
                    "allow_source_reuse": bool(planned_group)})
        if isinstance(manual_range, (list, tuple)) and len(manual_range) == 2:
            manual_start, manual_end = map(float, manual_range)
            if manual_end > manual_start:
                # This candidate is deliberately first-class rather than a score
                # bonus: a human-reviewed action/person constraint is stronger
                # evidence than generic semantic similarity.
                planning.insert(0, {"score": 9.0, "event_id": "manual_override",
                    "event_start": manual_start, "event_end": manual_end,
                    "shot_id": "manual_override", "shot_start": manual_start,
                    "shot_end": manual_end, "allow_reuse": True})
        result["_planning_candidates"] = planning
        output_segments.append(result)
    blocked = [(float(item.get("clip_start", 0)), float(item.get("clip_end", 0)))
               for item in old.get("segments", []) if item.get("row_type") == "source_clip"]
    ad_path = folder / "_source_ad_intervals.json"
    if ad_path.exists():
        blocked.extend((float(item["start"]), float(item["end"]))
                       for item in _load(ad_path).get("intervals", _load(ad_path) if isinstance(_load(ad_path), list) else []))
    planning_summary = plan_timeline(output_segments, blocked)
    payload = {"mode": "shadow", "source_report": "★ 匹配报告.json",
               "planning_summary": planning_summary, "segments": output_segments}
    (folder / "★ 分层影子匹配报告.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    comparison = {
        "mode": "side-by-side",
        "note": "影子结果只用于审阅，尚未接管正式渲染。",
        "segments": [{
            "segment_id": item.get("segment_id"), "text": item.get("text"),
            "intent": item.get("intent"), "scene_hint": item.get("scene_hint"),
            "old": {"start": item.get("old_clip_start"), "end": item.get("old_clip_end")},
            "shadow": {"start": item.get("clip_start"), "end": item.get("clip_end"),
                       "event_id": item.get("shadow_event_id"), "score": item.get("shadow_score")},
            "top_events": item.get("candidate_events"), "top_shots": item.get("candidate_shots"),
        } for item in output_segments],
    }
    (folder / "★ 新旧匹配并排对比.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), "utf-8")
    planned_segments = []
    for item in output_segments:
        planned = dict(item)
        planned["action_point"] = [item.get("clip_start"), item.get("clip_end")]
        planned["clip_start"] = item.get("planned_clip_start")
        planned["clip_end"] = item.get("planned_clip_end")
        planned_segments.append(planned)
    takeover = {"mode": "takeover-preview", "safe_to_render": planning_summary["unresolved"] == 0,
                "scene_map_sha256": scene_map["sha256"],
                "planning_summary": planning_summary, "segments": planned_segments}
    (folder / "★ 分层接管预演报告.json").write_text(
        json.dumps(takeover, ensure_ascii=False, indent=2), "utf-8")
    return payload
