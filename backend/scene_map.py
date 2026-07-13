from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCENE_MAP_FILE = "_scene_map.json"


def scene_map_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fail(message: str) -> None:
    raise RuntimeError(f"场景地图门禁失败：{message}")


def validate_scene_map(folder: Path, narration_segments: list[dict] | None = None) -> dict:
    """Validate the mandatory, fully reviewed macro-scene map."""
    path = Path(folder) / SCENE_MAP_FILE
    if not path.is_file():
        _fail(f"缺少 {SCENE_MAP_FILE}；禁止匹配或渲染")
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"{SCENE_MAP_FILE} 不是有效 JSON：{exc}")
    if payload.get("coverage_reviewed") is not True:
        _fail("coverage_reviewed 必须为 true，表示已人工核对完整原片时间轴")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        _fail("scenes 不能为空")
    if int(payload.get("scene_count", -1)) != len(scenes):
        _fail(f"scene_count={payload.get('scene_count')} 与实际 scenes={len(scenes)} 不一致")
    names = [str(item.get("name") or "").strip() for item in scenes]
    if any(not name for name in names) or len(set(names)) != len(names):
        _fail("场景名称不能为空或重复")

    all_ranges: list[tuple[float, float]] = []
    for scene in scenes:
        ranges = scene.get("ranges") or []
        if not ranges:
            _fail(f"场景“{scene.get('name')}”缺少 ranges")
        for value in ranges:
            if not isinstance(value, list) or len(value) != 2:
                _fail(f"场景“{scene.get('name')}”存在非法 range：{value}")
            left, right = map(float, value)
            if left < 0 or right <= left:
                _fail(f"场景“{scene.get('name')}”存在非法 range：{value}")
            all_ranges.append((left, right))

    coverage = payload.get("coverage_ranges") or []
    if not coverage:
        _fail("缺少 coverage_ranges，无法证明完整原片已分场景")
    merged = sorted(all_ranges)
    for value in coverage:
        if not isinstance(value, list) or len(value) != 2:
            _fail(f"非法 coverage range：{value}")
        left, right = map(float, value)
        cursor = left
        for start, end in merged:
            if end <= cursor or start >= right:
                continue
            if start > cursor + 0.25:
                _fail(f"完整覆盖存在空洞：{cursor:.3f}-{start:.3f}s")
            cursor = max(cursor, min(end, right))
            if cursor >= right - 0.25:
                break
        if cursor < right - 0.25:
            _fail(f"完整覆盖不足：{cursor:.3f}-{right:.3f}s 未划入大场景")

    plans = payload.get("parent_scene_plans")
    if not isinstance(plans, dict) or not plans:
        _fail("缺少 parent_scene_plans（整段主场景→尾句承上启下硬约束）")
    for parent, groups in plans.items():
        if not isinstance(groups, list) or not 1 <= len(groups) <= 2:
            _fail(f"解说段 {parent} 只能有 1 个主场景，或“主场景→尾句承接场景”2 组")
        ordered = sorted(groups, key=lambda item: int(item.get("from_shot", 0)))
        if ordered != groups or int(ordered[0].get("from_shot", 0)) != 1:
            _fail(f"解说段 {parent} 的场景计划必须从镜头 1 连续开始")
        for index, group in enumerate(ordered):
            left = int(group.get("from_shot", 0))
            right = int(group.get("to_shot", -1))
            if right < left or group.get("scene") not in names:
                _fail(f"解说段 {parent} 存在非法场景组：{group}")
            if index and left != int(ordered[index - 1].get("to_shot", -1)) + 1:
                _fail(f"解说段 {parent} 的场景组不连续")
        if len(ordered) == 2:
            main_len = int(ordered[0]["to_shot"]) - int(ordered[0]["from_shot"]) + 1
            tail_len = int(ordered[1]["to_shot"]) - int(ordered[1]["from_shot"]) + 1
            if ordered[0]["scene"] == ordered[1]["scene"] or tail_len >= main_len:
                _fail(f"解说段 {parent} 的第二场景必须是更短的尾句承接场景")
    if narration_segments is not None:
        for segment in narration_segments:
            if segment.get("row_type") != "narration":
                continue
            parent = segment.get("tts_parent_id") or segment.get("script_row_id")
            shot = int(segment.get("shot_index") or 0)
            groups = plans.get(str(parent), [])
            hits = [group for group in groups
                    if int(group.get("from_shot", 0)) <= shot <= int(group.get("to_shot", -1))]
            if len(hits) != 1:
                _fail(f"解说段 {parent} 镜头 {shot} 必须且只能命中一个段落场景计划")
            if hits[0].get("scene") not in names:
                _fail(f"解说段 {parent} 引用了不存在的场景：{hits[0].get('scene')}")
    payload["sha256"] = scene_map_digest(path)
    return payload
