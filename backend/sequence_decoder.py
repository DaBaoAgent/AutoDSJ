from __future__ import annotations

import re
from collections import defaultdict


def _event_number(value: object) -> int | None:
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else None


def _transition(previous: dict, current: dict) -> float:
    prev_event, event = previous.get("event_id"), current.get("event_id")
    prev_start, start = float(previous.get("shot_start", 0)), float(current.get("shot_start", 0))
    score = 0.0
    if prev_event == event:
        score += 0.42
    else:
        left, right = _event_number(prev_event), _event_number(event)
        if left is not None and right is not None:
            distance = right - left
            if distance == 1:
                score += 0.18
            elif distance < 0:
                score -= 0.72 + min(0.45, abs(distance) * 0.06)
            else:
                score -= min(0.38, max(0, distance - 1) * 0.055)
    if start + 0.15 >= prev_start:
        score += 0.13
    else:
        score -= 0.52 + min(0.35, (prev_start - start) / 60.0)
    if current.get("scene") and previous.get("scene") and current.get("scene") != previous.get("scene"):
        score -= 1.2
    return score


def decode_parent_sequences(segments: list[dict], *, max_candidates: int = 14) -> dict:
    """Viterbi decode all clauses in a continuity group as one sequence."""
    groups: dict[object, list[dict]] = defaultdict(list)
    for segment in segments:
        key = segment.get("continuity_group_id") or segment.get("tts_parent_id") or segment.get("script_row_id")
        groups[key].append(segment)
    decoded = 0
    for key, group in groups.items():
        layers = [list(item.get("_planning_candidates", []))[:max_candidates] for item in group]
        if not layers or any(not layer for layer in layers):
            continue
        scores = [float(item.get("score", 0)) for item in layers[0]]
        backrefs: list[list[int]] = []
        for layer_index in range(1, len(layers)):
            previous_layer, layer = layers[layer_index - 1], layers[layer_index]
            next_scores, refs = [], []
            for candidate in layer:
                options = [scores[i] + _transition(prev, candidate) for i, prev in enumerate(previous_layer)]
                best = max(range(len(options)), key=options.__getitem__)
                next_scores.append(options[best] + float(candidate.get("score", 0)))
                refs.append(best)
            scores = next_scores
            backrefs.append(refs)
        best_final_index = max(range(len(scores)), key=scores.__getitem__)
        best_path_score = float(scores[best_final_index])
        path = [best_final_index]
        for refs in reversed(backrefs):
            path.append(refs[path[-1]])
        path.reverse()
        for segment, layer, selected_index in zip(group, layers, path):
            selected = layer[selected_index]
            selected["sequence_selected"] = True
            selected["sequence_group"] = str(key)
            segment["sequence_event_id"] = selected.get("event_id")
            segment["sequence_shot_id"] = selected.get("shot_id")
            segment["sequence_score"] = round(best_path_score / max(1, len(group)), 4)
        decoded += 1
    return {"decoded_groups": decoded, "total_groups": len(groups)}
