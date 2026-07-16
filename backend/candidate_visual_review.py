from __future__ import annotations

import hashlib
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

from .drama_source_index import _build_identity_map
from .embed_match import dashscope_key
from .face_gallery import render_known_people
from .media import detect_materials
from .net_retry import retry_call
from .vision_api import _call_bailian_multimodal_json, _extract_frames_at_times, _format_time


CACHE_FILE = "_candidate_visual_review.json"
ESCALATION_CACHE_FILE = "_candidate_visual_escalation.json"
SCHEMA = "v1-scene-bounded-multiframe-candidate-review"


def _canonical_role(value: str) -> str:
    aliases = {"黄亦玫": "玫瑰", "Rose": "玫瑰", "rose": "玫瑰"}
    return aliases.get(str(value or "").strip(), str(value or "").strip())


def _scene_ranges(scene_map: dict, scene_hint: str | None) -> list[tuple[float, float]]:
    scenes = scene_map.get("scenes", [])
    selected = [item for item in scenes if not scene_hint or item.get("name") == scene_hint]
    return [(float(left), float(right)) for scene in selected for left, right in scene.get("ranges", [])
            if float(right) > float(left)]


def _range_allowed(start: float, end: float, ranges: list[tuple[float, float]]) -> bool:
    if not ranges:
        return False
    midpoint = (start + end) / 2.0
    return any(left <= midpoint <= right for left, right in ranges)


def _sample_times(start: float, end: float, count: int) -> list[float]:
    count = max(1, min(9, int(count or 3)))
    if count == 1:
        ratios = (0.5,)
    elif count == 2:
        ratios = (0.3, 0.7)
    elif count == 3:
        ratios = (0.2, 0.5, 0.8)
    else:
        # Include the beginning/end state while staying away from exact cuts.
        ratios = tuple(0.08 + 0.84 * index / (count - 1) for index in range(count))
    return [round(start + (end - start) * ratio, 3) for ratio in ratios]


def _limit_images_per_candidate(images: list[dict], limit: int) -> list[dict]:
    """Evenly reduce an oversized multimodal request without dropping candidates."""
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in images:
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in grouped:
            grouped[candidate_id] = []
            order.append(candidate_id)
        grouped[candidate_id].append(item)
    output: list[dict] = []
    for candidate_id in order:
        values = grouped[candidate_id]
        if len(values) <= limit:
            output.extend(values)
            continue
        indexes = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
        output.extend(values[index] for index in indexes)
    return output


def _task_priority(segment: dict) -> float:
    intent = segment.get("intent") or {}
    events = segment.get("candidate_events") or []
    margin = 1.0
    if len(events) >= 2:
        margin = float(events[0].get("score", 0)) - float(events[1].get("score", 0))
    return (
        8.0 * int(intent.get("temporal_type") == "action_sequence")
        + 4.0 * int(len(intent.get("characters") or []) >= 2)
        + 3.0 * int(bool(intent.get("locations")))
        + 2.0 * int(bool(intent.get("objects")))
        + 2.0 * int(margin < 0.08)
    )


def build_review_tasks(segments: list[dict], scene_map: dict, *, max_segments: int = 12,
                       candidates_per_segment: int = 3, frames_per_candidate: int = 3) -> list[dict]:
    """Build high-risk review tasks without ever widening the reviewed scene."""
    ranked: list[tuple[float, int, dict]] = []
    for order, segment in enumerate(segments):
        intent = segment.get("intent") or {}
        if not intent.get("requires_candidate_review"):
            continue
        planning = segment.get("_planning_candidates") or []
        if any(item.get("shot_id") == "manual_override" for item in planning[:1]):
            continue
        allowed = _scene_ranges(scene_map, segment.get("scene_hint"))
        candidates, seen = [], set()
        for item in sorted(planning, key=lambda value: float(value.get("score", 0)), reverse=True):
            shot_id = str(item.get("shot_id") or "")
            if not shot_id or shot_id in seen:
                continue
            start = float(item.get("shot_start", item.get("event_start", 0)))
            end = float(item.get("shot_end", item.get("event_end", start)))
            if end <= start or not _range_allowed(start, end, allowed):
                continue
            seen.add(shot_id)
            candidates.append({
                "shot_id": shot_id,
                "range": [round(start, 3), round(end, 3)],
                "matcher_score": round(float(item.get("score", 0)), 4),
                "times": _sample_times(start, end, frames_per_candidate),
            })
            if len(candidates) >= max(1, int(candidates_per_segment)):
                break
        if not candidates:
            continue
        task = {
            "segment_id": str(segment.get("segment_id")),
            "text": str(segment.get("visual_intent") or segment.get("text") or ""),
            "scene_hint": segment.get("scene_hint"),
            "intent": intent,
            "candidates": candidates,
        }
        ranked.append((_task_priority(segment), order, task))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:max(0, int(max_segments))]]


