"""
成片质检：比对解说与画面的匹配精准度。
用法：python scripts/audit_quality.py --folder "<单集素材夹>" [--fix]

检查维度（按严重度）：
1. CRITICAL - 片尾/广告误入：解说画面落在广告禁区
2. HIGH - 人物缺失：解说点名角色但画面中未出现该角色
3. HIGH - 场景语义冲突：解说描述的情绪/动作与画面明显矛盾
4. WARNING - 低置信度：visual_match_score < 0.35
5. WARNING - 片尾邻近：解说画面在片尾60s内且非结尾场景
6. INFO - 画面单调：连续多分镜使用相同源区间

依赖数据：
- ★ 匹配报告.json（clip_start/clip_end, visual_match_score, visual_match_evidence）
- _source_visual_index.json（people, caption, action, emotion, scene）
- _scene_map.json（excluded_ranges, parent_scene_plans）
- _source_ad_intervals.json（硬屏蔽区间）

输出 _quality_audit.json，含问题列表、严重级别、修复方案。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# ── 视觉索引中的演员名 → 角色名映射 ──
# 视觉 API 返回 people 字段格式："演员名（饰角色名）"
# 这个映射帮助从演员名反查角色
ACTOR_TO_CHARACTER = {
    "刘亦菲": "黄亦玫",
    "佟大为": "黄振华",
    "万茜": "苏更生",
    "林更新": "方协文",
    "彭冠英": "庄国栋",
    "朱珠": "姜雪琼",
    "陈瑶": "白晓荷",
    "吴玉芳": "黄妈妈",
    "蓝盈莹": "关芝芝",
    "张月": "韩鹦",
}


def _extract_characters_from_people_field(people_text: str) -> set[str]:
    """从视觉帧的 people 字段中提取角色名（处理'演员名（饰角色名）'格式）."""
    found = set()
    # 直接匹配角色名
    for actor, char in ACTOR_TO_CHARACTER.items():
        if actor in people_text:
            found.add(char)
    # 也匹配 "饰XXX" 格式
    for m in re.finditer(r'饰\s*([^\s)）]+)', people_text):
        found.add(m.group(1))
    return found


# ── 角色名→别名 ──
CHARACTER_ALIASES = {
    "黄亦玫": ["黄亦玫", "玫瑰", "黄同学", "黄一梅", "亦玫"],
    "黄振华": ["黄振华", "振华", "我哥"],
    "苏更生": ["苏更生", "苏苏", "更生"],
    "方协文": ["方协文", "协文", "方师兄", "小方"],
    "庄国栋": ["庄国栋", "国栋"],
    "姜雪琼": ["姜雪琼", "姜总", "江总", "雪琼"],
    "黄妈妈": ["黄妈妈", "妈妈", "阿姨", "母亲"],
    "胡琳": ["胡琳"],
    "白晓荷": ["白晓荷", "晓荷"],
}


def extract_characters_from_text(text: str) -> set[str]:
    """从解说文本中提取提及的角色名（去别名）."""
    found = set()
    for char_name, aliases in CHARACTER_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):  # 最长匹配优先
            if alias in text:
                found.add(char_name)
                break
    return found


# ── 关键时刻关键词 → 预期画面特征 ──
# 使用 \b 词边界避免子串误匹配（如"打算"不匹配"打架"）
KEY_MOMENT_PATTERNS = [
    (re.compile(r'\b(高兴|激动|兴奋|开心|欢喜)\b'), {"emotion": ["高兴", "开心", "激动", "喜悦", "笑", "微笑"]}),
    (re.compile(r'\b(伤心|难过|痛哭|流泪|悲伤|痛苦|哭泣)\b'), {"emotion": ["悲伤", "难过", "伤心", "哭泣", "凝重"]}),
    (re.compile(r'\b(生气|愤怒|恼火|发火|发怒)\b'), {"emotion": ["愤怒", "生气", "严肃", "不悦"]}),
    (re.compile(r'\b(吃饭|用餐|请客|饭局)\b'), {"scene": ["餐厅", "饭桌", "餐桌", "吃饭"]}),
    (re.compile(r'\b(打电话|通话|来电|拨通)\b'), {"action": ["打电话", "通话", "接电话"]}),
    (re.compile(r'\b(打架|动手|推搡|挥拳|打斗|冲突)\b'), {"action": ["打架", "推搡", "冲突", "挥拳"]}),
    (re.compile(r'\b(拥抱|抱住|相拥)\b'), {"action": ["拥抱", "抱住"]}),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def find_nearest_visual_frame(time_sec: float, frames: list[dict], max_dist: float = 10) -> dict | None:
    """找到最接近给定时间的视觉帧."""
    best, best_dist = None, float("inf")
    for f in frames:
        d = abs(f["time"] - time_sec)
        if d < best_dist and d <= max_dist:
            best, best_dist = f, d
    return best


def _frame_field_to_str(val: Any) -> str:
    """将视觉帧字段（可能是str/list/dict）转为统一字符串."""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    if isinstance(val, dict):
        return " ".join(str(v) for v in val.values())
    return str(val or "")


def check_characters_in_frame(narration_text: str, frame: dict | None) -> tuple[bool, str]:
    """检查解说提到的角色是否出现在画面中."""
    if frame is None:
        return True, "无视觉帧可查"

    mentioned = extract_characters_from_text(narration_text)
    if not mentioned:
        return True, "解说未点名角色"

    # 从 people 字段提取画面中的角色
    people_str = _frame_field_to_str(frame.get("people", ""))
    frame_chars = _extract_characters_from_people_field(people_str)

    # 只标记：点名了角色但画面中 ANY 提到的角色都不在
    # 如果至少有一个提到的角色在画面中，认为匹配可接受
    if not mentioned & frame_chars and mentioned:
        missing = mentioned - frame_chars
        return False, f"画面中缺少：{', '.join(sorted(missing))}（画面人物：{people_str[:80]}）"
    return True, ""


def check_semantic_match(narration_text: str, frame: dict | None) -> tuple[bool, str]:
    """检查关键时刻的语义是否与画面匹配."""
    if frame is None:
        return True, ""

    for pattern, expectations in KEY_MOMENT_PATTERNS:
        if pattern.search(narration_text):
            for field, allowed in expectations.items():
                frame_val = _frame_field_to_str(frame.get(field)).lower()
                if not any(exp.lower() in frame_val for exp in allowed):
                    return False, (
                        f"解说暗示{field}={allowed}，"
                        f"画面={_frame_field_to_str(frame.get(field, ''))[:60]}"
                    )
    return True, ""


def audit_episode(folder: Path) -> dict:
    """对单集执行完整质检."""
    def _find_json(*paths: str) -> Path | None:
        for p in paths:
            fp = folder / p
            if fp.exists():
                return fp
        return None

    # 新工作区优先；根目录是正式 run 的最新镜像；最后兼容旧 _DY 工作区。
    match_path = _find_json(
        "_AutoDSJ工作文件/★ 匹配报告.json",
        "★ 匹配报告.json",
        "_DY工作文件/★ 匹配报告.json",
    )
    if match_path is None:
        raise FileNotFoundError(f"未找到匹配报告：{folder}")
    match = load_json(match_path)

    vi_path = _find_json(
        "_AutoDSJ工作文件/_source_visual_index.json",
        "_source_visual_index.json",
        "_DY工作文件/_source_visual_index.json",
    )
    frames = load_json(vi_path).get("frames", []) if vi_path else []

    ad_path = _find_json(
        "_AutoDSJ工作文件/_source_ad_intervals.json",
        "_source_ad_intervals.json",
        "_DY工作文件/_source_ad_intervals.json",
    )
    ad_intervals = []
    if ad_path:
        ad = load_json(ad_path)
        ad_intervals = [(i["start"], i["end"]) for i in ad.get("intervals", [])]

    sm_path = _find_json(
        "_AutoDSJ工作文件/_scene_map.json",
        "_scene_map.json",
        "_DY工作文件/_scene_map.json",
    )
    sm = load_json(sm_path) if sm_path else None
    excluded = sm.get("excluded_ranges", []) if sm else []

    blocked = ad_intervals + excluded

    # 找出结尾场景（用于跳过"片尾邻近"误报）
    ending_scene_names = set()
    if sm:
        for s in sm.get("scenes", []):
            name = s.get("name", "")
            if any(kw in name for kw in ["结尾", "餐厅", "晚宴", "最后"]):
                ending_scene_names.add(name)

    issues = []
    stats = Counter()

    # 计算视频总时长（最大 source_end）
    max_src = max(s.get("clip_end", 0) for s in match["segments"])

    for seg in match.get("segments", []):
        sid = seg["segment_id"]
        row = seg.get("row_type", "")
        if row != "narration":
            continue

        text = seg.get("text", "")
        clip_s = seg.get("clip_start", 0)
        clip_e = seg.get("clip_end", 0)
        out_s = seg.get("output_start", 0)
        conf = seg.get("match_confidence", "")
        vis_score = seg.get("visual_match_score", 0)
        vis_ev = seg.get("visual_match_evidence", "")

        stats["total_narration"] += 1

        # ── Check 1: 广告/片尾误入 ──
        in_blocked = False
        for bs, be in blocked:
            if (bs <= clip_s <= be) or (bs <= clip_e <= be):
                issues.append({
                    "segment_id": sid, "output_time": out_s,
                    "severity": "CRITICAL",
                    "check": "片尾/广告误入",
                    "detail": f"source [{clip_s:.1f}-{clip_e:.1f}s] 在禁区 [{bs:.0f}-{be:.0f}s] 内",
                    "text": text[:60],
                    "fix": "扩大 excluded_ranges 或 ad_intervals 硬屏蔽，重跑 shadow-match + render",
                })
                stats["critical_blocked"] += 1
                in_blocked = True
                break
        if not in_blocked:
            stats["clean_source"] += 1

        # ── Check 2: 人物缺失 ──
        frame = find_nearest_visual_frame(clip_s, frames)
        ok, detail = check_characters_in_frame(text, frame)
        if not ok:
            issues.append({
                "segment_id": sid, "output_time": out_s,
                "severity": "HIGH",
                "check": "人物缺失",
                "detail": detail,
                "text": text[:60],
                "fix": "在正确场景中添加选择性视觉帧，或调整 parent_scene_plans",
            })
            stats["high_missing_char"] += 1

        # ── Check 3: 场景语义冲突 ──
        ok, detail = check_semantic_match(text, frame)
        if not ok:
            issues.append({
                "segment_id": sid, "output_time": out_s,
                "severity": "HIGH",
                "check": "场景语义冲突",
                "detail": detail,
                "text": text[:60],
                "fix": "在正确时间点添加选择性视觉帧",
            })
            stats["high_semantic"] += 1

        # ── Check 4: 低置信度 ──
        if conf == "H" and vis_score < 0.35:
            issues.append({
                "segment_id": sid, "output_time": out_s,
                "severity": "WARNING",
                "check": "低置信度",
                "detail": f"visual_match_score={vis_score:.3f}",
                "text": text[:60],
                "fix": "检查该分镜匹配是否合理，必要时添加视觉帧",
            })
            stats["warning_low_conf"] += 1

        # ── Check 5: 片尾邻近（排除结尾场景） ──
        scene_hint = vis_ev.split(" / ")[-1] if " / " in vis_ev else ""
        is_ending = any(es in scene_hint for es in ending_scene_names)
        if not is_ending and clip_s > max_src - 60:
            issues.append({
                "segment_id": sid, "output_time": out_s,
                "severity": "WARNING",
                "check": "片尾邻近",
                "detail": f"source [{clip_s:.1f}s] 距片尾 {max_src - clip_s:.0f}s（非结尾场景）",
                "text": text[:60],
                "fix": "检查是否误用了片尾画面，考虑排除最后60s",
            })
            stats["warning_near_end"] += 1

    # ── 全局检查：场景聚集异常 ──
    narration_srcs = [(s.get("clip_start", 0), s.get("clip_end", 0), s["segment_id"])
                      for s in match["segments"] if s.get("row_type") == "narration"]

    consecutive_cluster = 0
    for i in range(len(narration_srcs) - 1):
        s1, e1, _ = narration_srcs[i]
        s2, e2, _ = narration_srcs[i + 1]
        if abs(s1 - s2) < 3 and abs(e1 - e2) < 3:
            consecutive_cluster += 1
        else:
            consecutive_cluster = 0
        if consecutive_cluster >= 4:  # 连续5段用同一区间
            stats["clustered_src"] += 1
            break

    if stats.get("clustered_src", 0) > 0:
        issues.append({
            "segment_id": 0, "output_time": 0,
            "severity": "INFO",
            "check": "场景聚集异常",
            "detail": "连续多个分镜使用几乎相同的源区间，可能导致画面单调",
            "text": "",
            "fix": "检查 parent_scene_plans 是否过度集中，分散到不同场景",
        })

    report = {
        "version": 1,
        "stats": dict(stats),
        "issues": issues,
        "critical_count": stats.get("critical_blocked", 0),
        "high_count": stats.get("high_missing_char", 0) + stats.get("high_semantic", 0),
        "warning_count": stats.get("warning_low_conf", 0) + stats.get("warning_near_end", 0),
        "info_count": stats.get("clustered_src", 0),
        "pass": stats.get("critical_blocked", 0) == 0,
    }

    return report


def suggest_fixes(report: dict) -> list[str]:
    """根据质检结果生成修复建议."""
    suggestions = []

    if report["critical_count"] > 0:
        suggestions.append(
            "CRITICAL: 解说画面落入广告/片尾禁区。"
            "将片尾时间加入 _source_ad_intervals.json 硬屏蔽，"
            "然后重跑 shadow-match + render。"
        )

    if report["high_count"] > 0:
        suggestions.append(
            "HIGH: 人物缺失或语义冲突（{n}处）。"
            "检查 parent_scene_plans 是否指向正确场景，"
            "在关键时间点运行 autodsj.py visual --target-frames，"
            "然后重跑 shadow-match + render。"
            .format(n=report["high_count"])
        )

    if report["warning_count"] > 0:
        suggestions.append(
            f"WARNING: {report['warning_count']}处低置信度或片尾邻近。人工复核后决定是否修复。"
        )

    if not suggestions:
        suggestions.append("✅ 全部质检通过，无需要修复的问题。")

    return suggestions


def main():
    import argparse
    ap = argparse.ArgumentParser(description="DY 成片质检 —— 比对解说与画面匹配精准度")
    ap.add_argument("--folder", required=True, help="单集素材目录")
    ap.add_argument("--fix", action="store_true", help="输出修复建议")
    ap.add_argument("--json", action="store_true", help="只输出 JSON，不打印摘要")
    args = ap.parse_args()

    folder = Path(args.folder)
    report = audit_episode(folder)

    out_path = folder / "_quality_audit.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    if not args.json:
        print(f"质检完成：{out_path}")
        print(f"  总分镜：{report['stats'].get('total_narration', 0)}")
        print(f"  CRITICAL: {report['critical_count']}")
        print(f"  HIGH:     {report['high_count']}")
        print(f"  WARNING:  {report['warning_count']}")
        print(f"  INFO:     {report['info_count']}")
        print(f"  PASS:     {report['pass']}")

    if args.fix:
        print("\n修复建议：")
        for s in suggest_fixes(report):
            print(f"  {s}")

    if not args.json:
        # 打印 HIGH 问题的前 5 条摘要
        high_issues = [i for i in report["issues"] if i["severity"] in ("CRITICAL", "HIGH")]
        if high_issues:
            print(f"\n前 {min(5, len(high_issues))} 条严重问题：")
            for i in high_issues[:5]:
                src_time = i.get("output_time", 0)
                m, s = divmod(src_time, 60)
                ts = f"{int(m)}:{int(s):02d}"
                print(f"  [{ts}] {i['check']}: {i['detail'][:100]}")


if __name__ == "__main__":
    main()
