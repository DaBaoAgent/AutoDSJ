from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .concurrency import get_concurrency
from .config_store import runtime_env, safe_settings_dump
from .media import detect_materials
from .schemas import AppSettings, JobInfo
from .postprocess import run_postprocess
from .manual_script import SCRIPT_TABLE_FILE


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
JOBS_DIR = RUNTIME / "jobs"
LOGS_DIR = RUNTIME / "logs"


@dataclass
class JobRuntime:
    info: JobInfo
    settings: AppSettings
    logs: list[str] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    process: subprocess.Popen | None = None
    cancel_requested: bool = False


class JobManager:
    def __init__(self):
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, JobRuntime] = {}
        self.lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def _ensure_script_table(self, settings: AppSettings) -> None:
        table_path = Path(settings.material_folder) / SCRIPT_TABLE_FILE
        if not table_path.exists():
            raise RuntimeError("请先手动点击“生成脚本表”，确认脚本表生成成功后再开始成片")

    def create(self, settings: AppSettings) -> JobInfo:
        settings.drama.keep_source_audio = True
        with self.lock:
            if any(x.info.status in ("queued", "running") for x in self.jobs.values()):
                raise RuntimeError("已有智能成片任务正在运行，请等待或先取消")
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        info = JobInfo(id=job_id, status="queued", stage="等待启动", progress=0,
                       message="任务已加入队列")
        info.created_at = time.time()
        runtime = JobRuntime(info=info, settings=settings)
        with self.lock:
            self.jobs[job_id] = runtime
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "settings.json").write_text(safe_settings_dump(settings), "utf-8")
        threading.Thread(target=self._run_full, args=(runtime,), daemon=True).start()
        return info

    def get(self, job_id: str) -> JobRuntime | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.info.status not in ("queued", "running"):
            return False
        job.cancel_requested = True
        if job.process and job.process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(job.process.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    job.process.terminate()
            except OSError:
                pass
        self._update(job, status="cancelled", stage="已取消", message="用户取消了任务")
        return True

    def subscribe(self, job_id: str) -> asyncio.Queue | None:
        job = self.get(job_id)
        if not job:
            return None
        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue):
        job = self.get(job_id)
        if job and queue in job.subscribers:
            job.subscribers.remove(queue)

    def _emit(self, job: JobRuntime, payload: dict):
        if not self.loop:
            return
        for queue in list(job.subscribers):
            asyncio.run_coroutine_threadsafe(queue.put(payload), self.loop)

    def _log(self, job: JobRuntime, message: str, level: str = "info"):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        job.logs.append(line)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        (LOGS_DIR / f"{job.info.id}.log").open("a", encoding="utf-8").write(line + "\n")
        self._emit(job, {"type": "log", "level": level, "line": line})

    def _update(self, job: JobRuntime, **values):
        status = values.get("status")
        if status == "queued":
            values.setdefault("started_at", 0.0)
            values.setdefault("finished_at", 0.0)
            values.setdefault("elapsed_seconds", 0.0)
        elif status == "running" and not job.info.started_at:
            values["started_at"] = time.time()
            values["finished_at"] = 0.0
            values["elapsed_seconds"] = 0.0
        elif status in ("success", "failed", "cancelled"):
            finished_at = time.time()
            started_at = job.info.started_at or values.get("started_at") or job.info.created_at or finished_at
            values.setdefault("finished_at", finished_at)
            values.setdefault("elapsed_seconds", max(0.0, float(values["finished_at"]) - float(started_at)))
        for key, value in values.items():
            setattr(job.info, key, value)
        self._emit(job, {"type": "status", "job": job.info.model_dump()})

    def _command(self, job: JobRuntime) -> list[str]:
        media = detect_materials(job.settings.material_folder, job.settings.drama.source_count)
        target_seconds = max(30.0, media.duration - job.settings.video.trim_head - job.settings.video.trim_tail)
        ratio = max(0.05, min(1.0, target_seconds / media.duration))
        voice = job.settings.voice
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
            voice_args = ["--gpt-sovits", str(engine), "--reference", str(reference),
                          "--prompt-text", voice.gpt_sovits_reference_text,
                          "--gpt-sovits-seed", str(voice.gpt_sovits_seed),
                          "--gpt-sovits-text-split-method", voice.gpt_sovits_text_split_method,
                          "--gpt-sovits-temperature", str(voice.gpt_sovits_temperature),
                          "--gpt-sovits-top-p", str(voice.gpt_sovits_top_p),
                          "--gpt-sovits-top-k", str(voice.gpt_sovits_top_k),
                          "--gpt-sovits-repetition-penalty", str(voice.gpt_sovits_repetition_penalty)]
        elif voice.mode == "system":
            backend = "qwen-clone"
            voice_args = ["--qwen-voice", voice.system_voice,
                          "--qwen-model", "qwen3-tts-flash-realtime",
                          "--qwen-volume", str(voice.volume),
                          "--qwen-pitch", str(voice.pitch)]
        else:
            backend, voice_args = "cosyvoice", []
        speech_rate = voice.speech_rate
        source_volume = max(0.0, min(1.0, float(getattr(job.settings.drama, "source_play_volume", 100)) / 100.0))
        narration_source_volume = max(0.0, min(1.0, float(getattr(job.settings.drama, "narration_source_volume", 0)) / 100.0))
        cmd = [sys.executable, "-u", str(ROOT / "anchored_pipeline.py"),
               job.settings.material_folder, "--ratio", f"{ratio:.8f}",
               "--target-seconds", f"{target_seconds:.3f}",
               "--tts-backend", backend, "--speech-rate", str(speech_rate),
               "--trim-head", str(job.settings.video.trim_head),
               "--trim-tail", str(job.settings.video.trim_tail),
               *voice_args]
        if job.settings.drama.keep_source_audio:
            cmd += ["--include-source-audio", "--source-volume", f"{source_volume:.4f}",
                    "--narration-source-volume", f"{narration_source_volume:.4f}"]
        if voice.mode == "clone" and voice.provider == "gpt_sovits" and voice.polish_audio:
            cmd.append("--polish")
        cmd.extend(["--concurrency", str(get_concurrency())])
        return cmd

    def _run_process(self, job: JobRuntime, cmd: list[str]) -> None:
        env = runtime_env(job.settings)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = ((subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
                         if os.name == "nt" else 0)
        job.process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                       errors="replace", env=env, creationflags=creationflags)
        assert job.process.stdout
        recent_lines: list[str] = []
        for line in job.process.stdout:
            if job.cancel_requested:
                break
            line = line.rstrip()
            if line:
                recent_lines.append(line)
                recent_lines = recent_lines[-12:]
                self._log(job, line)
                progress = self._parse_progress(line)
                if progress:
                    self._update(job, **progress)
        code = job.process.wait()
        if not job.cancel_requested and code != 0:
            detail = "\n".join(recent_lines[-8:])
            raise RuntimeError(f"成片内核退出，代码 {code}" + (f"\n{detail}" if detail else ""))

    def _run_full(self, job: JobRuntime):
        try:
            self._update(job, status="running", stage="智能配音与剪辑", progress=8,
                         message="正在生成配音并剪辑成片")
            media = detect_materials(job.settings.material_folder, job.settings.drama.source_count)
            self._log(job, f"检测到主原片：{Path(media.video_path).name}")
            self._log(job, "成片规则：严格按用户文案顺序；人物动作视觉匹配；全片镜头不复用")
            self._ensure_script_table(job.settings)
            self._log(job, "已使用手动生成的脚本表，不再自动生成脚本表")
            self._log(
                job,
                "音频规则：播放原片时原片音量 "
                f"{job.settings.drama.source_play_volume}%；解说时原片音量 "
                f"{job.settings.drama.narration_source_volume}%，配音 100%",
            )
            self._run_process(job, self._command(job))
            if job.cancel_requested:
                return
            output = Path(job.settings.material_folder) / "★ 成片.mp4"
            if not output.exists():
                raise RuntimeError("流水线结束但未找到成片")
            self._update(job, stage="输出设置", progress=92, message="正在应用分辨率和片头片尾留白")
            run_postprocess(job.settings, Path(job.settings.material_folder), JOBS_DIR / job.info.id)
            self._update(job, status="success", stage="成片完成", progress=100,
                         message="智能成片已完成", output_path=str(output))
            self._log(job, f"成片完成：{output}", "success")
        except Exception as exc:
            if not job.cancel_requested:
                self._log(job, f"任务失败：{exc}", "error")
                self._update(job, status="failed", stage="处理失败", message=str(exc), error=str(exc))

    @staticmethod
    def _parse_progress(line: str) -> dict | None:
        match = re.search(r"Qwen TTS (\d+)/(\d+)", line)
        if match:
            current, total = map(int, match.groups())
            return {"stage": "克隆音色配音", "progress": 8 + int(current / total * 30),
                    "message": f"配音 {current}/{total}"}
        match = re.search(r"GPT-SoVITS (\d+)/(\d+)", line)
        if match:
            current, total = map(int, match.groups())
            return {"stage": "本地 GPT-SoVITS 克隆配音", "progress": 8 + int(current / total * 30),
                    "message": f"GPT-SoVITS 配音 {current}/{total}"}
        match = re.search(r"视频 (\d+)/(\d+)", line)
        if match:
            current, total = map(int, match.groups())
            return {"stage": "精准画面剪辑", "progress": 42 + int(current / total * 48),
                    "message": f"画面渲染 {current}/{total}"}
        if "文案" in line and "句" in line:
            return {"stage": "文案完成", "progress": 8, "message": line}
        if line.startswith("完成："):
            return {"stage": "最终质检", "progress": 96, "message": "正在检查输出"}
        return None


manager = JobManager()
