from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


VISUAL_INDEX_FILE = "_source_visual_index.json"


@dataclass(frozen=True)
class VisualFrame:
    time: float
    text: str
    evidence: str


_CONCEPT_ALIASES = {
    "周世辉": "周世辉 西装男 西装男子 白西装 浅灰西装 戴眼镜男 持花男 花束",
    "玫瑰": "玫瑰 年轻女子 女生 画室 美术史",
    "关芝芝": "关芝芝 白裙女 格子西装女 未婚妻 行李箱",
    "黄振华": "黄振华 男子 戴眼镜男 哥哥",
    "黄妈妈": "黄妈妈 母亲 妈妈 中年女性 家中",
    "姜雪琼": "姜雪琼 老板 面试官 成熟女性 二面",
    "单位": "单位 公司 办公室 办公场所 工作室 仓库",
    "学校": "学校 校园 教室 走廊 楼梯 史论系",
    "跑去": "跑去 赶去 奔跑 行走 乘扶梯 询问教室",
    "回到": "回到 进入 站立 行走",
    "结婚": "结婚 婚礼 西装 花束 囍字",
    "西装": "西装 白西装 浅灰西装 正装",
    "面试": "面试 面试官 公司 办公室 丝巾 口红 台球",
    "丝巾": "丝巾 方巾 围巾 布料",
    "手机": "手机 电话 通话",
    "搬空": "搬空 搬家 行李箱 纸箱 绳索",
    "辞职": "辞职 离开 收拾 行走",
}

_STOPWORDS = {
    "一个", "自己", "已经", "就是", "还是", "没有", "根本", "这次", "这一", "那条",
    "因为", "所以", "可是", "偏偏", "终于", "很快", "开始", "面对", "真正", "从此",
    "觉得", "以为", "如果", "只要", "怎么", "什么", "完全", "直接", "当场", "一句话",
}


def split_visual_clauses(text: str, min_chars: int = 10, max_chars: int = 34) -> list[str]:
    clean = re.sub(r"\s+", "", str(text or "")).strip()
    if not clean:
        return []
    raw = [part.strip() for part in re.findall(r"[^，。！？；,.!?;]+[，。！？；,.!?;]?", clean) if part.strip()]
    clauses: list[str] = []
    pending = ""
    for part in raw:
        if len(pending) + len(part) < min_chars:
            pending += part
            continue
        candidate = pending + part
        pending = ""
        if len(candidate) <= max_chars:
            clauses.append(candidate)
            continue
        pieces = [x for x in re.split(r"(?<=但)|(?<=却)|(?<=可)|(?<=而)|(?<=又)", candidate) if x]
        clauses.extend(pieces if len(pieces) > 1 else [candidate])
    if pending:
        if clauses and len(pending) < min_chars:
            clauses[-1] += pending
        else:
            clauses.append(pending)
    return [part.strip("，, ") for part in clauses if part.strip("，, ")]


def _flatten_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    return str(value or "")


def _frame_text(record: dict) -> str:
    fields = (
        record.get("caption"), record.get("visual_caption"), record.get("people"), record.get("characters"),
        record.get("character"), record.get("action"), record.get("actions"),
        record.get("scene"), record.get("emotion"), record.get("dialogue"),
    )
    return " ".join(_flatten_text(value) for value in fields if value)


def _normalize(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or "")).lower()


def load_visual_frames(folder: Path) -> list[VisualFrame]:
    path = folder / VISUAL_INDEX_FILE
    if not path.exists():
        raise RuntimeError("缺少原片视觉索引，请先完成视觉识别")
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("原片视觉索引损坏，请重新视觉识别") from exc
    records = payload.get("frames") or payload.get("records") or []
    frames: list[VisualFrame] = []
    for record in records:
        try:
            timestamp = float(record.get("time", record.get("timestamp")))
        except (TypeError, ValueError):
            continue
        evidence = _frame_text(record).strip()
        if evidence:
            frames.append(VisualFrame(timestamp, _normalize(evidence), evidence))
    if not frames:
        raise RuntimeError("原片视觉索引没有可用识别帧")
    return sorted(frames, key=lambda frame: frame.time)


def _expanded_query(text: str) -> str:
    additions = [aliases for key, aliases in _CONCEPT_ALIASES.items() if key in text]
    return text + " " + " ".join(additions)


def _terms(text: str) -> set[str]:
    normalized = _normalize(_expanded_query(text))
    terms: set[str] = set()
    for size in (2, 3, 4):
        terms.update(normalized[index:index + size] for index in range(max(0, len(normalized) - size + 1)))
    return {term for term in terms if term not in _STOPWORDS and not term.isdigit()}


