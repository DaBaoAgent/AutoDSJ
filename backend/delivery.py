from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .manual_script import (
    SCRIPT_TABLE_FILE,
    find_manual_script_file,
    parse_manual_script,
    read_script_document,
)
from .media_tools import ffprobe
from .schemas import AppSettings
from .vision_api import parse_srt


VIDEO_FILE = "★ 成片.mp4"
SUBTITLE_FILE = "★ 字幕.srt"
MATCH_REPORT_FILE = "★ 匹配报告.json"
PUBLISH_FILE = "★ 发布信息.txt"
JIANYING_FILE = "★ 剪映字幕导入.txt"
DELIVERY_REPORT_FILE = "★ 交付清单.json"


def _atomic_write(path: Path, content: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def _script_path(folder: Path) -> Path:
    table_path = folder / SCRIPT_TABLE_FILE
    if table_path.exists():
        try:
            payload = json.loads(table_path.read_text(encoding="utf-8"))
            raw = str(payload.get("script_file") or "").strip()
            candidate = Path(raw)
            if raw and not candidate.is_absolute():
                candidate = folder / candidate
            if raw and candidate.is_file():
                return candidate
        except (OSError, json.JSONDecodeError):
            pass
    path = find_manual_script_file(folder)
    if path is None:
        raise RuntimeError("发布交付失败：找不到原片/解说文案")
    return path


def build_jianying_lines(script_text: str) -> list[str]:
    """Remove 原片/解说 labels while preserving the manuscript's line order."""
    output: list[str] = []
    label_re = re.compile(r"^\s*(原片|解说)\s*[:：]\s*(.*)$")
    for raw in script_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        match = label_re.match(line)
        if match:
            tail = match.group(2).strip()
            if tail:
                output.append(tail)
            continue
        output.append(line)
    if not output:
        raise RuntimeError("发布交付失败：文案中没有可导入剪映的字幕文本")
    return output


def _shorten(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", "", text).strip("，。！？；：,.!?;: ")
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。！？!?；;]+", text) if item.strip()]


def _series_info(folder: Path) -> tuple[str, str]:
    match = re.search(r"(.+?)\s*第\s*(\d+)\s*集", folder.name)
    if match:
        series = match.group(1).strip() or folder.parent.name
        return series, match.group(2)
    return folder.parent.name or folder.name, ""


def build_publish_text(folder: Path, narration_texts: list[str], video_meta: dict,
                       settings: AppSettings) -> str:
    if not narration_texts:
        raise RuntimeError("发布交付失败：文案中没有解说段")
    series, episode = _series_info(folder)
    all_text = "".join(narration_texts)
    first = _sentences(narration_texts[0]) or [narration_texts[0]]
    last = _sentences(narration_texts[-1]) or [narration_texts[-1]]
    hook = _shorten(first[0], 30)
    ending = _shorten(last[-1], 30)
    conflict = next(
        (_shorten(sentence, 30) for text in narration_texts for sentence in _sentences(text)
         if any(word in sentence for word in ("没想到", "可是", "却", "直到", "原来", "谎"))),
        _shorten(first[-1], 30),
    )
    episode_label = f"第{episode}集" if episode else ""

    if "异地恋" in all_text and any(word in all_text for word in ("隐瞒", "谎言", "谎话", "撒谎")):
        titles = [
            f"异地恋最怕的不是距离，而是一次次隐瞒｜{series}{episode_label}",
            f"一个要安全感，一个想喘息：玫瑰和庄国栋为什么越爱越累｜{series}",
            f"隔着屏幕，谁都无法真正抱住对方｜{series}{episode_label}",
        ]
        cover_main, cover_sub = "隐瞒就是裂缝", "异地恋最怕的从来不是距离"
        question = "异地恋里，隐瞒行踪和过度追问，究竟哪一个更伤感情？"
    else:
        titles = [
            f"{hook}｜{series}{episode_label}",
            f"{conflict}｜{series}{episode_label}",
            f"{ending}｜{series}",
        ]
        cover_main, cover_sub = _shorten(hook, 12), _shorten(conflict, 18)
        question = "如果是你，面对这样的选择会怎么做？"

    intro = _shorten("，".join(first[:2]), 90)
    close = _shorten(last[-1], 70)
    safe_series = re.sub(r"[^\w\u4e00-\u9fff]", "", series)
    tags = [f"#{safe_series}", f"#第{episode}集" if episode else "#电视剧解说",
            "#电视剧解说", "#影视解说", "#情感成长"]
    tags = list(dict.fromkeys(tags))
    while len(tags) < 5:
        tags.append("#剧情解说")

    return (
        f"《{series}》{episode_label} 发布信息\n\n"
        "标题（三选一）\n"
        + "\n".join(f"{idx}. {title}" for idx, title in enumerate(titles, 1))
        + f"\n\n剧情简介\n{intro}。{close}。\n{question}\n\n"
        + f"话题标签（5个）\n{' '.join(tags[:5])}\n\n"
        + f"封面文案\n主标题：{cover_main}\n副标题：{cover_sub}\n\n"
        + "成片信息\n"
        + f"时长：{video_meta['duration']:.1f} 秒\n"
        + f"画面：{video_meta['width']}×{video_meta['height']}\n"
        + f"音频：原片 {settings.drama.source_play_volume}% / 配音 {settings.voice.volume}%（统一等响）\n"
    )


def probe_video(path: Path) -> dict:
    result = subprocess.run(
        [ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    payload = json.loads(result.stdout)
    video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("发布交付失败：成片没有视频流")
    duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("发布交付失败：无法读取成片时长")
    return {"duration": duration, "width": int(video.get("width") or 0), "height": int(video.get("height") or 0)}


def run_delivery(settings: AppSettings, output: Path | None = None) -> dict:
    folder = Path(settings.material_folder)
    video_path = output or folder / VIDEO_FILE
    subtitle_path = folder / SUBTITLE_FILE
    match_path = folder / MATCH_REPORT_FILE
    missing = [str(path.name) for path in (video_path, subtitle_path, match_path) if not path.is_file()]
    if missing:
        raise RuntimeError(f"发布交付失败：缺少 {', '.join(missing)}")

    video_meta = probe_video(video_path)
    subtitles = parse_srt(subtitle_path)
    if max(item.end for item in subtitles) > video_meta["duration"] + 0.5:
        raise RuntimeError("发布交付失败：SRT 末尾时间超过成片时长")

    script_path = _script_path(folder)
    script_text = read_script_document(script_path)
    blocks = parse_manual_script(script_text)
    narration_texts = [block.text for block in blocks if block.row_type == "narration"]
    jianying_lines = build_jianying_lines(script_text)
    publish_text = build_publish_text(folder, narration_texts, video_meta, settings)

    _atomic_write(folder / JIANYING_FILE, "\n".join(jianying_lines) + "\n")
    _atomic_write(folder / PUBLISH_FILE, publish_text)

    pronoun_lines = [line for line in jianying_lines if re.search(r"[他她]", line)]
    report = {
        "status": "ready",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_file": str(script_path),
        "video": {"file": str(video_path), **video_meta},
        "subtitle": {"file": str(subtitle_path), "entry_count": len(subtitles),
                     "last_end": round(max(item.end for item in subtitles), 3)},
        "jianying": {"file": str(folder / JIANYING_FILE), "line_count": len(jianying_lines)},
        "publish": {"file": str(folder / PUBLISH_FILE), "title_count": 3, "tag_count": 5},
        "matching_report": str(match_path),
        "pronoun_review_line_count": len(pronoun_lines),
    }
    _atomic_write(folder / DELIVERY_REPORT_FILE, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
