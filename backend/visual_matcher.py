from __future__ import annotations

import bisect
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
    # --- people (fallback when data-driven _DYNAMIC_CHAR_ALIASES misses; keep shallow) ---
    "玫瑰": "玫瑰 年轻女子 女生",
    "黄振华": "黄振华 男子 戴眼镜男 哥哥",
    "姜雪琼": "姜雪琼 老板 成熟女性",
    # --- venues / places ---
    "活动现场": "活动现场 会场 展厅 展览厅 大厅 宴会厅 活动现场 发布会 艺术展",
    "酒店": "酒店 宴会厅 大堂 前台 签到台 接待处",
    "公司": "公司 办公室 办公区 工位 会议室 写字楼",
    "医院": "医院 急诊 急救 输液 病床 护工 白大褂 担架 挂号",
    "餐厅": "餐厅 饭桌 碗筷 进食 用餐 食堂 餐桌",
    "聚会": "聚会 酒会 派对 社交 晚宴 举杯 碰杯 酒杯",
    "车里": "车里 驾驶座 副驾驶 车内 开车 行驶 方向盘",
    "建筑": "建筑 玻璃幕墙 写字楼 大厦 大门",
    "过道": "过道 走廊 楼梯 安全出口 楼道 通道 拐角",
    "家中": "家中 客厅 沙发 卧室 厨房 冰箱 阳台",
    # --- actions ---
    "进入": "进入 走进 推门 步入 进入会场 进门 穿过 赶往",
    "找人": "找人 寻找 环顾 巡视 张望 打量 东张西望 四处看",
    "交谈": "交谈 说话 聊天 对话 讨论 交流 搭话",
    "见面": "见面 见到 握手 迎接 介绍 认识 引荐",
    "跟随": "跟随 带领 引路 指引 陪同 引导 伸手",
    "站立": "站立 站着 站在 驻足 依靠",
    "道歉": "道歉 鞠躬 低头 歉意 对不起 赔礼",
    "打电话": "打电话 手机 通话 拨号 接听 耳机",
    "微笑": "微笑 笑着 笑容 咧嘴 嘴角上扬 温和",
    "惊讶": "惊讶 吃惊 愣住 愣神 张嘴 诧异 意外",
    "喝多": "喝多 醉酒 微醺 脸红 瘫坐 倚靠 摇晃 倒酒 干杯 红酒杯 空杯",
    "送医": "送医 送医院 搀扶 急救 急救室 急诊 挂号 排队",
    "招待": "招待 接待 递水 端茶 握手 签署 登记 签到 登记表 电脑",
    "试穿": "试穿 试衣服 更衣室 镜前 礼服 换装 试鞋",
    "偷偷": "偷偷 悄悄 蹑手蹑脚 探头 偷看 窥视 暗中",
    "犹豫": "犹豫 迟疑 停顿 皱眉 纠结 站着不动",
    "拿下": "拿下 成功 搞定 签约 谈判 拍板 握手成交",
    "感谢": "感谢 道谢 微笑致意 感激",
    "规划": "规划 策划 画图 图纸 讲解 汇报 看表 白板 幻灯片 演示 屏幕",
    "撰写": "撰写 写作 打字 键盘 笔记本 电脑屏幕 伏案",
    # --- visual clues / shot types ---
    "近景": "近景 特写 脸部 表情 面部 肩以上 大头",
    "中景": "中景 半身 两人 双人 餐桌对坐",
    "全景": "全景 远景 大厅 走廊延伸 人群 多人物",
    "背影": "背影 走路 走远 前行 离开",
    "名字": "名字 名片 工牌 胸牌 名牌 标签 证件",
    "酒杯": "酒杯 高脚杯 红酒杯 碰杯 干杯 酒瓶 香槟",
    "名片": "名片 工牌 胸牌 证件 名牌 工作证",
    "沙发": "沙发 座椅 长椅 等候椅 休息区",
}

