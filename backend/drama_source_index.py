from __future__ import annotations

import json
import tempfile
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from .media import detect_materials
from .net_retry import retry_call
from .vision_api import (
    FrameSample,
    _call_siliconflow_vision,
    _extract_frames,
    _extract_frames_at_times,
    _format_time,
    _probe_duration,
    _subtitle_json,
    parse_srt,
)
from .schemas import AppSettings


SOURCE_SUBTITLE_FILE = "_source_subtitle_index.json"
SOURCE_VISUAL_FILE = "_source_visual_index.json"
SOURCE_CANDIDATE_FILE = "_source_clip_candidates.json"
QWEN37_PLUS_MAX_BATCH_FRAMES = 500
# 索引结构版本：抽帧分辨率↑/结构化 prompt/人脸身份注入后旧缓存不再兼容，
# 版本变化时自动重跑视觉识别（无需用户手动 --force-visual）。
VISUAL_SCHEMA = "v3-selective-face-720p"

ROLE_WEIGHTS = {
    "hook": 0.32,
    "turning_point": 0.28,
    "suspense": 0.26,
    "emotion": 0.22,
    "plot": 0.14,
    "transition": 0.04,
}

TENSION_KEYWORDS = (
    "冲突", "争吵", "质问", "崩溃", "愤怒", "震惊", "哭", "威胁", "分手", "离婚",
    "结婚", "老公", "老婆", "背叛", "误会", "秘密", "真相", "不可能", "凭什么",
    "对不起", "是不是", "为什么", "别", "滚", "你敢",
)

BLOCKED_SUBTITLE_TOKENS = (
    "片头曲", "片尾曲", "主题曲", "字幕组", "广告", "版权所有", "备案号", "本集完",
    "独家冠名", "邀请您观看", "同城旅行", "超级省", "人像之光", "丁桂儿", "脐贴",
    "歌暂停",
)


def _folder(value: str) -> Path:
    folder = Path(value.strip().strip('"')).expanduser()
    if not folder.is_dir():
        raise ValueError(f"素材文件夹不存在：{folder}")
    return folder


def _match_subtitle(video: Path, subtitles: list[Path], index: int) -> Path:
    video_stem = video.stem.lower()
    for subtitle in subtitles:
        subtitle_stem = subtitle.stem.lower()
        if subtitle_stem in video_stem or video_stem in subtitle_stem:
            return subtitle
    return subtitles[min(index, len(subtitles) - 1)]


def _source_record(frame: FrameSample, source_index: int, video: Path, interval: float) -> dict:
    return {
        "frame_id": f"source_{source_index}_{int(round(frame.time * 1000)):010d}",
        "video_role": "source",
        "source_index": source_index,
        "source_file": video.name,
        "time": frame.time,
        "time_text": _format_time(frame.time),
        "interval": interval,
        "clip_id": None,
        "image_path": frame.path,
        "image_file": frame.file,
    }


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(path)


def _write_visual_progress(
    folder: Path,
    *,
    model: str,
    frame_interval: float,
    stage: str,
    message: str,
    progress: int,
    frame_count: int = 0,
    success_count: int = 0,
    failed_count: int = 0,
) -> None:
    _write_json_atomic(folder / SOURCE_VISUAL_FILE, {
        "model": model,
        "api_url": "",
        "frame_interval": frame_interval,
        "status": stage,
        "message": message,
        "progress": max(0, min(100, int(progress))),
        "frame_count": max(0, int(frame_count)),
        "success_count": max(0, int(success_count)),
        "failed_count": max(0, int(failed_count)),
        "frames": [],
        "errors": [],
        "created_at": time.time(),
    })


def _apply_identity(frames: list[dict], identity_map: "dict | None") -> None:
    """把人脸识别身份覆盖到帧记录：写 people(演员(饰角色)) + identified，并把角色名前置进 caption。

    people 字段是 visual_matcher 建组/身份加分的直接来源；人脸未命中的帧保留 VL 的原描述。
    """
    if not identity_map:
        return
    from .face_gallery import render_people_field
    for frame in frames:
        ident = identity_map.get(str(frame.get("frame_id")))
        if not ident:
            continue
        frame["identified"] = ident
        people_field = render_people_field(ident)
        if not people_field:
            continue
        frame["people"] = people_field
        roles = list(dict.fromkeys(r["role"] for r in ident if r.get("role")))
        caption = str(frame.get("caption") or "").strip()
        if roles and not any(role in caption for role in roles):
            role_text = "、".join(roles)
            frame["caption"] = f"【{role_text}】{caption}" if caption else role_text


