"""Lean, CLI-facing pipeline orchestrator for the DY 工作流.

This replaces the old FastAPI + WebSocket ``JobManager``. It builds the
``anchored_pipeline.py`` command from :class:`AppSettings`, runs it as a
subprocess while streaming stdout to the console, then applies the
post-processing pass (resolution / padding / subtitle re-timing).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .concurrency import get_concurrency
from .config_store import runtime_env
from .manual_script import SCRIPT_TABLE_FILE
from .media import detect_materials
from .postprocess import run_postprocess
from .schemas import AppSettings

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
PIPELINE = ROOT / "anchored_pipeline.py"
OUTPUT_NAME = "★ 成片.mp4"

LogFn = Callable[[str], None]


def _log(on_line: LogFn | None, message: str) -> None:
    (on_line or print)(message)


def build_pipeline_command(settings: AppSettings, *, concurrency: int | None = None) -> list[str]:
    """Translate settings into the ``anchored_pipeline.py`` argv."""
    media = detect_materials(settings.material_folder, settings.drama.source_count)
    target_seconds = max(30.0, media.duration - settings.video.trim_head - settings.video.trim_tail)
    ratio = max(0.05, min(1.0, target_seconds / media.duration))

    voice = settings.voice
    if voice.mode == "clone" and voice.provider == "qwen":
        backend = "qwen-clone"
        voice_args = [
            "--qwen-voice", voice.clone_voice_id,
            "--qwen-model", voice.qwen_clone_model,
            "--qwen-reference-audio", voice.qwen_reference_audio,
            "--qwen-reference-text-path", voice.qwen_reference_text_path,
            "--qwen-volume", str(voice.volume),
            "--qwen-pitch", str(voice.pitch),
        ]
    elif voice.mode == "clone" and voice.provider == "gpt_sovits":
        reference = Path(voice.gpt_sovits_reference_audio)
        engine = Path(voice.gpt_sovits_engine_path)
        if not engine.is_dir():
            raise RuntimeError(f"本地 GPT-SoVITS 引擎不存在：{engine}")
        if not reference.is_file():
            raise RuntimeError(f"GPT-SoVITS 参考音频不存在：{reference}")
        if not voice.gpt_sovits_reference_text.strip():
            raise RuntimeError("请填写参考音频对应文字")
        backend = "gpt-sovits"
        voice_args = [
            "--gpt-sovits", str(engine), "--reference", str(reference),
            "--prompt-text", voice.gpt_sovits_reference_text,
            "--gpt-sovits-seed", str(voice.gpt_sovits_seed),
            "--gpt-sovits-text-split-method", voice.gpt_sovits_text_split_method,
            "--gpt-sovits-temperature", str(voice.gpt_sovits_temperature),
            "--gpt-sovits-top-p", str(voice.gpt_sovits_top_p),
            "--gpt-sovits-top-k", str(voice.gpt_sovits_top_k),
            "--gpt-sovits-repetition-penalty", str(voice.gpt_sovits_repetition_penalty),
        ]
    elif voice.mode == "system":
        backend = "qwen-clone"
        voice_args = [
            "--qwen-voice", voice.system_voice,
            "--qwen-model", "qwen3-tts-flash-realtime",
            "--qwen-volume", str(voice.volume),
            "--qwen-pitch", str(voice.pitch),
        ]
    else:
        backend, voice_args = "cosyvoice", []

    source_volume = max(0.0, min(1.0, float(settings.drama.source_play_volume) / 100.0))
    narration_source_volume = max(0.0, min(1.0, float(settings.drama.narration_source_volume) / 100.0))

    cmd = [
        sys.executable, "-u", str(PIPELINE),
        settings.material_folder,
        "--ratio", f"{ratio:.8f}",
        "--target-seconds", f"{target_seconds:.3f}",
        "--tts-backend", backend,
        "--speech-rate", str(voice.speech_rate),
        "--trim-head", str(settings.video.trim_head),
        "--trim-tail", str(settings.video.trim_tail),
        *voice_args,
    ]
    if settings.drama.keep_source_audio:
        cmd += [
            "--include-source-audio",
            "--source-volume", f"{source_volume:.4f}",
            "--narration-source-volume", f"{narration_source_volume:.4f}",
        ]
    if voice.mode == "clone" and voice.provider == "gpt_sovits" and voice.polish_audio:
        cmd.append("--polish")
    cmd += ["--concurrency", str(concurrency if concurrency and concurrency > 0 else get_concurrency())]
    return cmd


def ensure_script_table(settings: AppSettings) -> None:
    table_path = Path(settings.material_folder) / SCRIPT_TABLE_FILE
    if not table_path.exists():
        raise RuntimeError("缺少脚本表，请先运行 `dy script` 生成脚本表")


def _stream_subprocess(cmd: list[str], settings: AppSettings, on_line: LogFn | None) -> None:
    env = runtime_env(settings)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=creationflags,
    )
    assert process.stdout
    recent: list[str] = []
    for line in process.stdout:
        line = line.rstrip()
        if line:
            recent.append(line)
            recent = recent[-12:]
            _log(on_line, line)
    code = process.wait()
    if code != 0:
        detail = "\n".join(recent[-8:])
        raise RuntimeError(f"成片内核退出，代码 {code}" + (f"\n{detail}" if detail else ""))


def render(settings: AppSettings, *, on_line: LogFn | None = None, concurrency: int | None = None) -> Path:
    """Run the anchored pipeline + post-process into the final ``★ 成片.mp4``."""
    folder = Path(settings.material_folder)
    ensure_script_table(settings)
    media = detect_materials(settings.material_folder, settings.drama.source_count)
    _log(on_line, f"主原片：{Path(media.video_path).name}")
    _log(
        on_line,
        "音频规则：原片音量 "
        f"{settings.drama.source_play_volume}% / 解说段原片 "
        f"{settings.drama.narration_source_volume}% / 配音 100%",
    )
    _stream_subprocess(build_pipeline_command(settings, concurrency=concurrency), settings, on_line)

    output = folder / OUTPUT_NAME
    if not output.exists():
        raise RuntimeError("流水线结束但未找到成片")
    _log(on_line, "应用分辨率与片头片尾留白…")
    work_dir = RUNTIME / "postprocess"
    work_dir.mkdir(parents=True, exist_ok=True)
    run_postprocess(settings, folder, work_dir)
    _log(on_line, f"成片完成：{output}")
    return output