_STOPWORDS = {
    "一个", "自己", "已经", "就是", "还是", "没有", "根本", "这次", "这一", "那条",
    "因为", "所以", "可是", "偏偏", "终于", "很快", "开始", "面对", "真正", "从此",
    "觉得", "以为", "如果", "只要", "怎么", "什么", "完全", "直接", "当场", "一句话",
}


# --- Per-episode character identity (data-driven) -------------------------
# The visual index annotates faces as "演员（饰演角色）". We harvest those pairs
# per-episode so character-name matching adapts to whatever cast is on screen,
# instead of relying on a hardcoded (and quickly stale) appearance table.
_DYNAMIC_CHAR_ALIASES: dict[str, str] = {}
_CHAR_GROUPS: list[frozenset] = []
# Nicknames the narration writer uses that the on-screen credits never spell
# out. Bridges 文案昵称 -> 剧名，so name matching can still fire.
_NICKNAME_BRIDGE = {
    # 《玫瑰的故事》文案昵称 -> 剧中本名（源：维基/豆瓣角色表，2026-07 核实）。
    # 只有当本名在视觉索引的「演员（饰演角色）」标注里出现、成组后，桥接才生效；
    # 多余条目无害（register_character_aliases 仅在 real ∈ 组内时才并入昵称）。
    "玫瑰": "黄亦玫",
    "小玫": "黄亦玫",
    "Rosie": "黄亦玫",
    "苏苏": "苏更生",
    "小初": "方太初",
    "Tina": "姜雪琼",
    "Eric": "庄国栋",
    "咪咪": "周可咪",
}
_ACTOR_ROLE_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z·]{2,8})\s*[（(]\s*饰演?\s*([\u4e00-\u9fff]{2,5})\s*[)）]"
)


def register_character_aliases(records: list[dict]) -> None:
    """Build character alias groups from the index's 演员（饰演角色）annotations.

    Primary source is the `演员（饰演角色）` pattern. As a fallback, any bridged
    real-name (see _NICKNAME_BRIDGE) that the index labels by role-name ONLY this
    episode (no actor annotation) still gets a group seeded from its appearance in
    the frames, so narration nicknames (苏苏→苏更生, 小初→方太初) can fire the
    character bonus even when the actor wasn't recognized on screen.
    """
    global _DYNAMIC_CHAR_ALIASES, _CHAR_GROUPS
    groups: dict[str, set[str]] = {}
    all_text_parts: list[str] = []
    for record in records:
        blob = " ".join(
            str(record.get(key, "")) for key in ("people", "caption", "characters", "character")
        )
        all_text_parts.append(blob)
        for actor, role in _ACTOR_ROLE_RE.findall(blob):
            groups.setdefault(role, {role}).update({actor, role})
    all_text = " ".join(all_text_parts)
    # Seed a group for any bridged real-name that appears role-only in the frames.
    for real in set(_NICKNAME_BRIDGE.values()):
        if real in all_text and not any(real in members for members in groups.values()):
            groups.setdefault(real, {real})
    for nick, real in _NICKNAME_BRIDGE.items():
        for members in groups.values():
            if real in members:
                members.add(nick)
    _CHAR_GROUPS = [frozenset(members) for members in groups.values() if len(members) >= 2]
    aliases: dict[str, str] = {}
    for members in _CHAR_GROUPS:
        joined = " ".join(sorted(members))
        for token in members:
            aliases[token] = joined
    _DYNAMIC_CHAR_ALIASES = aliases


def _character_hits(text: str) -> set[int]:
    """Which character groups does this raw text mention (by group index)."""
    return {idx for idx, members in enumerate(_CHAR_GROUPS)
            if any(token in text for token in members)}


