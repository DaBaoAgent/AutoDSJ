from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .ad_filter import detect_ad_intervals
from .media import detect_materials
from .narration_intent import ACTION_VOCAB, CHARACTERS
from .shot_index import build_shot_index
from .vision_api import parse_srt
from .visual_matcher import split_visual_clauses


SCENE_DRAFT_FILE = "_scene_map.draft.json"
_STOP_BIGRAMS = {
    "什么", "怎么", "这个", "那个", "一个", "我们", "你们", "他们", "自己", "就是",
    "还是", "已经", "没有", "不是", "可以", "知道", "觉得", "因为", "所以", "但是",
    "然后", "现在", "真的", "这里", "那里", "时候", "这样", "一下", "这么", "那么",
}


def _atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(path)


def _subtract_ranges(left: float, right: float, excluded: list[tuple[float, float]]) -> list[list[float]]:
    cursor = left
    result: list[list[float]] = []
    for start, end in sorted(excluded):
        start, end = max(left, start), min(right, end)
        if end <= cursor or start >= right:
            continue
        if start > cursor + 0.25:
            result.append([round(cursor, 3), round(start, 3)])
        cursor = max(cursor, end)
    if cursor < right - 0.25:
        result.append([round(cursor, 3), round(right, 3)])
    return result


def _cue_gap(boundary: float, cues: list) -> float:
    previous = max((cue.end for cue in cues if cue.end <= boundary), default=boundary)
    following = min((cue.start for cue in cues if cue.start >= boundary), default=boundary)
    return max(0.0, following - previous)


def _text_window(boundary: float, cues: list, left: float, right: float) -> str:
    return "".join(cue.text for cue in cues if cue.end >= boundary + left and cue.start <= boundary + right)


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)
    return {clean[index:index + 2] for index in range(max(0, len(clean) - 1))}


def _semantic_change(boundary: float, cues: list) -> float:
    before = _bigrams(_text_window(boundary, cues, -45.0, -0.1))
    after = _bigrams(_text_window(boundary, cues, 0.1, 45.0))
    if not before or not after:
        return 0.35
    overlap = len(before & after) / max(1, len(before | after))
    return 1.0 - overlap


def _choose_boundaries(start: float, end: float, shots: list[dict], cues: list,
                       *, target: float = 120.0, minimum: float = 45.0,
                       maximum: float = 210.0) -> list[float]:
    if end - start <= maximum:
        return [start, end]
    shot_starts = sorted(
        float(shot["start"]) for shot in shots
        if start + minimum <= float(shot["start"]) <= end - minimum
    )
    edges = [start]
    cursor = start
    while end - cursor > maximum:
        candidates = [value for value in shot_starts if cursor + minimum <= value <= cursor + maximum]
        if not candidates:
            chosen = min(end, cursor + target)
        else:
            def score(value: float) -> float:
                proximity = 1.0 - min(1.0, abs((value - cursor) - target) / max(target, 1.0))
                silence = min(1.0, _cue_gap(value, cues) / 4.0)
                change = _semantic_change(value, cues)
                return 0.45 * proximity + 0.30 * silence + 0.25 * change

            chosen = max(candidates, key=lambda value: (score(value), -abs(value - cursor - target)))
        if chosen <= cursor + 0.25:
            break
        edges.append(round(chosen, 3))
        cursor = chosen
    edges.append(end)
    return edges


def _keywords(text: str, limit: int = 5) -> list[str]:
    explicit = [name for name in CHARACTERS if name in text]
    explicit.extend(action for action, aliases in ACTION_VOCAB.items()
                    if action in text or any(term in text for term in aliases.split()))
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)
    counts = Counter(clean[index:index + 2] for index in range(max(0, len(clean) - 1)))
    frequent = [token for token, _ in counts.most_common(30)
                if token not in _STOP_BIGRAMS and not token.isdigit()]
    return list(dict.fromkeys([*explicit, *frequent]))[:limit]


def _scene_for_time(value: float, scenes: list[dict]) -> dict:
    for scene in scenes:
        if any(float(left) <= value <= float(right) for left, right in scene.get("ranges", [])):
            return scene
    return min(
        scenes,
        key=lambda scene: min(
            abs(value - (float(left) + float(right)) / 2)
            for left, right in scene.get("ranges", [[0.0, 0.0]])
        ),
    )


