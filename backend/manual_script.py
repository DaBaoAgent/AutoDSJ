from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path

from .media import detect_materials
from .ad_filter import detect_ad_intervals, overlaps_ad
from .schemas import AppSettings
from .vision_api import parse_srt


SCRIPT_TABLE_FILE = "_drama_script_table.json"
SCRIPT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".rtf", ".docx"}


@dataclass
class SubtitleLine:
    index: int
    start: float
    end: float
    text: str
    clean: str


@dataclass
class ScriptBlock:
    row_type: str
    text: str
    clean: str


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _clean_for_match(text: str) -> str:
    text = re.sub(r"\s+", "", str(text or ""))
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text).lower()


def _spoken_text(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    joined = "，".join(line.rstrip("，。！？；：,.!?;:") for line in lines if line.strip())
    joined = re.sub(r"，+", "，", joined).strip("，")
    if joined and joined[-1] not in "。！？；":
        joined += "。"
    return joined


def _source_text(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return " ".join(lines)


def read_script_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".text"}:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".rtf":
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
        raw = re.sub(r"\\[a-zA-Z]+\d* ?", "", raw)
        raw = raw.replace("{", "").replace("}", "")
        return raw
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        return unescape(xml)
    raise ValueError(f"暂不支持的文案格式：{path.suffix}")


def find_manual_script_file(folder: Path) -> Path | None:
    candidates: list[Path] = []
    for ext in SCRIPT_EXTENSIONS:
        candidates.extend(folder.glob(f"*{ext}"))
    blocked = ("配音稿", "发布信息", "匹配报告")
    candidates = [
        path for path in candidates
        if not path.name.startswith("_")
        and not path.name.startswith("★")
        and not any(token in path.stem for token in blocked)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: (0 if "文案" in path.stem else 1, -path.stat().st_mtime))
    return candidates[0]


def parse_manual_script(text: str) -> list[ScriptBlock]:
    blocks: list[ScriptBlock] = []
    current_type = ""
    current_lines: list[str] = []
    label_re = re.compile(r"^\s*(原片|解说)\s*[:：]\s*(.*)$")

    def flush() -> None:
        nonlocal current_lines, current_type
        body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_type and body:
            row_type = "source_clip" if current_type == "原片" else "narration"
            blocks.append(ScriptBlock(row_type=row_type, text=body, clean=_clean_for_match(body)))
        current_lines = []

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = label_re.match(raw_line)
        if match:
            flush()
            current_type = match.group(1)
            tail = match.group(2).strip()
            current_lines = [tail] if tail else []
            continue
        if current_type:
            current_lines.append(raw_line)
    flush()

    if not blocks:
        raise ValueError("解说文案里没有识别到“原片：”或“解说：”标签")
    if not any(block.row_type == "source_clip" for block in blocks):
        raise ValueError("解说文案里没有“原片：”段落")
    if not any(block.row_type == "narration" for block in blocks):
        raise ValueError("解说文案里没有“解说：”段落")
    return blocks


def parse_srt_file(path: Path) -> list[SubtitleLine]:
    lines = [
        SubtitleLine(item.idx, item.start, item.end, item.text, _clean_for_match(item.text))
        for item in parse_srt(path)
        if _clean_for_match(item.text)
    ]
    if not lines:
        raise ValueError(f"字幕没有可解析内容：{path}")
    return lines


def _score_match(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if haystack in needle:
        return min(0.95, len(haystack) / max(1, len(needle)) + 0.25)
    return SequenceMatcher(None, needle, haystack).ratio()


def match_source_block(block: ScriptBlock, subtitles: list[SubtitleLine],
                       min_start: float = 0.0) -> dict:
    needle = block.clean
    best: dict | None = None
    max_window_seconds = 75.0
    max_extra_chars = max(16, round(len(needle) * 0.55))
    for i, sub in enumerate(subtitles):
        combined = ""
        end_index = i
        for j in range(i, len(subtitles)):
            if subtitles[j].end - sub.start > max_window_seconds:
                break
            combined += subtitles[j].clean
            end_index = j
            if len(combined) >= len(needle) + max_extra_chars:
                break
            if needle in combined:
                break
        score = _score_match(needle, combined)
        # Chronological cursor is a SOFT preference, not a hard gate: user scripts
        # often list 原片 clips out of episode order, so never exclude earlier
        # subtitles outright — only mildly favour matches at/after the cursor.
        if sub.start < min_start - 1:
            score -= 0.08
        candidate = {
            "start": subtitles[i].start,
            "end": subtitles[end_index].end,
            "text": " ".join(item.text for item in subtitles[i:end_index + 1]),
            "score": max(0.0, min(1.0, score)),
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    line_matches: list[tuple[int, int, float, str]] = []
    script_lines = [
        (line.strip(), _clean_for_match(line))
        for line in block.text.splitlines()
        if _clean_for_match(line)
    ]
    search_from = 0
    for line_text, line_clean in script_lines:
        local_best: tuple[int, int, float] | None = None
        for i in range(search_from, len(subtitles)):
            if line_matches and subtitles[i].start - subtitles[line_matches[0][0]].start > 95:
                break
            combined = ""
            end_index = i
            for j in range(i, min(len(subtitles), i + 3)):
                combined += subtitles[j].clean
                end_index = j
                score = _score_match(line_clean, combined)
                # Soft chronological preference (see match_source_block window loop):
                # penalise matches before the cursor instead of excluding them, so
                # out-of-order 原片 clips still resolve to their true position.
                if subtitles[i].start < min_start - 1:
                    score -= 0.08
                current_span = end_index - i
                best_span = local_best[1] - local_best[0] if local_best else 10_000
                if local_best is None or score > local_best[2] + 1e-6 or (
                    abs(score - local_best[2]) <= 1e-6
                    and (current_span < best_span or (current_span == best_span and i < local_best[0]))
                ):
                    local_best = (i, end_index, score)
                if score >= 0.98:
                    break
        if local_best and local_best[2] >= 0.48:
            line_matches.append((*local_best, line_text))
            search_from = local_best[1] + 1
    required_matches = max(2, round(len(script_lines) * 0.55)) if script_lines else 0
    if line_matches and len(line_matches) >= required_matches:
        intervals: list[dict] = []
        for first_i, last_j, score, requested_text in line_matches:
            part = {
                "start": subtitles[first_i].start,
                "end": subtitles[last_j].end,
                "text": " ".join(item.text for item in subtitles[first_i:last_j + 1]),
                "requested_text": requested_text,
                "score": score,
                "first_subtitle_index": first_i,
                "last_subtitle_index": last_j,
            }
            previous = intervals[-1] if intervals else None
            if (
                previous
                and first_i == int(previous["last_subtitle_index"]) + 1
                and part["start"] - float(previous["end"]) <= 1.2
            ):
                previous["end"] = part["end"]
                previous["text"] = f"{previous['text']} {part['text']}".strip()
                previous["requested_text"] = (
                    f"{previous['requested_text']} {part['requested_text']}"
                ).strip()
                previous["score"] = min(float(previous["score"]), score)
                previous["last_subtitle_index"] = last_j
            else:
                intervals.append(part)
        line_score = sum(item[2] for item in line_matches) / max(1, len(script_lines))
        coverage_bonus = len(line_matches) / max(1, len(script_lines)) * 0.2
        return {
            "start": float(intervals[0]["start"]),
            "end": float(intervals[-1]["end"]),
            "text": " ".join(str(item["text"]) for item in intervals),
            "score": max(0.0, min(1.0, line_score + coverage_bonus)),
            "intervals": intervals,
            "match_mode": "exact_script_lines",
        }
    if best is None:
        fallback = next((sub for sub in subtitles if sub.start >= min_start), subtitles[-1])
        best = {"start": fallback.start, "end": fallback.end, "text": fallback.text, "score": 0.0}
    best["intervals"] = [{
        "start": best["start"],
        "end": best["end"],
        "text": best["text"],
        "requested_text": block.text.replace("\n", " "),
        "score": best["score"],
    }]
    best["match_mode"] = "fallback_window"
    return best


def _visual_intent_for_range(folder: Path, start: float, end: float, fallback: str) -> str:
    candidates_path = folder / "_source_clip_candidates.json"
    if candidates_path.exists():
        try:
            payload = json.loads(candidates_path.read_text("utf-8"))
            candidates = payload.get("candidates", [])
            overlap = [
                item for item in candidates
                if float(item.get("end", 0)) > start and float(item.get("start", 0)) < end
            ]
            if overlap:
                overlap.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
                captions = overlap[0].get("visual_captions", []) or []
                if captions:
                    return "；".join(str(x) for x in captions[:2])
        except Exception:
            pass
    return fallback[:60] or "按用户文案匹配的原片画面"


def generate_manual_script_table(settings: AppSettings, script_path: str | None = None) -> dict:
    folder = Path(settings.material_folder.strip().strip('"')).expanduser()
    if not folder.is_dir():
        raise ValueError(f"素材文件夹不存在：{folder}")
    media = detect_materials(str(folder), settings.drama.source_count)
    script_file = Path(script_path).expanduser() if script_path else find_manual_script_file(folder)
    if not script_file or not script_file.exists():
        raise ValueError("没有找到解说文案文件，请上传 txt/md/docx 等文案")
    suffix = script_file.suffix.lower()
    if suffix not in SCRIPT_EXTENSIONS:
        raise ValueError(f"解说文案格式不支持：{script_file.name}")

    subtitles = parse_srt_file(Path(media.subtitle_paths[0]))
    ad_intervals = detect_ad_intervals(folder)
    subtitles = [line for line in subtitles if not overlaps_ad(line.start, line.end, ad_intervals)]
    blocks = parse_manual_script(read_script_document(script_file))
    rows: list[dict] = []
    row_id = 1
    last_source_end = float(settings.video.trim_head)
    source_block_count = 0
    source_clip_count = 0
    narration_count = 0
    previous_match: dict | None = None

    for block in blocks:
        if block.row_type == "source_clip":
            match = match_source_block(block, subtitles, last_source_end)
            source_block_count += 1
            intervals = match.get("intervals") or [match]
            for part_index, part in enumerate(intervals, 1):
                start = max(float(settings.video.trim_head), float(part["start"]))
                end = max(start + 0.2, float(part["end"]))
                source_clip_count += 1
                text = str(part.get("requested_text") or part.get("text") or _source_text(block.text))
                label = f"原片 {source_block_count}"
                if len(intervals) > 1:
                    label += f"-{part_index}"
                rows.append({
                    "row_id": row_id,
                    "row_type": "source_clip",
                    "insert_role": "manual_source",
                    "insert_role_label": label,
                    "text": f"原片对白：{text}",
                    "matched_clip_id": source_clip_count,
                    "source_block_id": source_block_count,
                    "source_part_index": part_index,
                    "source_part_count": len(intervals),
                    "source_index": 1,
                    "source_file": Path(media.video_path).name,
                    "source_start": round(start, 3),
                    "source_end": round(end, 3),
                    "duration": round(end - start, 3),
                    "use_exact_duration": True,
                    "source_time_text": f"{_format_time(start)} - {_format_time(end)}",
                    "source_audio_mode": "keep_dialogue",
                    "visual_intent": _visual_intent_for_range(folder, start, end, text),
                    "match_score": round(float(part.get("score", match["score"])), 3),
                    "match_reason": "逐行匹配用户文案，只播放命中的原片对白区间",
                    "locked": True,
                    "alternatives": [],
                })
                row_id += 1
            start = float(intervals[0]["start"])
            end = float(intervals[-1]["end"])
            previous_match = {
                "start": start, "end": end, "text": _source_text(block.text), "score": match["score"]
            }
            last_source_end = max(last_source_end, end)
        else:
            narration_count += 1
            text = _spoken_text(block.text)
            if not text:
                continue
            start = float(previous_match["start"]) if previous_match else float(settings.video.trim_head)
            end = float(previous_match["end"]) if previous_match else start + 3.0
            rows.append({
                "row_id": row_id,
                "row_type": "narration",
                "insert_role": "manual_narration",
                "insert_role_label": f"解说 {narration_count}",
                "text": text,
                "matched_clip_id": narration_count,
                "source_index": 1,
                "source_file": Path(media.video_path).name,
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "source_time_text": f"{_format_time(start)} - {_format_time(end)}",
                "source_audio_mode": "narration_only",
                "visual_intent": _visual_intent_for_range(folder, start, end, text),
                "match_score": round(float(previous_match["score"]) if previous_match else 0.0, 3),
                "match_reason": "按用户文案“解说：”段落生成配音，不包含标签文字",
                "locked": True,
                "alternatives": [],
            })
            row_id += 1

    if source_block_count == 0 or narration_count == 0:
        raise ValueError("文案必须同时包含“原片：”和“解说：”段落")

    usable_end = max(float(settings.video.trim_head), float(media.duration) - float(settings.video.trim_tail))
    trim_head = float(settings.video.trim_head)
    scene_back, scene_forward = 8.0, 80.0
    for index, row in enumerate(rows):
        if row["row_type"] != "narration":
            continue
        previous_source = next(
            (item for item in reversed(rows[:index]) if item["row_type"] == "source_clip"), None
        )
        next_source = next(
            (item for item in rows[index + 1:] if item["row_type"] == "source_clip"), None
        )
        # Anchor the narration's B-roll to the scene of its adjacent quoted clip
        # (the 原片 it expands on). Source clips are NOT chronological, so the old
        # "gap between prev and next clip" window is meaningless — we key off the
        # actual scene time instead, so 解说 about 谁/做什么 pulls that scene.
        anchor = previous_source or next_source
        if anchor is not None:
            anchor_start = float(anchor["source_start"])
            anchor_end = float(anchor["source_end"])
        else:
            anchor_start = max(trim_head, float(row["source_start"]))
            anchor_end = max(anchor_start + 2.0, float(row["source_end"]))
        preferred_start = max(trim_head, anchor_start - scene_back)
        preferred_end = min(usable_end, anchor_end + scene_forward)
        # Don't bleed into the next quoted scene when it sits just ahead of us.
        if previous_source is not None and next_source is not None:
            next_start = float(next_source["source_start"])
            if anchor_end < next_start < preferred_end:
                preferred_end = next_start
        if preferred_end - preferred_start < 6.0:
            preferred_end = min(usable_end, preferred_start + 30.0)
        row["source_start"] = round(preferred_start, 3)
        row["source_end"] = round(preferred_end, 3)
        row["source_time_text"] = f"{_format_time(preferred_start)} - {_format_time(preferred_end)}"
        row["visual_intent"] = row["text"]
        row["match_score"] = 0.0
        row["match_reason"] = "锚定相邻原片所在剧情场景，按人物/动作/场景精准匹配画面；全局禁止复用"

    narration_text = "\n".join(row["text"] for row in rows if row["row_type"] == "narration")
    payload = {
        "ok": True,
        "script_source": "manual_upload",
        "folder": str(folder.resolve()),
        "script_file": str(script_file.resolve()),
        "row_count": len(rows),
        "source_clip_count": source_clip_count,
        "source_block_count": source_block_count,
        "narration_count": narration_count,
        "narration_text": narration_text,
        "ad_exclusion_count": len(ad_intervals),
        "ad_exclusions": ad_intervals,
        "rows": rows,
        "validation": {
            "video_ok": bool(media.video_path),
            "srt_ok": bool(media.subtitle_paths),
            "script_ok": True,
            "source_blocks": source_block_count,
            "source_clips": source_clip_count,
            "narration_blocks": narration_count,
            "low_match_rows": [
                row["row_id"] for row in rows
                if row["row_type"] == "source_clip" and float(row.get("match_score", 0)) < 0.45
            ],
        },
        "generated_file": str((folder / SCRIPT_TABLE_FILE).resolve()),
    }
    (folder / SCRIPT_TABLE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        "utf-8",
    )
    (folder / "配音稿.txt").write_text(narration_text, "utf-8")
    return payload
