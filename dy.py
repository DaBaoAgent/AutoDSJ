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

from backend import runner
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
    ready = frame_count > 0 and success > 0 and status not in {"extracting_frames", "recognizing_frames"}
    return {
        "exists": True, "ready": ready, "frame_count": frame_count,
        "success": success, "failed": failed, "status": status,
        "interval": float(payload.get("frame_interval") or 0.0),
    }


def _visual_ready(folder: Path) -> bool:
    return _visual_stats(folder)["ready"]


def _run_visual(settings: AppSettings, *, force: bool, interval: float = 0.0, workers: int = 3) -> dict:
    key = _dashscope_key(settings)
    if not key:
        raise SystemExit("缺少百炼 DASHSCOPE_API_KEY。请用 `dy set-key --dashscope <KEY>` 配置。")
    folder = Path(settings.material_folder)
    # 续跑（非强制、未指定间隔）时复用已有索引的抽帧间隔，保证帧 ID 对齐、命中缓存
    if not force and interval <= 0:
        existing = _visual_stats(folder)
        if existing.get("exists") and existing.get("interval"):
            interval = float(existing["interval"])
    mode = "重跑" if force else "识别/续跑"
    print(f"→ 视觉{mode}（模型 {settings.api.visual_model}，并发 {workers}，抽帧 {interval or '自适应'}）…")
    result = build_source_index(
        settings,
        siliconflow_api_key=key,
        visual_model=settings.api.visual_model,
        frame_interval=interval,
        visual_batch_size=8,
        visual_delay_sec=1.0,
        visual_workers=workers,
        force_visual=force,
        enable_visual_model=True,
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
    _run_visual(settings, force=args.force, interval=args.interval, workers=args.workers)


def cmd_script(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    save_settings(settings)
    if not _visual_ready(Path(settings.material_folder)):
        raise SystemExit("视觉索引未就绪。请先运行 `dy visual`。")
    table = generate_manual_script_table(settings)
    validation = table.get("validation", {})
    print(f"脚本表已生成：原片 {validation.get('source_clips', 0)} 段 · "
          f"解说 {validation.get('narration_blocks', 0)} 段 · {len(table.get('narration_text', ''))} 字")
    low = validation.get("low_match_rows") or []
    if low:
        print(f"  ⚠ 低匹配度原片行：{low}")


def cmd_run(args: argparse.Namespace) -> None:
    settings = _resolve_settings(args.folder)
    save_settings(settings)
    folder = Path(settings.material_folder)

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
        _run_visual(settings, force=True, interval=args.interval, workers=args.workers)
    elif stats["ready"] and not args.resume and stats["failed"] == 0:
        print(f"  复用已有视觉索引，成功 {stats['success']}/{stats['frame_count']} 帧（--force-visual 重跑）")
    elif stats["ready"] and stats["failed"] > 0 and not args.resume:
        print(f"  已有索引但 {stats['failed']} 帧失败，自动续跑抢救…")
        _run_visual(settings, force=False, interval=args.interval, workers=args.workers)
    else:
        _run_visual(settings, force=False, interval=args.interval, workers=args.workers)

    print("[3/4] 生成脚本表")
    table = generate_manual_script_table(settings)
    validation = table.get("validation", {})
    print(f"  原片 {validation.get('source_clips', 0)} 段 · 解说 {validation.get('narration_blocks', 0)} 段")

    print("[4/4] 配音 + 剪辑 + 后处理")
    output = runner.render(settings, on_line=lambda line: print(f"  {line}"), concurrency=args.concurrency)
    print(f"\n✔ 成片完成：{output}")


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


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dy",
        description="DY 工作流 — 电视剧/短剧全自动智能剪辑（纯后端）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="一键全流程：检测→视觉→脚本表→成片")
    p_run.add_argument("--folder", help="素材文件夹（覆盖已保存路径）")
    p_run.add_argument("--force-visual", action="store_true", help="强制重跑视觉识别（清空重来）")
    p_run.add_argument("--resume", action="store_true", help="续跑视觉识别：复用已识别帧，只重试失败帧")
    p_run.add_argument("--skip-visual", action="store_true", help="跳过视觉识别（需已有索引）")
    p_run.add_argument("--interval", type=float, default=0.0, help="抽帧间隔秒（默认自适应）")
    p_run.add_argument("--workers", type=int, default=3, help="视觉识别并发批数（默认3）")
    p_run.add_argument("--concurrency", type=int, default=None, help="渲染并发（覆盖，跳过基准）")
    p_run.set_defaults(func=cmd_run)

    p_detect = sub.add_parser("detect", help="检测素材（原片/字幕/文案）")
    p_detect.add_argument("--folder")
    p_detect.set_defaults(func=cmd_detect)

    p_pre = sub.add_parser("preflight", help="一次性红绿灯：素材/文案/Key/FFmpeg 是否就绪")
    p_pre.add_argument("--folder")
    p_pre.set_defaults(func=cmd_preflight)

    p_visual = sub.add_parser("visual", help="运行视觉识别，建立视觉索引（默认续跑）")
    p_visual.add_argument("--folder")
    p_visual.add_argument("--force", action="store_true", help="强制重跑（清空重来）")
    p_visual.add_argument("--interval", type=float, default=0.0, help="抽帧间隔秒（默认自适应/复用）")
    p_visual.add_argument("--workers", type=int, default=3, help="并发批数（默认3）")
    p_visual.set_defaults(func=cmd_visual)

    p_script = sub.add_parser("script", help="生成脚本表（对齐字幕/文案）")
    p_script.add_argument("--folder")
    p_script.set_defaults(func=cmd_script)

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

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