def _build_identity_map(folder: Path, records: list[dict], settings: AppSettings) -> dict:
    """对每张抽帧跑本地人脸识别，返回 {frame_id: [识别结果...]}。

    需剧集根有 `_face_gallery.json`（用 `dy faces build` 建）且已装 insightface。
    任一缺失则返回空 dict，管线自动退回「无身份注入」的纯 VL 描述。
    """
    vis = settings.visual
    if not vis.use_face_gallery:
        return {}
    try:
        from .face_gallery import FaceIdentifier, insightface_available, load_gallery
    except Exception:
        return {}
    # 人脸库全剧集共享：先查单集夹，再回退到剧集根（素材夹的父目录）
    gallery_path = folder / vis.face_gallery_file
    if not gallery_path.exists():
        parent_path = folder.parent / vis.face_gallery_file
        if parent_path.exists():
            gallery_path = parent_path
    gallery = load_gallery(gallery_path)
    if not gallery:
        return {}
    if not insightface_available():
        print("  ⚠ 检测到人脸库但未安装 insightface，跳过身份识别（仅纯 VL 描述）", flush=True)
        return {}
    print(f"  人脸识别开始：库中 {gallery.get('role_count', 0)} 个角色 / "
          f"{gallery.get('vector_count', 0)} 张参考照", flush=True)
    try:
        identifier = FaceIdentifier(
            gallery,
            threshold=vis.face_threshold,
            min_size=vis.face_min_size,
            det_size=vis.face_det_size,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ 人脸识别初始化失败，跳过身份注入：{exc}", flush=True)
        return {}
    identity_map: dict[str, list] = {}
    total = len(records)
    hit = 0
    for index, record in enumerate(records):
        try:
            ident = identifier.identify(record["image_path"], vis.frame_width, vis.frame_height)
        except Exception:
            ident = []
        if ident:
            identity_map[record["frame_id"]] = ident
            hit += 1
        if (index + 1) % 50 == 0 or index + 1 == total:
            print(f"  人脸识别 {index + 1}/{total}，已识别 {hit} 帧", flush=True)
    print(f"  人脸识别完成：{hit}/{total} 帧识别到已知角色", flush=True)
    return identity_map


def _face_gallery_configured(folder: Path, settings: AppSettings) -> bool:
    if not settings.visual.use_face_gallery:
        return False
    name = settings.visual.face_gallery_file
    return (folder / name).exists() or (folder.parent / name).exists()


def _annotate_source_frames(
    folder: Path,
    records: list[dict],
    api_key: str,
    model: str,
    *,
    frame_interval: float,
    batch_size: int = 8,
    delay_sec: float = 1.0,
    workers: int = 1,
    force: bool = False,
    identity_map: "dict | None" = None,
    cached_visual: "dict | None" = None,
) -> dict:
    visual_file = folder / SOURCE_VISUAL_FILE
    source_signature = [
        {
            "frame_id": item["frame_id"],
            "source_index": item["source_index"],
            "source_file": item["source_file"],
            "time": item["time"],
        }
        for item in records
    ]
    cached = cached_visual if isinstance(cached_visual, dict) else _read_json(visual_file)
    if cached and not force:
        if (
            cached.get("model") == model
            and cached.get("frame_interval") == frame_interval
            and cached.get("visual_schema") == VISUAL_SCHEMA
            and cached.get("source_signature") == source_signature
            and cached.get("success_count", 0) >= len(records)
        ):
            _apply_identity(cached.get("frames", []), identity_map)
            _write_json_atomic(visual_file, cached)
            return cached

    cached_frames: dict[str, dict] = {}
    if cached.get("model") == model and cached.get("visual_schema") == VISUAL_SCHEMA:
        cached_frames = {
            str(item.get("frame_id")): item
            for item in cached.get("frames", [])
            if item.get("frame_id") and item.get("caption")
        }

    record_map = {item["frame_id"]: item for item in records}
    result = {
        "model": model,
        "api_url": "",
        "frame_interval": frame_interval,
        "visual_schema": VISUAL_SCHEMA,
        "status": "recognizing_frames",
        "message": f"视觉识别准备中：共 {len(records)} 帧",
        "progress": 0,
        "frame_count": len(records),
        "success_count": 0,
        "failed_count": 0,
        "source_signature": source_signature,
        "frames": [],
        "errors": [],
        "created_at": time.time(),
    }

    pending = []
    for record in records:
        if identity_map is not None:
            from .face_gallery import render_known_people
            record["known_people"] = render_known_people(identity_map.get(record["frame_id"], []))
        cached_item = cached_frames.get(record["frame_id"])
        if cached_item:
            cached_item.update({
                "video_role": "source",
                "source_index": record["source_index"],
                "source_file": record["source_file"],
                "time": record["time"],
                "time_text": record["time_text"],
                "interval": frame_interval,
            })
            result["frames"].append(cached_item)
        else:
            pending.append(record)

    result["success_count"] = sum(1 for item in result["frames"] if item.get("caption"))
    result["failed_count"] = len(result["frames"]) - result["success_count"]
    _write_json_atomic(visual_file, result)
    batch_size = max(1, min(QWEN37_PLUS_MAX_BATCH_FRAMES, int(batch_size or 8)))
    workers = max(1, min(4, int(workers or 1)))
    batches = [
        (start // batch_size + 1, pending[start:start + batch_size])
        for start in range(0, len(pending), batch_size)
    ]

    def _failed_records(batch: list[dict], error: str) -> list[dict]:
        return [{
            "frame_id": source["frame_id"],
            "video_role": "source",
            "source_index": source["source_index"],
            "source_file": source["source_file"],
            "time": source["time"],
            "time_text": source["time_text"],
            "interval": frame_interval,
            "caption": "",
            "error": error,
        } for source in batch]

    def process_batch(batch_no: int, batch: list[dict], *, attempts: int = 3) -> tuple[int, list[dict], list[dict], str]:
        detail = {"msg": ""}

        def _call() -> list[dict]:
            try:
                # In the first-run pipeline sparse extraction is still running
                # while API batches are submitted.  Wait only for this batch's
                # files instead of blocking on the complete episode frame set.
                deadline = time.monotonic() + 150.0
                while any(not Path(item["image_path"]).is_file() for item in batch):
                    if time.monotonic() >= deadline:
                        missing = [item["frame_id"] for item in batch
                                   if not Path(item["image_path"]).is_file()]
                        raise RuntimeError(f"抽帧流水线超时：{missing}")
                    time.sleep(0.05)
                return _call_siliconflow_vision(api_key, model, result["api_url"], batch)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[-800:]
                detail["msg"] = f"HTTP {exc.code}: {body}"
                raise
            except Exception as exc:
                detail["msg"] = str(exc)
                raise

        try:
            parsed = retry_call(_call, attempts=attempts, base_delay=max(delay_sec, 2.0), max_delay=45.0)
            return batch_no, parsed, [], ""
        except Exception as exc:  # noqa: BLE001
            last_error = detail["msg"] or str(exc)
            return batch_no, [], _failed_records(batch, last_error), last_error

    def merge_batch(batch_no: int, parsed_batch: list[dict], failed_batch: list[dict], error: str) -> None:
        if parsed_batch:
            for item in parsed_batch:
                source = record_map.get(str(item.get("frame_id")), {})
                item.update({
                    "video_role": "source",
                    "source_index": source.get("source_index"),
                    "source_file": source.get("source_file"),
                    "time": source.get("time", item.get("time")),
                    "time_text": source.get("time_text", item.get("time_text")),
                    "interval": frame_interval,
                })
                result["frames"].append(item)
        else:
            result["errors"].append({"batch": batch_no, "error": error})
            result["frames"].extend(failed_batch)

        order = {item["frame_id"]: index for index, item in enumerate(records)}
        result["frames"].sort(key=lambda item: order.get(str(item.get("frame_id")), 10**9))
        result["success_count"] = sum(1 for item in result["frames"] if item.get("caption"))
        result["failed_count"] = len(result["frames"]) - result["success_count"]
        done_count = result["success_count"] + result["failed_count"]
        result["status"] = "recognizing_frames"
        result["progress"] = int(round(min(99, max(12, done_count * 100 / max(1, len(records))))))
        result["message"] = f"视觉识别进行中：成功 {result['success_count']}/{len(records)} 帧"
        _write_json_atomic(visual_file, result)

    if workers <= 1 or len(batches) <= 1:
        for batch_no, batch in batches:
            merge_batch(*process_batch(batch_no, batch))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_batch, batch_no, batch) for batch_no, batch in batches]
            for future in as_completed(futures):
                merge_batch(*future.result())

    # 失败帧单独重跑：首轮无 caption 的帧，用更小批(2)+更多重试再抢救一次
    retry_ids = [str(item.get("frame_id")) for item in result["frames"] if not item.get("caption")]
    retry_records = [record_map[fid] for fid in retry_ids if fid in record_map]
    if retry_records:
        print(f"  失败帧单独重跑：{len(retry_records)} 帧", flush=True)
        recovered: dict[str, dict] = {}
        small_batches = [
            (idx // 2 + 1, retry_records[idx:idx + 2])
            for idx in range(0, len(retry_records), 2)
        ]
        for batch_no, batch in small_batches:
            _, parsed, _, _ = process_batch(batch_no, batch, attempts=4)
            for item in parsed:
                if item.get("caption"):
                    recovered[str(item.get("frame_id"))] = item
        if recovered:
            for frame in result["frames"]:
                hit = recovered.get(str(frame.get("frame_id")))
                if hit and not frame.get("caption"):
                    frame.update(hit)
                    frame["interval"] = frame_interval
            result["success_count"] = sum(1 for item in result["frames"] if item.get("caption"))
            result["failed_count"] = len(result["frames"]) - result["success_count"]
            print(f"  重跑抢救成功 {len(recovered)} 帧", flush=True)

    result["status"] = "complete"
    result["progress"] = 100
    result["message"] = f"视觉识别完成：成功 {result['success_count']}/{len(records)} 帧"
    _apply_identity(result["frames"], identity_map)
    _write_json_atomic(visual_file, result)
    return result


def _blank_visual_index(records: list[dict], model: str, frame_interval: float) -> dict:
    return {
        "model": model,
        "api_url": "",
        "frame_interval": frame_interval,
        "frame_count": len(records),
        "success_count": 0,
        "failed_count": 0,
        "frames": [
            {
                "frame_id": item["frame_id"],
                "video_role": "source",
                "source_index": item["source_index"],
                "source_file": item["source_file"],
                "time": item["time"],
                "time_text": item["time_text"],
                "interval": frame_interval,
                "caption": "",
            }
            for item in records
        ],
        "errors": [],
    }


def _clean_text(text: str) -> str:
    return " ".join(str(text or "").replace("\u3000", " ").split()).strip()


def _usable_cue(cue: dict) -> bool:
    text = _clean_text(cue.get("text", ""))
    return bool(text) and not any(token in text for token in BLOCKED_SUBTITLE_TOKENS)


def _visual_between(frames: Iterable[dict], source_index: int, start: float, end: float) -> list[dict]:
    result = []
    for frame in frames:
        if int(frame.get("source_index") or 0) != source_index:
            continue
        time_value = float(frame.get("time") or 0)
        if start <= time_value <= end:
            result.append(frame)
    return result


def _first_nonempty(values: Iterable[str]) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _merge_values(frames: list[dict], key: str, limit: int = 3) -> list[str]:
    values = []
    seen = set()
    for frame in frames:
        text = _clean_text(frame.get(key, ""))
        if text and text not in seen:
            seen.add(text)
            values.append(text)
        if len(values) >= limit:
            break
    return values


def _dominant_role(frames: list[dict], dialogue_text: str) -> str:
    roles = [str(frame.get("editing_role", "")).strip() for frame in frames if frame.get("editing_role")]
    if roles:
        return Counter(roles).most_common(1)[0][0]
    if any(token in dialogue_text for token in ("为什么", "是不是", "凭什么", "不可能", "对不起")):
        return "turning_point"
    if any(token in dialogue_text for token in ("秘密", "真相", "谁", "到底")):
        return "suspense"
    return "plot"


def _candidate_score(frames: list[dict], dialogue_text: str, role: str) -> float:
    score = 0.35 + ROLE_WEIGHTS.get(role, 0.08)
    text_blob = dialogue_text + " " + " ".join(
        _clean_text(frame.get("caption", "")) + " " + _clean_text(frame.get("emotion", ""))
        for frame in frames
    )
    score += min(0.24, sum(0.03 for token in TENSION_KEYWORDS if token in text_blob))
    if dialogue_text:
        score += 0.08
    if any(str(frame.get("shot_scale", "")).lower() in ("close-up", "medium") for frame in frames):
        score += 0.05
    if len(dialogue_text) < 10:
        score -= 0.05
    return round(max(0.05, min(0.99, score)), 3)


def _build_candidates(subtitles: list[dict], visual_index: dict, settings: AppSettings) -> list[dict]:
    target_seconds = 20.0
    frames = visual_index.get("frames", [])
    candidates = []
    clip_id = 1

    for source_index in sorted({int(item["source_index"]) for item in subtitles}):
        cues = [
            item for item in subtitles
            if int(item["source_index"]) == source_index and _usable_cue(item)
        ]
        cues.sort(key=lambda item: float(item["start"]))
        current: list[dict] = []

        def flush() -> None:
            nonlocal clip_id, current
            if not current:
                return
            start = float(current[0]["start"])
            end = float(current[-1]["end"])
            if end <= start:
                current = []
                return
            dialogue_lines = [_clean_text(item.get("text", "")) for item in current if _clean_text(item.get("text", ""))]
            dialogue_text = " ".join(dialogue_lines)
            visual_frames = _visual_between(frames, source_index, start, end)
            role = _dominant_role(visual_frames, dialogue_text)
            candidate = {
                "clip_id": clip_id,
                "source_index": source_index,
                "source_file": current[0].get("source_file", ""),
                "subtitle_file": current[0].get("subtitle_file", ""),
                "start": round(start, 3),
                "end": round(end, 3),
                "start_text": _format_time(start),
                "end_text": _format_time(end),
                "duration": round(end - start, 3),
                "dialogue": dialogue_lines[:12],
                "dialogue_text": dialogue_text[:500],
                "visual_captions": _merge_values(visual_frames, "caption", 4),
                "characters": _merge_values(visual_frames, "people", 3),
                "actions": _merge_values(visual_frames, "action", 3),
                "emotions": _merge_values(visual_frames, "emotion", 3),
                "editing_role": role,
                "douyin_value": _first_nonempty(frame.get("douyin_value", "") for frame in visual_frames),
                "source_audio_mode": "keep_dialogue" if settings.drama.keep_source_audio else "lower_under_narration",
                "score": _candidate_score(visual_frames, dialogue_text, role),
            }
            candidates.append(candidate)
            clip_id += 1
            current = []

        for cue in cues:
            if not current:
                current = [cue]
                continue
            gap = float(cue["start"]) - float(current[-1]["end"])
            duration = float(cue["end"]) - float(current[0]["start"])
            if gap <= 4.0 and duration <= target_seconds * 1.2:
                current.append(cue)
            else:
                flush()
                current = [cue]
        flush()

    candidates.sort(key=lambda item: (item["source_index"], item["start"]))
    for index, item in enumerate(candidates, 1):
        item["clip_id"] = index
    return candidates


def adaptive_frame_interval(
    duration: float,
    *,
    target_frames: int = 320,
    lo: float = 4.0,
    hi: float = 12.0,
) -> float:
    """按视频时长自适应抽帧间隔，把总帧数控制在 ~target_frames，封顶 [lo, hi] 秒。

    短集抽得更密（间隔小、更准），长集抽得更疏（间隔大、更快、控 API 量）。
    """
    if duration <= 0:
        return 6.0
    return round(max(lo, min(hi, duration / max(1, target_frames))), 3)


def build_source_index(
    settings: AppSettings,
    *,
    siliconflow_api_key: str = "",
    visual_model: str = "",
    frame_interval: float = 6.0,
    visual_batch_size: int = 8,
    visual_delay_sec: float = 1.0,
    visual_workers: int = 1,
    force_visual: bool = False,
    enable_visual_model: bool = True,
    visual_sample_times: dict[int, list[float]] | None = None,
) -> dict:
    folder = _folder(settings.material_folder)
    media = detect_materials(settings.material_folder, settings.drama.source_count)
    video_paths = [Path(path) for path in media.video_paths]
    subtitle_paths = [Path(path) for path in media.subtitle_paths]
    if not video_paths:
        raise RuntimeError("没有可识别的原片视频")
    if not subtitle_paths:
        raise RuntimeError("没有可识别的原片 SRT/ASS 字幕")

    if frame_interval and float(frame_interval) > 0:
        frame_interval = max(2.0, float(frame_interval))
    else:
        frame_interval = adaptive_frame_interval(float(media.duration))
    model = visual_model or settings.api.visual_model or "qwen3.7-plus"

    subtitle_sources = []
    subtitle_records = []
    frame_records: list[dict] = []
    extraction_futures = []
    # Progress updates intentionally replace the public index while a run is in
    # flight.  Keep the last completed payload in memory first, otherwise a
    # resume would erase its own frame cache before annotation starts.
    previous_visual_index = _read_json(folder / SOURCE_VISUAL_FILE) if enable_visual_model else {}

    with tempfile.TemporaryDirectory(prefix="daobaoai_dy_source_") as temp_root, \
            ThreadPoolExecutor(max_workers=max(1, min(3, len(video_paths)))) as extraction_pool:
        temp = Path(temp_root)
        if enable_visual_model:
            _write_visual_progress(
                folder,
                model=model,
                frame_interval=frame_interval,
                stage="extracting_frames",
                message=f"正在截取视频帧：0/{len(video_paths)} 个视频",
                progress=1,
            )
        for index, video in enumerate(video_paths):
            subtitle = _match_subtitle(video, subtitle_paths, index)
            entries = parse_srt(subtitle)
            duration = _probe_duration(video)
            trim_start = min(float(settings.video.trim_head), max(0.0, duration))
            trim_end = max(trim_start, duration - float(settings.video.trim_tail))
            source_index = index + 1
            source_subtitles = []
            for item in _subtitle_json(entries):
                if float(item["end"]) < trim_start or float(item["start"]) > trim_end:
                    continue
                clipped_start = max(float(item["start"]), trim_start)
                clipped_end = min(float(item["end"]), trim_end)
                item["start"] = round(clipped_start, 3)
                item["end"] = round(clipped_end, 3)
                item["start_text"] = _format_time(clipped_start)
                item["end_text"] = _format_time(clipped_end)
                item["duration"] = round(max(0.0, clipped_end - clipped_start), 3)
                item["source_index"] = source_index
                item["source_file"] = video.name
                item["subtitle_file"] = subtitle.name
                source_subtitles.append(item)
                subtitle_records.append(item)
            subtitle_sources.append({
                "source_index": source_index,
                "video_path": str(video.resolve()),
                "subtitle_path": str(subtitle.resolve()),
                "duration": round(duration, 3),
                "trim_start": round(trim_start, 3),
                "trim_end": round(trim_end, 3),
                "subtitle_count": len(source_subtitles),
            })
            if enable_visual_model:
                _write_visual_progress(
                    folder,
                    model=model,
                    frame_interval=frame_interval,
                    stage="extracting_frames",
                    message=f"正在截取视频帧：{source_index}/{len(video_paths)} {video.name}",
                    progress=max(1, int(index / max(1, len(video_paths)) * 10)),
                    frame_count=len(frame_records),
                )
            selected = (visual_sample_times or {}).get(source_index)
            if selected is not None:
                out_dir = temp / f"source_{source_index}"
                out_dir.mkdir(parents=True, exist_ok=True)
                values = sorted(set(round(float(value), 3) for value in selected))
                frames = [
                    FrameSample(
                        value, 0, f"source{source_index}_{frame_no:06d}.jpg",
                        str(out_dir / f"source{source_index}_{frame_no:06d}.jpg"),
                    )
                    for frame_no, value in enumerate(values, 1)
                ]
                extraction_futures.append(extraction_pool.submit(
                    _extract_frames_at_times,
                    video, out_dir, values, f"source{source_index}",
                    width=settings.visual.frame_width, height=settings.visual.frame_height,
                    jpeg_q=settings.visual.jpeg_q, workers=visual_workers,
                ))
            else:
                frames = _extract_frames(
                    video, temp / f"source_{source_index}", frame_interval, f"source{source_index}",
                    width=settings.visual.frame_width,
                    height=settings.visual.frame_height,
                    jpeg_q=settings.visual.jpeg_q,
                )
            frame_records.extend(
                _source_record(frame, source_index, video, frame_interval)
                for frame in frames
                if trim_start <= frame.time <= trim_end
            )
            if enable_visual_model:
                _write_visual_progress(
                    folder,
                    model=model,
                    frame_interval=frame_interval,
                    stage="extracting_frames",
                    message=f"视频帧截取完成：{source_index}/{len(video_paths)} 个视频，待识别 {len(frame_records)} 帧",
                    progress=max(2, min(12, int(source_index / max(1, len(video_paths)) * 12))),
                    frame_count=len(frame_records),
                )

        if enable_visual_model:
            if not siliconflow_api_key:
                raise RuntimeError("已请求视觉识别，但 SiliconFlow API Key 未配置")
            _write_visual_progress(
                folder,
                model=model,
                frame_interval=frame_interval,
                stage="recognizing_frames",
                message=f"开始视觉帧识别：共 {len(frame_records)} 帧，每批 {visual_batch_size} 帧",
                progress=12,
                frame_count=len(frame_records),
            )
            # Face identity must be known before the VL prompt.  Only this
            # optional branch waits for all frames; the common no-gallery path
            # overlaps extraction with the first visual API batches.
            if _face_gallery_configured(folder, settings):
                for future in extraction_futures:
                    future.result()
            identity_map = _build_identity_map(folder, frame_records, settings)
            visual_index = _annotate_source_frames(
                folder,
                frame_records,
                siliconflow_api_key,
                model,
                frame_interval=frame_interval,
                batch_size=visual_batch_size,
                delay_sec=visual_delay_sec,
                workers=visual_workers,
                force=force_visual,
                identity_map=identity_map,
                cached_visual=previous_visual_index,
            )
        else:
            visual_index = _blank_visual_index(frame_records, model, frame_interval)
            _write_json_atomic(folder / SOURCE_VISUAL_FILE, visual_index)

        for future in extraction_futures:
            future.result()

    subtitle_payload = {
        "sources": subtitle_sources,
        "subtitle_count": len(subtitle_records),
        "subtitles": subtitle_records,
    }
    candidates = _build_candidates(subtitle_records, visual_index, settings)
    candidate_payload = {
        "settings": {
            "keep_source_audio": settings.drama.keep_source_audio,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
    }

    _write_json_atomic(folder / SOURCE_SUBTITLE_FILE, subtitle_payload)
    _write_json_atomic(folder / SOURCE_CANDIDATE_FILE, candidate_payload)

    generated = [SOURCE_SUBTITLE_FILE, SOURCE_VISUAL_FILE, SOURCE_CANDIDATE_FILE]
    return {
        "ok": True,
        "folder": str(folder.resolve()),
        "source_count": len(subtitle_sources),
        "subtitle_count": len(subtitle_records),
        "visual_model": visual_index.get("model", model),
        "visual_frame_count": visual_index.get("frame_count", 0),
        "visual_success_count": visual_index.get("success_count", 0),
        "visual_failed_count": visual_index.get("failed_count", 0),
        "candidate_count": len(candidates),
        "sources": subtitle_sources,
        "generated_files": [str((folder / name).resolve()) for name in generated],
    }