def _signature(video: Path, model: str, tasks: list[dict], matching: object, visual: object) -> str:
    stat = video.stat()
    value = {
        "schema": SCHEMA,
        "video": {"name": video.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "model": model,
        "tasks": tasks,
        "settings": {
            "confidence": float(getattr(matching, "candidate_review_min_confidence", 0.72)),
            "width": int(getattr(visual, "frame_width", 1280)),
            "height": int(getattr(visual, "frame_height", 720)),
            "jpeg_q": int(getattr(visual, "jpeg_q", 3)),
        },
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _review_matches_task(review: dict, task: dict, frames_per_candidate: int) -> bool:
    reviewed_ids = [
        str(item.get("shot_id") or "")
        for item in review.get("candidates", [])
        if isinstance(item, dict)
    ]
    task_ids = [str(item.get("shot_id") or "") for item in task.get("candidates", [])]
    return (
        str(review.get("segment_id")) == str(task.get("segment_id"))
        and reviewed_ids == task_ids
        and int(review.get("frames_per_candidate") or 0) == int(frames_per_candidate)
    )


def _prompt(task: dict) -> str:
    requirements = task["intent"].get("hard_requirements") or {}
    candidate_ids = [item["shot_id"] for item in task["candidates"]]
    return (
        "你是电视剧剪辑候选镜头复核器。下面是同一句解说的多个候选镜头，每个候选按时间顺序给出多帧。\n"
        "只比较单帧和连续三帧可见事实，不推断集数、剧情因果或谁一定在说话。人物姓名只能使用每张图前的“已知人物”；"
        "已知人物为无时绝不猜姓名。场景范围已经由人工场景地图限定，你不能推荐候选列表之外的镜头。\n"
        f"解说：{task['text']}\n场景硬边界：{task.get('scene_hint') or '已限定范围'}\n"
        f"必须满足：{json.dumps(requirements, ensure_ascii=False)}\n"
        f"禁止出现：{json.dumps(task['intent'].get('must_not_have') or [], ensure_ascii=False)}\n"
        f"候选ID：{json.dumps(candidate_ids, ensure_ascii=False)}\n"
        "逐候选判断人物以外的动作、地点、道具是否命中；动作必须结合前中后帧判断。"
        "只返回严格 JSON："
        "{\"selected_shot_id\":\"候选ID\",\"confidence\":0.0,\"needs_review\":false,"
        "\"candidates\":[{\"shot_id\":\"候选ID\",\"score\":0.0,\"action_match\":true,"
        "\"location_match\":true,\"object_match\":true,\"must_not_have_absent\":true,"
        "\"visible_facts\":[\"事实\"],\"rejection_reasons\":[\"原因\"]}]}"
    )


def _sanitize_review(task: dict, parsed: dict, known_roles: dict[str, set[str]],
                     min_confidence: float) -> dict:
    candidate_ids = {item["shot_id"] for item in task["candidates"]}
    selected = str(parsed.get("selected_shot_id") or "")
    raw_candidates = parsed.get("candidates") if isinstance(parsed.get("candidates"), list) else []
    by_id = {str(item.get("shot_id")): item for item in raw_candidates if isinstance(item, dict)}
    if selected not in candidate_ids:
        selected = max(task["candidates"], key=lambda item: item["matcher_score"])["shot_id"]
    selected_result = by_id.get(selected, {})
    requirements = task["intent"].get("hard_requirements") or {}
    required_roles = {_canonical_role(item) for item in requirements.get("characters", [])}
    observed_roles = {_canonical_role(item) for item in known_roles.get(selected, set())}
    character_match = required_roles.issubset(observed_roles)
    action_match = not requirements.get("actions") or selected_result.get("action_match") is True
    location_match = not requirements.get("locations") or selected_result.get("location_match") is True
    object_match = not requirements.get("objects") or selected_result.get("object_match") is True
    negative_match = not task["intent"].get("must_not_have") or selected_result.get("must_not_have_absent") is True
    confidence = max(0.0, min(1.0, float(parsed.get("confidence") or selected_result.get("score") or 0)))
    hard_met = character_match and action_match and location_match and object_match and negative_match
    needs_review = bool(parsed.get("needs_review")) or confidence < min_confidence or not hard_met
    return {
        "segment_id": task["segment_id"],
        "selected_shot_id": selected,
        "confidence": round(confidence, 4),
        "accepted": not needs_review,
        "needs_review": needs_review,
        "hard_requirements_met": hard_met,
        "checks": {
            "characters": character_match,
            "actions": action_match,
            "locations": location_match,
            "objects": object_match,
            "must_not_have_absent": negative_match,
        },
        "required_roles": sorted(required_roles),
        "identity_roles": sorted(observed_roles),
        "candidates": [by_id.get(item["shot_id"], {"shot_id": item["shot_id"]})
                       for item in task["candidates"]],
    }


def _run_candidate_visual_review_phase(
    folder: Path,
    segments: list[dict],
    scene_map: dict,
    matching: object,
    visual: object,
    *,
    model: str,
    cache_file: str = CACHE_FILE,
    candidates_per_segment: int | None = None,
    frames_per_candidate: int | None = None,
    only_segment_ids: set[str] | None = None,
    phase: str = "base",
) -> dict:
    folder = folder.resolve()
    candidate_limit = int(
        candidates_per_segment or getattr(matching, "candidate_review_candidates", 3)
    )
    frame_limit = int(frames_per_candidate or getattr(matching, "candidate_review_frames", 3))
    tasks = build_review_tasks(
        segments, scene_map,
        max_segments=int(getattr(matching, "candidate_review_max_segments", 12)),
        candidates_per_segment=candidate_limit,
        frames_per_candidate=frame_limit,
    )
    if only_segment_ids is not None:
        tasks = [item for item in tasks if item["segment_id"] in only_segment_ids]
    material = detect_materials(str(folder), max_videos=1)
    video = Path(material.video_path)
    signature = _signature(video, model, tasks, matching, visual)
    cache_path = folder / cache_file
    cached_reviews: list[dict] = []
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text("utf-8"))
            if cached.get("source_signature") == signature:
                if cached.get("status") == "complete":
                    cached["cache_hit"] = True
                    return cached
                cached_reviews = [item for item in cached.get("reviews", []) if isinstance(item, dict)]
            elif (
                cached.get("schema") == SCHEMA
                and cached.get("phase") == phase
                and cached.get("model") == model
                and int(cached.get("candidates_per_segment") or 0) == candidate_limit
                and int(cached.get("frames_per_candidate") or 0) == frame_limit
            ):
                current_tasks = {str(item["segment_id"]): item for item in tasks}
                cached_reviews = [
                    item for item in cached.get("reviews", [])
                    if isinstance(item, dict)
                    and str(item.get("segment_id")) in current_tasks
                    and _review_matches_task(
                        item, current_tasks[str(item.get("segment_id"))], frame_limit)
                ]
        except (OSError, ValueError, TypeError):
            pass
    started = time.perf_counter()
    completed_ids = {str(item.get("segment_id")) for item in cached_reviews}
    pending_tasks = [item for item in tasks if item["segment_id"] not in completed_ids]
    base = {"schema": SCHEMA, "phase": phase, "source_signature": signature, "model": model,
            "candidates_per_segment": candidate_limit, "frames_per_candidate": frame_limit,
            "task_count": len(tasks), "task_segment_ids": [item["segment_id"] for item in tasks],
            "cache_hit": False, "resumed_count": len(cached_reviews),
            "reviews": list(cached_reviews), "errors": []}
    if not tasks:
        result = {**base, "status": "complete", "success_count": 0, "failed_count": 0,
                  "elapsed_seconds": 0.0}
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        return result
    if not pending_tasks:
        result = {**base, "status": "complete", "success_count": len(tasks), "failed_count": 0,
                  "accepted_count": sum(1 for item in base["reviews"] if item.get("accepted")),
                  "unresolved_count": sum(1 for item in base["reviews"] if not item.get("accepted")),
                  "elapsed_seconds": 0.0}
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        return result
    api_key = dashscope_key()
    if not api_key:
        return {**base, "status": "unavailable", "reason": "missing_dashscope_api_key",
                "success_count": len(cached_reviews), "failed_count": len(pending_tasks),
                "elapsed_seconds": 0.0}

    with tempfile.TemporaryDirectory(prefix="autodsj_candidate_review_") as temp_value:
        temp = Path(temp_value)
        requested = [value for task in pending_tasks for candidate in task["candidates"] for value in candidate["times"]]
        samples = _extract_frames_at_times(
            video, temp, requested, "candidate", width=int(getattr(visual, "frame_width", 1280)),
            height=int(getattr(visual, "frame_height", 720)), jpeg_q=int(getattr(visual, "jpeg_q", 3)),
            workers=2,
        )
        by_time = {round(item.time, 3): item for item in samples}
        records: list[dict] = []
        task_images: dict[str, list[dict]] = {}
        for task in pending_tasks:
            images = []
            for candidate in task["candidates"]:
                for position, value in enumerate(candidate["times"], 1):
                    sample = by_time.get(round(value, 3))
                    if sample is None:
                        continue
                    frame_id = f"{task['segment_id']}:{candidate['shot_id']}:{position}"
                    record = {"frame_id": frame_id, "image_path": sample.path, "time": value,
                              "candidate_id": candidate["shot_id"], "position": position}
                    records.append(record)
                    images.append(record)
            task_images[task["segment_id"]] = images
        identity_map = _build_identity_map(folder, records, SimpleNamespace(visual=visual))
        for task in pending_tasks:
            for item in task_images[task["segment_id"]]:
                identities = identity_map.get(item["frame_id"], [])
                roles = {str(value.get("role")) for value in identities if value.get("role")}
                item["identity_roles"] = roles
                item["known_people"] = render_known_people(identities)
                item["label"] = (
                    f"candidate={item['candidate_id']}; position={item['position']}; "
                    f"time={_format_time(item['time'])}; 已知人物：{item['known_people'] or '无'}"
                )

        timeout = int(getattr(matching, "candidate_review_timeout_seconds", 240))
        min_confidence = float(getattr(matching, "candidate_review_min_confidence", 0.72))

        def review_one(task: dict) -> dict:
            images = task_images.get(task["segment_id"], [])
            expected = sum(len(item["times"]) for item in task["candidates"])
            if len(images) != expected:
                raise RuntimeError(f"候选帧提取不完整：{len(images)}/{expected}")
            limits = [len(images) // max(1, len(task["candidates"]))]
            if phase == "unresolved_escalation" and limits[0] > 5:
                limits.extend([5, 3])
            elif limits[0] > 2:
                limits.extend([2, 1])
            limits = list(dict.fromkeys(max(1, value) for value in limits))
            last_error: Exception | None = None
            for index, limit in enumerate(limits):
                cloud_images = _limit_images_per_candidate(images, limit)
                try:
                    parsed = retry_call(
                        lambda: _call_bailian_multimodal_json(
                            api_key, model, _prompt(task), cloud_images, timeout=timeout),
                        attempts=2,
                        base_delay=2.0, max_delay=12.0,
                    )
                    break
                except Exception as exc:  # bounded payload fallback for flaky multimodal uploads
                    last_error = exc
            else:
                raise last_error or RuntimeError("候选视觉复核请求失败")
            cloud_roles: dict[str, set[str]] = {}
            for item in cloud_images:
                cloud_roles.setdefault(item["candidate_id"], set()).update(item.get("identity_roles") or set())
            review = _sanitize_review(
                task, parsed, cloud_roles, min_confidence)
            review["review_phase"] = phase
            review["frames_per_candidate"] = len(task["candidates"][0].get("times", []))
            review["cloud_frames_per_candidate"] = max(
                (sum(1 for item in cloud_images if item["candidate_id"] == candidate["shot_id"])
                 for candidate in task["candidates"]), default=0)
            return review

        workers = max(1, min(2, int(getattr(matching, "candidate_review_workers", 2))))
        if workers == 1:
            for task in pending_tasks:
                try:
                    base["reviews"].append(review_one(task))
                except Exception as exc:  # noqa: BLE001
                    base["errors"].append({"segment_id": task["segment_id"], "error": str(exc)})
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(review_one, task): task for task in pending_tasks}
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        base["reviews"].append(future.result())
                    except Exception as exc:  # noqa: BLE001
                        base["errors"].append({"segment_id": task["segment_id"], "error": str(exc)})
    order = {task["segment_id"]: index for index, task in enumerate(tasks)}
    base["reviews"].sort(key=lambda item: order.get(item["segment_id"], 10**9))
    success = len(base["reviews"])
    result = {
        **base,
        "status": "complete" if success == len(tasks) else "partial",
        "success_count": success,
        "failed_count": len(tasks) - success,
        "accepted_count": sum(1 for item in base["reviews"] if item.get("accepted")),
        "unresolved_count": sum(1 for item in base["reviews"] if not item.get("accepted")) + len(base["errors"]),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result


def _merge_escalation(base: dict, escalation: dict) -> dict:
    reviews = {str(item.get("segment_id")): item for item in base.get("reviews", [])}
    reviews.update({str(item.get("segment_id")): item for item in escalation.get("reviews", [])})
    ordered = [reviews[str(segment_id)] for segment_id in base.get("task_segment_ids", [])
               if str(segment_id) in reviews]
    errors = list(escalation.get("errors", []))
    task_count = int(base.get("task_count") or len(base.get("task_segment_ids", [])))
    unresolved_ids = {str(item.get("segment_id")) for item in ordered if not item.get("accepted")}
    unresolved_ids.update(str(item.get("segment_id")) for item in errors)
    failed = int(escalation.get("failed_count") or 0)
    return {
        **base,
        "status": escalation.get("status"),
        "reviews": ordered,
        "errors": errors,
        "success_count": task_count - failed,
        "failed_count": failed,
        "accepted_count": sum(1 for item in ordered if item.get("accepted")),
        "unresolved_count": len(unresolved_ids),
        "elapsed_seconds": escalation.get("elapsed_seconds"),
        "base_elapsed_seconds": base.get("elapsed_seconds"),
        "cache_hit": bool(base.get("cache_hit")) and bool(escalation.get("cache_hit")),
        "escalation": {
            "enabled": True,
            "status": escalation.get("status"),
            "cache_file": ESCALATION_CACHE_FILE,
            "frame_count": escalation.get("frame_count"),
            "candidates_per_segment": escalation.get("candidates_per_segment"),
            "task_count": escalation.get("task_count"),
            "success_count": escalation.get("success_count"),
            "failed_count": escalation.get("failed_count"),
            "accepted_count": escalation.get("accepted_count"),
            "unresolved_count": escalation.get("unresolved_count"),
            "elapsed_seconds": escalation.get("elapsed_seconds"),
            "cache_hit": escalation.get("cache_hit"),
        },
    }


def run_candidate_visual_review(folder: Path, segments: list[dict], scene_map: dict,
                                matching: object, visual: object, *, model: str) -> dict:
    base = _run_candidate_visual_review_phase(
        folder, segments, scene_map, matching, visual, model=model)
    escalation_enabled = bool(getattr(matching, "use_candidate_review_escalation", False))
    escalation_frames = int(getattr(matching, "candidate_review_escalation_frames", 7))
    escalation_candidates = int(getattr(
        matching, "candidate_review_escalation_candidates",
        getattr(matching, "candidate_review_candidates", 3),
    ))
    base_frames = int(getattr(matching, "candidate_review_frames", 3))
    unresolved_ids = {str(item.get("segment_id")) for item in base.get("reviews", [])
                      if not item.get("accepted")}
    unresolved_ids.update(str(item.get("segment_id")) for item in base.get("errors", []))
    if (not escalation_enabled or base.get("status") != "complete"
            or escalation_frames <= base_frames or not unresolved_ids):
        return base
    escalation = _run_candidate_visual_review_phase(
        folder, segments, scene_map, matching, visual, model=model,
        cache_file=ESCALATION_CACHE_FILE,
        candidates_per_segment=escalation_candidates,
        frames_per_candidate=escalation_frames,
        only_segment_ids=unresolved_ids,
        phase="unresolved_escalation",
    )
    escalation["frame_count"] = escalation_frames
    return _merge_escalation(base, escalation)


def apply_candidate_reviews(segments: list[dict], payload: dict) -> dict:
    by_segment = {str(item.get("segment_id")): item for item in payload.get("reviews", [])}
    unresolved: list[str] = [str(item.get("segment_id")) for item in payload.get("errors", [])]
    unresolved.extend(str(item) for item in payload.get("task_segment_ids", [])
                      if str(item) not in by_segment)
    accepted = 0
    for segment in segments:
        review = by_segment.get(str(segment.get("segment_id")))
        if not review:
            continue
        segment["candidate_visual_review"] = review
        if not review.get("accepted"):
            unresolved.append(str(segment.get("segment_id")))
            continue
        planning = segment.get("_planning_candidates") or []
        selected_id = review.get("selected_shot_id")
        selected = next((item for item in planning if str(item.get("shot_id")) == selected_id), None)
        if selected is None:
            unresolved.append(str(segment.get("segment_id")))
            continue
        selected["score"] = max((float(item.get("score", 0)) for item in planning), default=0.0) + 2.0
        selected["candidate_visual_review_score"] = review.get("confidence")
        planning.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
        segment["clip_start"] = float(selected["shot_start"])
        segment["clip_end"] = float(selected["shot_end"])
        accepted += 1
    return {
        "status": payload.get("status"),
        "task_count": int(payload.get("task_count") or 0),
        "reviewed": len(by_segment),
        "accepted": accepted,
        "unresolved": len(set(unresolved)),
        "unresolved_segment_ids": sorted(set(unresolved)),
        "cache_hit": bool(payload.get("cache_hit")),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "escalation": payload.get("escalation"),
    }