def dominant_character_group(texts: list[str]) -> int | None:
    """The character group mentioned most often across the given narration texts.

    Used as the IMPLIED subject for pronoun-only / no-name narration (e.g. 只用
    「她」而不点名), so the protagonist's footage is preferred when nobody is
    explicitly named. Fully data-driven — adapts to whatever cast dominates this
    episode's 文案, so it works for any drama, not just the current one.
    """
    if not _CHAR_GROUPS:
        return None
    counts: dict[int, int] = {}
    for text in texts:
        for idx in _character_hits(text):
            counts[idx] = counts.get(idx, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / max(na * nb, 1e-12)


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
    register_character_aliases(records)
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
    additions += [aliases for key, aliases in _DYNAMIC_CHAR_ALIASES.items() if key in text]
    return text + " " + " ".join(additions)


def _terms(text: str, *, expand: bool = True) -> set[str]:
    raw = _expanded_query(text) if expand else text
    normalized = _normalize(raw)
    terms: set[str] = set()
    for size in (2, 3, 4):
        terms.update(normalized[index:index + size] for index in range(max(0, len(normalized) - size + 1)))
    return {term for term in terms if term not in _STOPWORDS and not term.isdigit()}


def _semantic_score(query: str, evidence: str) -> float:
    # Core terms (NO alias expansion) — denominator so expansion never dilutes.
    core_qt = _terms(query, expand=False)
    core_et = _terms(evidence, expand=False)
    if not core_qt or not core_et:
        return 0.0
    # Overlap uses EXPANDED terms so alias vocabulary bridges the gap.
    expanded_qt = _terms(query, expand=True)
    expanded_et = _terms(evidence, expand=True)
    overlap = expanded_qt & expanded_et
    weighted = sum(1.0 + min(2, len(term) - 2) * 0.45 for term in overlap)
    total = sum(1.0 + min(2, len(term) - 2) * 0.45 for term in core_qt)
    score = weighted / max(3.0, total)
    # Concept bonus: reward when the query mentions a known concept AND the
    # (expanded) evidence contains at least one of its visual synonyms.
    for key, aliases in _CONCEPT_ALIASES.items():
        if key in query and any(word in _expanded_query(evidence) for word in aliases.split() if len(word) >= 2):
            score += 0.16
    return min(1.0, score)


class VisualIntervalAllocator:
    def __init__(self, duration: float, frames: list[VisualFrame], guard: float = 0.18,
                 usable_start: float = 0.0, blocked_intervals: list[dict] | None = None,
                 frame_vecs: dict[float, list[float]] | None = None,
                 protagonist_group: int | None = None):
        self.duration = duration
        self.usable_start = max(0.0, usable_start)
        self.frames = frames
        self._frame_times = [frame.time for frame in frames]
        self.guard = guard
        self.frame_vecs = frame_vecs or {}
        self.protagonist_group = protagonist_group
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

    def reserve_source_clip(self, start: float, end: float, label: str) -> None:
        """Reserve a user-quoted 原片 clip. Unlike reserve(), source clips MAY overlap
        each other — the 文案 legitimately re-quotes the same footage (opening hook +
        in-narrative replay). Overlap with other source clips is fine; landing inside a
        detected ad zone is not. Still recorded in `used` so narration never reuses it.
        """
        if end <= start:
            raise RuntimeError(f"无效素材区间：{start:.3f}-{end:.3f}")
        for left, right, _ in self.blocked:
            if not (end + self.guard <= left or start >= right + self.guard):
                raise RuntimeError(f"素材区间命中广告禁区：{start:.3f}-{end:.3f} ({label})")
        self.used.append((start, end, label))

    def _window_evidence(self, start: float, end: float) -> tuple[str, list[VisualFrame]]:
        margin = max(1.0, min(5.0, (end - start) * 0.35))
        lo = bisect.bisect_left(self._frame_times, start - margin)
        hi = bisect.bisect_right(self._frame_times, end + margin)
        selected = self.frames[lo:hi]
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
                 label: str, chronological_start: float | None = None,
                 query_vec: list[float] | None = None,
                 scene_ranges: list[tuple[float, float]] | None = None) -> tuple[float, float, float, str]:
        if need <= 0:
            raise RuntimeError(f"{label} 配音时长无效")
        preferred_start = max(self.usable_start, preferred_start)
        preferred_end = min(self.duration, preferred_end)
        use_embed = bool(query_vec) and bool(self.frame_vecs)
        q_chars = _character_hits(query)
        # Implied-subject fallback: when the narration names NOBODY (pronoun-only
        # or subjectless, e.g. 「但她也不是…」), treat the episode protagonist as
        # the implied subject so the lead's footage is preferred. Explicit names
        # use a strong ±(0.4/0.3) pull; the implied protagonist a gentle ±(0.2/0.15).
        named = bool(q_chars)
        if named:
            subject_chars = q_chars
        elif self.protagonist_group is not None:
            subject_chars = {self.protagonist_group}
        else:
            subject_chars = set()

        def _scan(restrict: list[tuple[float, float]] | None) -> tuple[float, float, float, str] | None:
            # LLM-embedding semantic similarity is the PRIMARY driver. When
            # `restrict` (scene ranges) is given, candidates are HARD-limited to
            # that scene's 原片 区间 — narration about a labeled scene (会议/病房/活动现场
            # …) draws footage only from that scene. Without a scene, the whole
            # timeline is scanned with the anchored window as a soft proximity nudge.
            best: tuple[float, float, float, str] | None = None
            for start in self._starts(self.usable_start, self.duration, need):
                end = start + need
                if end > self.duration + 1e-6 or not self.free(start, end):
                    continue
                if restrict is not None and not any(
                    rs - 1e-6 <= start and end <= re + 1e-6 for rs, re in restrict
                ):
                    continue
                evidence, frames = self._window_evidence(start, end)
                if use_embed:
                    sims = [_cosine(query_vec, self.frame_vecs[f.time])
                            for f in frames if f.time in self.frame_vecs]
                    semantic = max(sims) if sims else 0.0
                else:
                    semantic = _semantic_score(query, evidence)
                score = semantic
                # Character identity — reward frames showing the (explicit/implied)
                # subject; punish frames clearly showing a DIFFERENT named lead.
                if subject_chars:
                    e_chars = _character_hits(evidence)
                    if subject_chars & e_chars:
                        score += 0.4 if named else 0.2
                    elif e_chars:
                        score -= 0.3 if named else 0.15
                # Proximity nudge toward the anchored window only matters in the
                # free (non-scene-locked) scan; inside a scene, semantics decide.
                if restrict is None:
                    if preferred_start <= start and end <= preferred_end:
                        score += 0.1
                    else:
                        gap = (preferred_start - end) if end < preferred_start else (start - preferred_end)
                        score += max(0.0, 0.08 * (1.0 - min(1.0, gap / 150.0)))
                if chronological_start is not None and start >= chronological_start:
                    score += 0.04
                proof = "；".join(frame.evidence for frame in frames[:3]) or "视觉帧附近无文字描述"
                candidate = (start, end, min(1.0, score), proof)
                if best is None or candidate[2] > best[2] + 1e-6 or (
                    abs(candidate[2] - best[2]) <= 1e-6 and candidate[0] < best[0]
                ):
                    best = candidate
            return best

        # Scene lock is a HARD constraint: a narration shot classified to a labeled
        # scene must draw its footage from that scene's ranges — this is what stops
        # "开会" narration grabbing a cafeteria/event frame. Falls back to the global
        # scan only when the scene is fully occupied (no free window), so it never
        # crashes even if many shots map to one short scene.
        best = _scan(scene_ranges) if scene_ranges else None
        if best is None:
            best = _scan(None)
        if best is None:
            raise RuntimeError(f"{label} 找不到未使用的匹配画面")
        self.reserve(best[0], best[1], label)
        return best