def _semantic_score(query: str, evidence: str) -> float:
    query_terms = _terms(query)
    evidence_terms = _terms(evidence)
    if not query_terms or not evidence_terms:
        return 0.0
    overlap = query_terms & evidence_terms
    weighted = sum(1.0 + min(2, len(term) - 2) * 0.45 for term in overlap)
    total = sum(1.0 + min(2, len(term) - 2) * 0.45 for term in query_terms)
    score = weighted / max(3.0, total)
    for key, aliases in _CONCEPT_ALIASES.items():
        if key in query and any(word in evidence for word in aliases.split() if len(word) >= 2):
            score += 0.16
    return min(1.0, score)


class VisualIntervalAllocator:
    def __init__(self, duration: float, frames: list[VisualFrame], guard: float = 0.18,
                 usable_start: float = 0.0, blocked_intervals: list[dict] | None = None):
        self.duration = duration
        self.usable_start = max(0.0, usable_start)
        self.frames = frames
        self.guard = guard
        self.used: list[tuple[float, float, str]] = []
        self.blocked = [
            (float(item["start"]), float(item["end"]), f"插片广告 {item.get('ad_id', '')}".strip())
            for item in (blocked_intervals or [])
        ]

    def free(self, start: float, end: float) -> bool:
        unavailable = [*self.blocked, *self.used]
        return all(end + self.guard <= left or start >= right + self.guard for left, right, _ in unavailable)

    def reserve(self, start: float, end: float, label: str) -> None:
        if end <= start:
            raise RuntimeError(f"无效素材区间：{start:.3f}-{end:.3f}")
        if not self.free(start, end):
            raise RuntimeError(f"素材区间重复或命中广告禁区：{start:.3f}-{end:.3f} ({label})")
        self.used.append((start, end, label))

    def _window_evidence(self, start: float, end: float) -> tuple[str, list[VisualFrame]]:
        margin = max(1.0, min(5.0, (end - start) * 0.35))
        selected = [frame for frame in self.frames if start - margin <= frame.time <= end + margin]
        if not selected and self.frames:
            midpoint = (start + end) / 2
            selected = sorted(self.frames, key=lambda frame: abs(frame.time - midpoint))[:2]
            selected.sort(key=lambda frame: frame.time)
        return " ".join(frame.evidence for frame in selected), selected

    def _starts(self, left: float, right: float, need: float) -> list[float]:
        max_start = right - need
        if max_start < left:
            return []
        starts = {round(left, 3), round(max_start, 3)}
        for frame in self.frames:
            for offset in (-need * 0.25, 0.0, need * 0.2):
                value = max(left, min(max_start, frame.time + offset))
                starts.add(round(value, 3))
        step = max(1.0, min(3.0, need / 2))
        value = left
        while value <= max_start + 1e-6:
            starts.add(round(value, 3))
            value += step
        return sorted(starts)

    def allocate(self, query: str, need: float, preferred_start: float, preferred_end: float,
                 label: str, chronological_start: float | None = None) -> tuple[float, float, float, str]:
        if need <= 0:
            raise RuntimeError(f"{label} 配音时长无效")
        preferred_start = max(self.usable_start, preferred_start)
        preferred_end = min(self.duration, preferred_end)
        ranges = [
            (preferred_start, preferred_end, 0.22),
            (max(self.usable_start, preferred_start - 45), min(self.duration, preferred_end + 45), 0.08),
            (self.usable_start, self.duration, 0.0),
        ]
        best: tuple[float, float, float, str] | None = None
        for range_index, (left, right, bonus) in enumerate(ranges):
            if chronological_start is not None:
                left = max(left, chronological_start)
            for start in self._starts(left, right, need):
                end = start + need
                if end > right + 1e-6 or not self.free(start, end):
                    continue
                evidence, frames = self._window_evidence(start, end)
                score = _semantic_score(query, evidence) + bonus
                if chronological_start is not None and start >= chronological_start:
                    score += 0.04
                if preferred_start <= start and end <= preferred_end:
                    score += 0.06
                proof = "；".join(frame.evidence for frame in frames[:3]) or "视觉帧附近无文字描述"
                candidate = (start, end, min(1.0, score), proof)
                if best is None or candidate[2] > best[2] + 1e-6 or (
                    abs(candidate[2] - best[2]) <= 1e-6 and candidate[0] < best[0]
                ):
                    best = candidate
            if best is not None:
                break
        if best is None:
            raise RuntimeError(f"{label} 找不到未使用的匹配画面")
        self.reserve(best[0], best[1], label)
        return best
