#!/usr/bin/env python3
"""DY 工作流 — 电视剧/短剧全自动智能剪辑（纯后端 CLI）。

一条正式管线：素材文件夹（原片 + SRT/ASS 字幕 + 原片/解说文案）
  ->  检测素材  ->  视觉识别  ->  生成脚本表  ->  配音 + 剪辑 + 后处理  ->  ★ 成片.mp4

常用：
    python dy.py run --folder "D:\\自动剪辑\\某剧"     # 一键全流程
    python dy.py doctor                              # 环境自检
    python dy.py status                              # 查看当前进度
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # Hermes/PowerShell may start Python with a GBK pipe even when all project
    # files are UTF-8; keep status/progress symbols from crashing the CLI.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend import runner
from backend.cleanup import cleanup_render_artifacts
from backend.config_store import (
    MASK,
    load_settings,
    read_env,
    read_secrets,
    save_settings,
    write_secrets,
)
from backend.drama_source_index import build_source_index
from backend.manual_script import (
    SCRIPT_TABLE_FILE,
    find_manual_script_file,
    generate_manual_script_table,
)
from backend.media import detect_materials
from backend.schemas import AppSettings
from backend.scene_map import validate_scene_map

ROOT = Path(__file__).resolve().parent
VISUAL_INDEX_FILE = "_source_visual_index.json"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resolve_settings(folder: str | None) -> AppSettings:
    settings = load_settings(mask_keys=False)
    if folder:
        settings.material_folder = str(Path(folder).expanduser().resolve())
    if not settings.material_folder:
        raise SystemExit("未设置素材文件夹。请用 `dy set --folder <路径>` 或 `dy run --folder <路径>`。")
    if not Path(settings.material_folder).is_dir():
        raise SystemExit(f"素材文件夹不存在：{settings.material_folder}")
    return settings


def _dashscope_key(settings: AppSettings) -> str:
    key = settings.api.dashscope_api_key
    if key == MASK:
        key = ""
    return (
        key
        or read_secrets().get("dashscope_api_key", "")
        or read_env().get("DASHSCOPE_API_KEY", "")
        or read_secrets().get("siliconflow_api_key", "")
        or read_env().get("SILICONFLOW_API_KEY", "")
    )


def _visual_stats(folder: Path) -> dict:
    path = folder / VISUAL_INDEX_FILE
    if not path.exists():
        return {"exists": False, "ready": False, "frame_count": 0, "success": 0, "failed": 0, "interval": 0.0}
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exists": True, "ready": False, "frame_count": 0, "success": 0, "failed": 0, "interval": 0.0, "corrupt": True}
    frame_count = int(payload.get("frame_count") or len(payload.get("frames") or []) or 0)
    success = int(payload.get("success_count") or 0)
    failed = int(payload.get("failed_count") or 0)
    status = str(payload.get("status") or "")
    selective_schema = payload.get("visual_schema") == "v3-selective-face-720p"
    plan_matches = True
    plan_path = folder / "_selective_visual_plan.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text("utf-8"))
            planned_times = [round(float(value), 3) for value in plan.get("times", [])]
            indexed_times = [round(float(item.get("time", -1)), 3)
                             for item in payload.get("source_signature", [])
                             if int(item.get("source_index", 1)) == 1]
            plan_matches = planned_times == indexed_times
        except (OSError, ValueError, TypeError):
            plan_matches = False
    ready = (0 < frame_count <= 60 and success == frame_count and selective_schema
             and plan_matches and status not in {"extracting_frames", "recognizing_frames"})
    return {
        "exists": True, "ready": ready, "frame_count": frame_count,
        "success": success, "failed": failed, "status": status,
        "interval": float(payload.get("frame_interval") or 0.0),
        "plan_matches": plan_matches,
    }


def _visual_ready(folder: Path) -> bool:
    return _visual_stats(folder)["ready"]


def _run_visual(settings: AppSettings, *, force: bool, interval: float = 0.0, workers: int = 3,
                target_frames: int = 0) -> dict:
    key = _dashscope_key(settings)
    if not key:
        raise SystemExit("缺少百炼 DASHSCOPE_API_KEY。请用 `dy set-key --dashscope <KEY>` 配置。")
    folder = Path(settings.material_folder)
    sample_times = None
    if interval <= 0:
        from backend.selective_visual import build_selective_visual_plan
        from backend.shot_index import build_shot_index
        build_shot_index(folder, force=False)
        vis = settings.visual
        plan = build_selective_visual_plan(
            folder,
            target=target_frames or vis.selective_target_frames,
            minimum=vis.selective_min_frames,
            maximum=vis.selective_max_frames,
        )
        sample_times = {1: plan["times"]}
    mode = "重跑" if force else "识别/续跑"
    vis = settings.visual
    _has_gallery = (folder / vis.face_gallery_file).exists() or (folder.parent / vis.face_gallery_file).exists()
    face_hint = "开" if vis.use_face_gallery and _has_gallery else "关/无库"
    sampling = (f"候选复核 {len(sample_times[1])} 帧" if sample_times else f"诊断密集抽帧 {interval:g}s")
    print(f"→ 视觉{mode}（模型 {settings.api.visual_model}，并发 {workers}，{sampling}，"
          f"{vis.frame_width}×{vis.frame_height}，每批 {vis.batch}，人脸库 {face_hint}）…")
    result = build_source_index(
        settings,
        siliconflow_api_key=key,
        visual_model=settings.api.visual_model,
        frame_interval=interval,
        visual_batch_size=settings.visual.batch,
        visual_delay_sec=1.0,
        visual_workers=workers,
        force_visual=force,
        enable_visual_model=True,
        visual_sample_times=sample_times,
    )
    fc = int(result.get("visual_frame_count") or 0)
    sc = int(result.get("visual_success_count") or 0)
    failed = int(result.get("visual_failed_count") or 0)
    tail = f"，仍有 {failed} 帧失败（可 `dy visual` 续跑或 `run --resume`）" if failed else ""
    print(f"  视觉识别完成：成功 {sc}/{fc} 帧{tail}")
    return result


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_detect(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    media = detect_materials(settings.material_folder, settings.drama.source_count)
    print(f"素材文件夹：{settings.material_folder}")
    print(f"主原片：{Path(media.video_path).name} · {media.width}x{media.height} · {media.duration:.1f}s · {media.video_codec}")
    print(f"字幕：{', '.join(Path(p).name for p in media.subtitle_paths)}")
    script = find_manual_script_file(Path(settings.material_folder))
    print(f"文案：{script.name if script else '（未找到，请放入 原片/解说 文案）'}")
    for warning in media.warnings:
        print(f"  ⚠ {warning}")


def cmd_visual(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    save_settings(settings)
    _run_visual(settings, force=args.force, interval=args.interval, workers=args.workers,
                target_frames=args.target_frames)


def cmd_shots(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    from backend.shot_index import build_shot_index

    folder = Path(settings.material_folder)
    print(f"→ CPU 镜头检测：{folder}（阈值 {args.threshold:g}）…", flush=True)
    result = build_shot_index(folder, threshold=args.threshold, force=args.force)
    durations = sorted(float(item["duration"]) for item in result.get("shots", []))
    median = durations[len(durations) // 2] if durations else 0.0
    print(f"镜头索引完成：{result.get('shot_count', 0)} 镜头，中位时长 {median:.2f}s"
          f" → {folder / '_source_shot_index.json'}")


def cmd_events(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    from backend.event_index import build_event_index

    folder = Path(settings.material_folder)
    result = build_event_index(folder, force=args.force)
    durations = sorted(float(item["duration"]) for item in result.get("events", []))
    median = durations[len(durations) // 2] if durations else 0.0
    print(f"事件索引完成：{result.get('event_count', 0)} 个事件块，中位时长 {median:.2f}s"
          f" → {folder / '_source_event_index.json'}")


def cmd_shadow_match(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    from backend.hierarchical_matcher import build_shadow_report
    folder = Path(settings.material_folder)
    result = build_shadow_report(folder, settings.matching, settings.visual)
    print(f"分层影子匹配完成：{len(result.get('segments', []))} 个解说分镜"
          f" → {folder / '★ 分层影子匹配报告.json'}")
    print(f"新旧并排对比 → {folder / '★ 新旧匹配并排对比.json'}")
    summary = result.get("planning_summary", {})
    visual_plan = result.get("visual_plan", {})
    state = "ready" if visual_plan.get("ready") else "pending dy visual + shadow-match"
    print(f"  selective visual review: {visual_plan.get('frame_count', 0)} frames ({state})"
          f" -> {folder / '_selective_visual_plan.json'}")
    print(f"接管预演：就绪 {summary.get('ready', 0)} / 未解决 {summary.get('unresolved', 0)}"
          f" → {folder / '★ 分层接管预演报告.json'}")


def cmd_voices(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    from backend.voice_index import build_voice_index
    folder = Path(settings.material_folder)
    try:
        result = build_voice_index(
            folder,
            force=args.force,
            threshold=settings.matching.voice_similarity_threshold,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"CAM++ 声纹索引完成：{result.get('segment_count', 0)} 个说话区间 / "
          f"角色 {', '.join(result.get('reference_roles', [])) or '未映射'}"
          f" → {folder / '_source_voice_index.json'}")


def cmd_script(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    save_settings(settings)
    table = generate_manual_script_table(settings)
    validation = table.get("validation", {})
    print(f"脚本表已生成：原片 {validation.get('source_clips', 0)} 段 · "
          f"解说 {validation.get('narration_blocks', 0)} 段 · {len(table.get('narration_text', ''))} 字")
    low = validation.get("low_match_rows") or []
    if low:
        print(f"  ⚠ 低匹配度原片行：{low}")


def cmd_deliver(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    from backend.delivery import run_delivery

    report = run_delivery(settings)
    print("发布交付完成：")
    print(f"  发布信息：{report['publish']['file']}")
    print(f"  剪映字幕：{report['jianying']['file']}（{report['jianying']['line_count']} 行）")
    print(f"  交付清单：{Path(settings.material_folder) / '★ 交付清单.json'}")


def cmd_run(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    save_settings(settings)
    folder = Path(settings.material_folder)

    if not getattr(args, "no_render", False):
        if not getattr(args, "hierarchical_match", False):
            raise SystemExit("正式渲染只允许分层匹配管线：必须使用 --hierarchical-match")
        try:
            validate_scene_map(folder)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc

    print("═══ DY 工作流 ═══")
    media = detect_materials(settings.material_folder, settings.drama.source_count)
    print(f"[1/4] 素材：{Path(media.video_path).name} · {media.duration:.1f}s · 字幕 {len(media.subtitle_paths)} 个")
    if not find_manual_script_file(folder):
        raise SystemExit("缺少“原片/解说”文案文件（txt/md/docx）。")

    print("[2/4] 视觉识别")
    stats = _visual_stats(folder)
    if args.skip_visual:
        if not stats["ready"]:
            raise SystemExit("--skip-visual 需要已有可用视觉索引，但未检测到。请先运行 `dy visual`。")
        print(f"  复用已有视觉索引（--skip-visual），成功 {stats['success']}/{stats['frame_count']} 帧")
    elif args.force_visual:
        _run_visual(settings, force=True, interval=0, workers=args.workers,
                    target_frames=args.target_frames)
    elif stats["ready"] and not args.resume and stats["failed"] == 0:
        print(f"  复用已有视觉索引，成功 {stats['success']}/{stats['frame_count']} 帧（--force-visual 重跑）")
    elif stats["ready"] and stats["failed"] > 0 and not args.resume:
        print(f"  已有索引但 {stats['failed']} 帧失败，自动续跑抢救…")
        _run_visual(settings, force=False, interval=0, workers=args.workers,
                    target_frames=args.target_frames)
    else:
        _run_visual(settings, force=False, interval=0, workers=args.workers,
                    target_frames=args.target_frames)

    print("[3/4] 生成脚本表")
    table = generate_manual_script_table(settings)
    validation = table.get("validation", {})
    print(f"  原片 {validation.get('source_clips', 0)} 段 · 解说 {validation.get('narration_blocks', 0)} 段")

    print("[4/4] 配音 + 剪辑 + 后处理")
    if getattr(args, "no_render", False):
        runner.render(settings, on_line=lambda line: print(f"  {line}"),
                      concurrency=args.concurrency, no_render=True,
                      hierarchical_match=args.hierarchical_match)
        report = Path(settings.material_folder) / "★ 匹配报告.json"
        print(f"\n匹配完成（未成片）：{report}")
        return
    output = runner.render(settings, on_line=lambda line: print(f"  {line}"), concurrency=args.concurrency,
                           hierarchical_match=args.hierarchical_match)
    print(f"\n成片完成：{output}")


def cmd_clean(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    removed, reclaimed = cleanup_render_artifacts(Path(settings.material_folder))
    print(f"已清理 {len(removed)} 项可重建中间产物，释放 {reclaimed / 1024 / 1024:.1f} MB")


def cmd_status(args: argparse.Namespace) -> None:
    settings = load_settings(mask_keys=False)
    if args.folder:
        settings.material_folder = str(Path(args.folder).expanduser().resolve())
    if not settings.material_folder:
        raise SystemExit("未设置素材文件夹。请用 `dy set --folder <路径>`。")
    folder = Path(settings.material_folder)
    print(f"素材文件夹：{folder}")
    if not folder.is_dir():
        print("  ✗ 文件夹不存在")
        return
    try:
        media = detect_materials(settings.material_folder, settings.drama.source_count)
        print(f"  原片 ✓ {Path(media.video_path).name} ({media.duration:.1f}s)")
        print(f"  字幕 ✓ {len(media.subtitle_paths)} 个")
    except Exception as exc:
        print(f"  素材 ✗ {exc}")
    print(f"  文案 {'✓' if find_manual_script_file(folder) else '✗ 未找到'}")
    vs = _visual_stats(folder)
    if vs["ready"]:
        tail = f"（{vs['failed']} 帧失败，可续跑）" if vs["failed"] else ""
        print(f"  视觉索引 ✓ 就绪 {vs['success']}/{vs['frame_count']} 帧{tail}")
    else:
        print("  视觉索引 ✗ 未就绪")
    # 人脸库（认「谁」的主力）；全集共享时在剧集根
    gallery_path = folder / settings.visual.face_gallery_file
    if not gallery_path.exists() and (folder.parent / settings.visual.face_gallery_file).exists():
        gallery_path = folder.parent / settings.visual.face_gallery_file
    if gallery_path.exists():
        try:
            g = json.loads(gallery_path.read_text("utf-8"))
            loc = "剧集根" if gallery_path.parent == folder.parent else "本集"
            print(f"  人脸库 ✓ {int(g.get('role_count', 0))} 角色 / {int(g.get('vector_count', 0))} 参考照（{loc}）")
        except (OSError, json.JSONDecodeError):
            print("  人脸库 ⚠ 存在但损坏（`dy faces build` 重建）")
    else:
        print("  人脸库 · 未建（`dy faces build`，缺则退回纯 VL 描述）")
    voice_path = folder / "_source_voice_index.json"
    if voice_path.exists():
        try:
            voice = json.loads(voice_path.read_text("utf-8"))
            print(f"  CAM++声纹 ✓ {int(voice.get('segment_count', 0))} 说话区间 / "
                  f"{len(voice.get('reference_roles', []))} 角色")
        except (OSError, json.JSONDecodeError):
            print("  CAM++声纹 ⚠ 索引损坏（`dy voices --force` 重建）")
    else:
        print("  CAM++声纹 · 未建（可选；`dy voices`）")
    print(f"  脚本表 {'✓' if (folder / SCRIPT_TABLE_FILE).exists() else '✗ 未生成'}")
    print(f"  成片 {'✓ ' + str(folder / '★ 成片.mp4') if (folder / '★ 成片.mp4').exists() else '✗ 未生成'}")


def cmd_config(args: argparse.Namespace) -> None:
    settings = load_settings(mask_keys=True)
    print(json.dumps(settings.model_dump(), ensure_ascii=False, indent=2))


def cmd_set(args: argparse.Namespace) -> None:
    settings = load_settings(mask_keys=False)
    if args.folder:
        settings.material_folder = str(Path(args.folder).expanduser().resolve())
    if args.resolution:
        settings.video.resolution = args.resolution
    if args.visual_model:
        settings.api.visual_model = args.visual_model
    save_settings(settings)
    print(f"已保存。素材文件夹：{settings.material_folder or '(未设置)'} · 分辨率：{settings.video.resolution} · 视觉模型：{settings.api.visual_model}")


def cmd_setkey(args: argparse.Namespace) -> None:
    secrets = read_secrets()
    if args.dashscope:
        secrets["dashscope_api_key"] = args.dashscope.strip()
    if args.siliconflow:
        secrets["siliconflow_api_key"] = args.siliconflow.strip()
    if not secrets:
        raise SystemExit("未提供任何 API Key。用 --dashscope / --siliconflow。")
    write_secrets(secrets)
    print("API Key 已加密保存到 config/secrets.bin。")


def cmd_doctor(args: argparse.Namespace) -> None:
    ok = True
    # FFmpeg
    try:
        from backend.media_tools import ffmpeg, ffprobe
        print(f"✓ ffmpeg   {ffmpeg()}")
        print(f"✓ ffprobe  {ffprobe()}")
    except Exception as exc:
        ok = False
        print(f"✗ FFmpeg 未找到：{exc}")
    # Python deps（主环境；numpy 仅 GPT-SoVITS 引擎自带环境需要，不在此检查）
    for module in ("dashscope", "pydantic", "cryptography"):
        try:
            __import__(module)
            print(f"✓ 依赖 {module}")
        except ImportError:
            ok = False
            print(f"✗ 缺少依赖 {module}（pip install -r requirements.txt）")
    # API key
    settings = load_settings(mask_keys=False)
    print(f"{'✓' if _dashscope_key(settings) else '✗'} 百炼 DASHSCOPE_API_KEY {'已配置' if _dashscope_key(settings) else '未配置'}")
    # 人脸识别（可选：认「谁」的主力，缺则退回纯 VL 描述，不阻塞）
    try:
        from backend.face_gallery import insightface_available
        if insightface_available():
            print("✓ 人脸识别 insightface 就绪（认演员/角色）")
        else:
            print("· 人脸识别 insightface 未装（可选；`pip install insightface onnxruntime opencv-python-headless`）")
    except Exception:
        print("· 人脸识别 insightface 未装（可选）")
    try:
        __import__("speakerlab")
        print("✓ CAM++ speakerlab 就绪（有 _voices 参考音频时可建角色声纹）")
    except ImportError:
        print("· CAM++ speakerlab 未装（可选；`pip install -r requirements-audio.txt`）")
    # Material folder
    folder = settings.material_folder
    if folder and Path(folder).is_dir():
        print(f"✓ 素材文件夹 {folder}")
    else:
        print(f"· 素材文件夹 {folder or '(未设置)'}")
    print("环境自检" + ("通过。" if ok else "存在问题，请按上方 ✗ 处理。"))
    if not ok:
        sys.exit(1)


def cmd_preflight(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    folder = Path(settings.material_folder)
    ok = True
    print("═══ DY 工作流 preflight ═══")
    print(f"素材文件夹：{folder}")
    # 1 素材（原片 + 字幕）
    try:
        media = detect_materials(settings.material_folder, settings.drama.source_count)
        print(f"✓ 原片 {Path(media.video_path).name} ({media.duration:.1f}s) · 字幕 {len(media.subtitle_paths)} 个")
        for warning in media.warnings:
            print(f"  ⚠ {warning}")
    except Exception as exc:
        ok = False
        print(f"✗ 素材：{exc}")
    # 2 文案
    script = find_manual_script_file(folder)
    if script:
        print(f"✓ 文案 {script.name}")
    else:
        ok = False
        print("✗ 文案：缺少“原片：/解说：”文案文件（txt/md/docx）")
    # 3 API Key
    if _dashscope_key(settings):
        print("✓ 百炼 DASHSCOPE_API_KEY 已配置")
    else:
        ok = False
        print("✗ 百炼 DASHSCOPE_API_KEY 未配置（dy set-key --dashscope <KEY>）")
    # 4 FFmpeg
    try:
        from backend.media_tools import ffmpeg, ffprobe
        ffmpeg(); ffprobe()
        print("✓ FFmpeg 就绪")
    except Exception as exc:
        ok = False
        print(f"✗ FFmpeg：{exc}")
    # 5 视觉索引 / 脚本表（run 会自动生成，仅提示）
    vs = _visual_stats(folder)
    if vs["ready"]:
        tail = f"（{vs['failed']} 帧失败，可续跑）" if vs["failed"] else ""
        print(f"· 视觉索引 已就绪 {vs['success']}/{vs['frame_count']} 帧{tail}")
    else:
        print("· 视觉索引 未就绪（run 会自动生成）")
    print("· 脚本表 " + ("已生成" if (folder / SCRIPT_TABLE_FILE).exists() else "未生成（run 会自动生成）"))
    print("preflight " + ("通过，可直接 `dy run`。" if ok else "不通过，请先处理上方 ✗。"))
    if not ok:
        sys.exit(1)


def cmd_concurrency(args: argparse.Namespace) -> None:
    from backend.concurrency import detect_optimal_concurrency, get_concurrency, set_concurrency
    if args.set:
        print(f"并发已固定为 {set_concurrency(args.set)}（写入 concurrency_profile.json）")
    elif args.benchmark:
        print("运行并发基准测试（会生成临时测试视频，稍候）…")
        print(f"基准最优并发 = {detect_optimal_concurrency()}，已缓存")
    else:
        print(f"当前渲染并发 = {get_concurrency()}")


def _faces_locate(settings: AppSettings, *, here: bool = False) -> tuple[Path, Path]:
    """返回 (参考照目录, 人脸库文件)。默认用剧集根（素材夹父目录）以全集共享；
    `here=True` 强制用当前单集夹。读取时若单集夹已有库/参考照则优先用单集。"""
    ep = Path(settings.material_folder)
    root = ep.parent
    fd, gf = settings.visual.faces_dir, settings.visual.face_gallery_file
    if here:
        base = ep
    elif (ep / gf).exists() or (ep / fd).is_dir():
        base = ep
    else:
        base = root
    return base / fd, base / gf


def cmd_faces(args: argparse.Namespace) -> None:
    import shutil

    from backend import face_gallery as fg

    settings = _resolve_settings(getattr(args, "folder", None))
    faces_root, gallery_path = _faces_locate(settings, here=getattr(args, "here", False))

    if args.faces_action == "add":
        role = args.role.strip()
        role_dir = faces_root / role
        role_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for src in args.images:
            src_path = Path(src).expanduser()
            if not src_path.is_file():
                print(f"  ⚠ 跳过（不存在）：{src_path}")
                continue
            dest = role_dir / src_path.name
            shutil.copy2(src_path, dest)
            copied += 1
        # 更新 roster（角色→演员）
        if args.actor:
            roster_path = faces_root / fg.ROSTER_FILE
            roster = {}
            if roster_path.exists():
                try:
                    roster = json.loads(roster_path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    roster = {}
            roster[role] = args.actor.strip()
            roster_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2), "utf-8")
        print(f"已添加 {copied} 张参考照到 {role_dir}"
              + (f"（演员：{args.actor}）" if args.actor else ""))
        print("提示：加完所有角色后运行 `dy faces build` 重建人脸库。")
        return

    if args.faces_action == "list":
        gallery = fg.load_gallery(gallery_path)
        if not gallery:
            print(f"未找到人脸库 {gallery_path}。放好 {faces_root}/<角色>/*.jpg 后运行 `dy faces build`。")
            # 顺便列出已有参考照目录
            if faces_root.is_dir():
                print(f"参考照目录 {faces_root}：")
                for d in sorted(p for p in faces_root.iterdir() if p.is_dir()):
                    n = len([f for f in d.iterdir() if f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}])
                    print(f"  · {d.name}：{n} 张")
            return
        roster = gallery.get("roster", {})
        print(f"人脸库 {gallery_path}")
        print(f"  角色 {gallery.get('role_count', 0)} 个 · 参考向量 {gallery.get('vector_count', 0)} 条")
        for role, vecs in gallery.get("roles", {}).items():
            actor = roster.get(role, "")
            print(f"  · {role}{'（' + actor + '）' if actor else ''}：{len(vecs)} 条")
        return

    # build
    if not fg.insightface_available():
        raise SystemExit("未安装 insightface，无法建库。请先 `pip install insightface onnxruntime opencv-python-headless`。")
    if not faces_root.is_dir():
        raise SystemExit(f"参考照目录不存在：{faces_root}\n请先 `dy faces add <角色> <图...>` 或手动放入 {faces_root}/<角色>/*.jpg")
    print(f"从 {faces_root} 建人脸库（首次会下载 buffalo_l 模型，稍候）…")
    gallery = fg.build_gallery(faces_root, det_size=settings.visual.face_det_size)
    fg.save_gallery(gallery, gallery_path)
    print(f"✓ 人脸库已建：{gallery.get('role_count', 0)} 个角色 / {gallery.get('vector_count', 0)} 条向量 → {gallery_path}")
    if gallery.get("skipped"):
        print(f"  ⚠ {len(gallery['skipped'])} 张未检出人脸（已跳过）：")
        for s in gallery["skipped"][:8]:
            print(f"    · {s}")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dy",
        description="DY 工作流 — 电视剧/短剧全自动智能剪辑（纯后端）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="正式管线：选择性视觉复核→脚本表→分层接管→成片")
    p_run.add_argument("--folder", help="素材文件夹（覆盖已保存路径）")
    p_run.add_argument("--force-visual", action="store_true", help="强制重跑视觉识别（清空重来）")
    p_run.add_argument("--resume", action="store_true", help="续跑视觉识别：复用已识别帧，只重试失败帧")
    p_run.add_argument("--skip-visual", action="store_true", help="跳过视觉识别（需已有索引）")
    p_run.add_argument("--target-frames", type=int, default=45,
                       help="选择性视觉复核帧数（30-60，默认45）")
    p_run.add_argument("--workers", type=int, default=3, help="视觉识别并发批数（默认3）")
    p_run.add_argument("--concurrency", type=int, default=None, help="渲染并发（覆盖，跳过基准）")
    p_run.add_argument("--no-render", action="store_true", help="只做匹配并写匹配报告/字幕，不成片")
    p_run.add_argument("--hierarchical-match", action="store_true",
                       help="使用已验证的 ★ 分层接管预演报告 覆盖正式解说画面")
    p_run.set_defaults(func=cmd_run)

    p_detect = sub.add_parser("detect", help="检测素材（原片/字幕/文案）")
    p_detect.add_argument("--folder")
    p_detect.set_defaults(func=cmd_detect)

    p_pre = sub.add_parser("preflight", help="一次性红绿灯：素材/文案/Key/FFmpeg 是否就绪")
    p_pre.add_argument("--folder")
    p_pre.set_defaults(func=cmd_preflight)

    p_visual = sub.add_parser("visual", help="候选驱动视觉复核（默认45帧，硬限制30-60）")
    p_visual.add_argument("--folder")
    p_visual.add_argument("--force", action="store_true", help="强制重跑（清空重来）")
    p_visual.add_argument("--interval", type=float, default=0.0, help="抽帧间隔秒（默认自适应/复用）")
    p_visual.add_argument("--target-frames", type=int, default=45,
                          help="选择性视觉复核帧数（30-60，默认45）")
    p_visual.add_argument("--workers", type=int, default=3, help="并发批数（默认3）")
    p_visual.set_defaults(func=cmd_visual)

    p_shots = sub.add_parser("shots", help="CPU 镜头边界检测，建立多关键时间点镜头索引")
    p_shots.add_argument("--folder")
    p_shots.add_argument("--threshold", type=float, default=8.0, help="转场阈值 0-100（默认8）")
    p_shots.add_argument("--force", action="store_true", help="忽略缓存强制重建")
    p_shots.set_defaults(func=cmd_shots)

    p_events = sub.add_parser("events", help="把物理镜头合并为大场景内的短事件块")
    p_events.add_argument("--folder")
    p_events.add_argument("--force", action="store_true")
    p_events.set_defaults(func=cmd_events)

    p_shadow = sub.add_parser("shadow-match", help="分层影子匹配；不修改正式成片时间线")
    p_shadow.add_argument("--folder")
    p_shadow.set_defaults(func=cmd_shadow_match)

    p_voices = sub.add_parser("voices", help="建立 CAM++ 说话人/角色声纹索引")
    p_voices.add_argument("--folder")
    p_voices.add_argument("--force", action="store_true")
    p_voices.set_defaults(func=cmd_voices)

    p_clean = sub.add_parser("clean", help="清理旧裁切片、旧配音、渲染临时文件；保留原片/场景地图/索引/成片")
    p_clean.add_argument("--folder")
    p_clean.set_defaults(func=cmd_clean)

    p_script = sub.add_parser("script", help="生成脚本表（对齐字幕/文案）")
    p_script.add_argument("--folder")
    p_script.set_defaults(func=cmd_script)

    p_deliver = sub.add_parser("deliver", help="为已有成片生成发布信息/剪映字幕并执行交付校验")
    p_deliver.add_argument("--folder")
    p_deliver.set_defaults(func=cmd_deliver)

    p_status = sub.add_parser("status", help="查看当前工作流进度")
    p_status.add_argument("--folder")
    p_status.set_defaults(func=cmd_status)

    p_config = sub.add_parser("config", help="打印当前配置（Key 已掩码）")
    p_config.set_defaults(func=cmd_config)

    p_set = sub.add_parser("set", help="设置素材文件夹/分辨率/视觉模型")
    p_set.add_argument("--folder")
    p_set.add_argument("--resolution", choices=["720P", "1080P", "2K", "4K"])
    p_set.add_argument("--visual-model", dest="visual_model")
    p_set.set_defaults(func=cmd_set)

    p_key = sub.add_parser("set-key", help="加密保存 API Key")
    p_key.add_argument("--dashscope", help="百炼 DashScope API Key（视觉 + 配音共用）")
    p_key.add_argument("--siliconflow", help="SiliconFlow API Key（可选）")
    p_key.set_defaults(func=cmd_setkey)

    p_doctor = sub.add_parser("doctor", help="环境自检（ffmpeg / 依赖 / API Key）")
    p_doctor.set_defaults(func=cmd_doctor)

    p_conc = sub.add_parser("concurrency", help="查看/固定渲染并发（跳过基准）")
    p_conc.add_argument("--set", type=int, help="手动固定并发数")
    p_conc.add_argument("--benchmark", action="store_true", help="运行基准测试并缓存最优并发")
    p_conc.set_defaults(func=cmd_concurrency)

    p_faces = sub.add_parser("faces", help="人脸库：建库/加参考照/查看（认清画面里是谁）")
    p_faces.add_argument("--folder", help="素材文件夹（覆盖已保存路径）")
    faces_sub = p_faces.add_subparsers(dest="faces_action", required=True)
    pf_build = faces_sub.add_parser("build", help="从 <剧集根>/_faces/<角色>/*.jpg 建人脸库（默认剧集根，全集共享）")
    pf_build.add_argument("--folder")
    pf_build.add_argument("--here", action="store_true", help="建到当前单集夹而非剧集根")
    pf_add = faces_sub.add_parser("add", help="添加某角色的参考照并可选记录演员名")
    pf_add.add_argument("role", help="角色名（如 黄亦玫）")
    pf_add.add_argument("images", nargs="+", help="一张或多张清晰正脸图片路径")
    pf_add.add_argument("--actor", help="该角色的演员名（写入 roster，索引会标成「演员（饰角色）」）")
    pf_add.add_argument("--folder")
    pf_add.add_argument("--here", action="store_true", help="加到当前单集夹而非剧集根")
    pf_list = faces_sub.add_parser("list", help="查看当前人脸库/参考照目录")
    pf_list.add_argument("--folder")
    pf_list.add_argument("--here", action="store_true", help="只看当前单集夹")
    p_faces.set_defaults(func=cmd_faces)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