def _parent_plans(script_table: dict, scenes: list[dict]) -> dict[str, list[dict]]:
    plans: dict[str, list[dict]] = {}
    parent_id = 0
    rows = script_table.get("rows", [])
    for row_index, row in enumerate(rows):
        if row.get("row_type") != "narration":
            continue
        parent_id += 1
        clauses = split_visual_clauses(str(row.get("text") or "")) or [str(row.get("text") or "")]
        previous_source = next(
            (item for item in reversed(rows[:row_index]) if item.get("row_type") == "source_clip"), None
        )
        next_source = next(
            (item for item in rows[row_index + 1:] if item.get("row_type") == "source_clip"), None
        )
        anchor = previous_source or next_source or row
        midpoint = (float(anchor.get("source_start", 0)) + float(anchor.get("source_end", 0))) / 2
        scene = _scene_for_time(midpoint, scenes)
        plans[str(parent_id)] = [{
            "from_shot": 1,
            "to_shot": max(1, len(clauses)),
            "scene": scene["name"],
        }]
    return plans


def build_scene_map_draft(folder: Path, settings, *, shot_index: dict | None = None,
                          script_table: dict | None = None, force: bool = False) -> dict:
    """Build a complete but explicitly unreviewed macro-scene map proposal.

    The draft never overwrites the formal map and never sets coverage_reviewed.
    Formal matching therefore keeps the existing human quality gate.
    """
    folder = Path(folder).resolve()
    output = folder / SCENE_DRAFT_FILE
    if output.exists() and not force:
        try:
            return json.loads(output.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            pass

    media = detect_materials(str(folder), settings.drama.source_count)
    shot_index = shot_index or build_shot_index(folder, force=False)
    cues = parse_srt(Path(media.subtitle_paths[0])) if media.subtitle_paths else []
    usable_start = float(settings.video.trim_head)
    usable_end = max(usable_start, float(media.duration) - float(settings.video.trim_tail))
    ads = detect_ad_intervals(folder)
    excluded = [(0.0, usable_start), (usable_end, float(media.duration))]
    excluded.extend((float(item["start"]), float(item["end"])) for item in ads)
    coverage = _subtract_ranges(usable_start, usable_end, excluded)

    scenes: list[dict] = []
    shots = shot_index.get("shots", [])
    for coverage_start, coverage_end in coverage:
        edges = _choose_boundaries(float(coverage_start), float(coverage_end), shots, cues)
        for left, right in zip(edges, edges[1:]):
            body = " ".join(cue.text for cue in cues if cue.end >= left and cue.start <= right)
            keywords = _keywords(body)
            scene_no = len(scenes) + 1
            label = "-".join(keywords[:2]) if keywords else f"{left:.0f}s"
            scenes.append({
                "name": f"场景-{scene_no:03d}｜{label}",
                "ranges": [[round(left, 3), round(right, 3)]],
                "characters": [name for name in CHARACTERS if name in body],
                "keywords": keywords,
                "draft_evidence": {
                    "subtitle_chars": len(re.sub(r"\s+", "", body)),
                    "physical_shots": sum(
                        1 for shot in shots
                        if float(shot["end"]) > left and float(shot["start"]) < right
                    ),
                },
            })
    if not scenes:
        raise RuntimeError("无法生成场景图草案：正片可用范围为空")

    script_table = script_table or {}
    payload = {
        "version": 5,
        "draft": True,
        "coverage_reviewed": False,
        "review_required": True,
        "method": "subtitle-change+silence+physical-shot-boundaries",
        "coverage_ranges": coverage,
        "excluded_ranges": [[round(left, 3), round(right, 3)] for left, right in sorted(excluded)
                            if right > left],
        "scene_count": len(scenes),
        "scenes": scenes,
        "parent_scene_plans": _parent_plans(script_table, scenes),
        "overrides": [],
        "review_notes": [
            "逐段确认场景边界、名称、人物和关键词。",
            "确认广告区间后，将 coverage_reviewed 改为 true 并另存为 _scene_map.json。",
            "正式管线不会自动接管本草案。",
        ],
    }
    _atomic_json(output, payload)
    return payload
