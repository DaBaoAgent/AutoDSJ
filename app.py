from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config_store import load_settings, save_settings
from backend.config_store import read_env, read_secrets, MASK
from backend.drama_source_index import build_source_index
from backend.jobs import manager
from backend.manual_script import (
    SCRIPT_EXTENSIONS,
    SCRIPT_TABLE_FILE,
    find_manual_script_file,
    generate_manual_script_table,
    parse_manual_script,
    read_script_document,
)
from backend.media import _looks_like_source, _natural_key, _probe_video, detect_materials
from backend.media_tools import gpt_sovits_python
from backend.qwen_voice import (
    DEFAULT_QWEN_CLONE_MODEL,
    DEFAULT_QWEN_REFERENCE_AUDIO,
    DEFAULT_QWEN_REFERENCE_TEXT_PATH,
    ensure_bailian_clone_voice,
    is_qwen_realtime_model,
    read_reference_text,
    synthesize_bailian_http_to_file,
)
from backend.schemas import AppSettings, JobInfo, MaterialInfo
from backend.vision_api import parse_srt


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
FRONTEND = ROOT / "frontend" / "dist"
GPT_SOVITS_TEST_LOCK = threading.Lock()
QWEN_CLONE_TEST_LOCK = threading.Lock()
NET_SAMPLE = {"time": time.time(), "sent": 0, "recv": 0}
DERIVED_FILES = {
    "_drama_script_table.json", "_narration_manifest.json", "_anchored_audio_concat.txt",
    "_anchored_muxed.mp4", "_anchored_silent.mp4", "配音.wav", "配音稿.txt", "配音稿_朗读版.txt",
    "★ 成片.mp4", "★ 字幕.srt", "★ 匹配报告.json",
}


def _clear_derived_materials(folder: Path, clear_visual: bool) -> None:
    names = set(DERIVED_FILES)
    if clear_visual:
        names.update({"_source_visual_index.json", "_source_subtitle_index.json", "_source_clip_candidates.json"})
    for name in names:
        (folder / name).unlink(missing_ok=True)
    for path in folder.glob("_anchored_*"):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink(missing_ok=True)
    for path in folder.glob("_gpt_sovits_jobs*.json"):
        path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    manager.bind_loop(asyncio.get_running_loop())
    yield


APP_NAME = "DaobaoAI-DY 大宝影视全自动智能剪辑工厂"


app = FastAPI(title=APP_NAME, version="2.0.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True, "name": APP_NAME, "version": "2.0.0"}


@app.get("/api/system-stats")
def system_stats():
    try:
        import psutil
    except ImportError as exc:
        raise HTTPException(500, "缺少 psutil，请安装后查看本机性能监测") from exc

    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    net = psutil.net_io_counters()
    now = time.time()
    elapsed = max(0.001, now - float(NET_SAMPLE.get("time", now)))
    upload = max(0, net.bytes_sent - int(NET_SAMPLE.get("sent", net.bytes_sent))) / elapsed
    download = max(0, net.bytes_recv - int(NET_SAMPLE.get("recv", net.bytes_recv))) / elapsed
    NET_SAMPLE.update({"time": now, "sent": net.bytes_sent, "recv": net.bytes_recv})

    cpu_temp = None
    try:
        temps = psutil.sensors_temperatures()
        readings = [item.current for group in temps.values() for item in group
                    if getattr(item, "current", None) is not None]
        if readings:
            cpu_temp = round(max(readings), 1)
    except Exception:
        cpu_temp = None

    return {
        "cpu_percent": round(cpu_percent, 1),
        "cpu_temperature": cpu_temp,
        "memory_percent": round(memory.percent, 1),
        "memory_used_gb": round(memory.used / 1024 ** 3, 2),
        "memory_total_gb": round(memory.total / 1024 ** 3, 2),
        "net_upload_bps": round(upload),
        "net_download_bps": round(download),
    }


@app.get("/api/config", response_model=AppSettings)
def get_config():
    return load_settings(mask_keys=True)


@app.put("/api/config", response_model=AppSettings)
def put_config(settings: AppSettings):
    return save_settings(settings)


@app.post("/api/materials/detect", response_model=MaterialInfo)
def detect(payload: dict):
    try:
        return detect_materials(
            str(payload.get("folder", "")),
            int(payload.get("max_videos", payload.get("source_count", 10)) or 10),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _upload_folder(folder_value: str = "") -> Path:
    folder_value = folder_value.strip().strip('"')
    if folder_value:
        folder = Path(folder_value).expanduser()
    else:
        settings = load_settings(mask_keys=False)
        folder = Path(settings.material_folder).expanduser() if settings.material_folder else RUNTIME / "manual_uploads" / "current"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _format_seconds(value: float) -> str:
    total_seconds = int(round(max(0.0, float(value))))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _inspect_uploaded_material(kind: str, path: Path) -> dict:
    if kind == "video":
        data = _probe_video(path)
        vstream = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), {})
        astream = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), {})
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        width = int(vstream.get("width") or 0)
        height = int(vstream.get("height") or 0)
        return {
            "ok": True,
            "kind": kind,
            "title": "原片检测通过",
            "summary": f"{path.name} · {width}x{height} · {_format_seconds(duration)} · {vstream.get('codec_name', 'unknown').upper()}",
            "details": [
                f"视频时长 {_format_seconds(duration)}",
                f"音频编码 {(astream.get('codec_name') or 'none').upper()}",
            ],
        }
    if kind == "subtitle":
        entries = parse_srt(path)
        first = entries[0]
        last = entries[-1]
        preview = " / ".join(item.text[:36] for item in entries[:3])
        return {
            "ok": True,
            "kind": kind,
            "title": "字幕检测通过",
            "summary": f"{path.name} · {path.suffix.upper().lstrip('.')} · {len(entries)} 条 · {_format_seconds(first.start)}-{_format_seconds(last.end)}",
            "details": [preview] if preview else [],
        }
    if kind == "script":
        blocks = parse_manual_script(read_script_document(path))
        source_blocks = sum(1 for item in blocks if item.row_type == "source_clip")
        narration_blocks = sum(1 for item in blocks if item.row_type == "narration")
        narration_text = "\n".join(item.text for item in blocks if item.row_type == "narration")
        preview = narration_text.replace("\n", " ")[:90]
        return {
            "ok": True,
            "kind": kind,
            "title": "文案检测通过",
            "summary": f"{path.name} · 原片 {source_blocks} 段 · 解说 {narration_blocks} 段 · {len(narration_text)} 字",
            "details": [preview] if preview else [],
        }
    raise ValueError("未知上传类型")


def _first_material_file(folder: Path, extensions: tuple[str, ...], preferred_stems: tuple[str, ...]) -> Path | None:
    for stem in preferred_stems:
        for ext in extensions:
            path = folder / f"{stem}{ext}"
            if path.is_file():
                return path
    files = sorted([path for ext in extensions for path in folder.glob(f"*{ext}")], key=_natural_key)
    source_files = [path for path in files if _looks_like_source(path)] or files
    return source_files[0] if source_files else None


def _material_status(folder_value: str) -> dict:
    folder = Path(str(folder_value or "").strip().strip('"')).expanduser()
    if not folder.is_dir():
        raise ValueError(f"素材文件夹不存在：{folder}")

    settings = load_settings(mask_keys=False)
    checks: dict[str, dict] = {}
    errors: dict[str, str] = {}
    material: MaterialInfo | None = None
    try:
        material = detect_materials(str(folder), settings.drama.source_count)
    except Exception as exc:
        errors["material"] = str(exc)

    candidates: dict[str, Path | None] = {
        "video": Path(material.video_path) if material else _first_material_file(folder, (".mp4", ".mkv", ".mov"), ("原片",)),
        "subtitle": Path(material.subtitle_paths[0]) if material and material.subtitle_paths else _first_material_file(folder, (".srt", ".ass"), ("字幕",)),
        "script": find_manual_script_file(folder),
    }
    for kind, path in candidates.items():
        if not path:
            continue
        try:
            checks[kind] = _inspect_uploaded_material(kind, path)
        except Exception as exc:
            errors[kind] = str(exc)
    return {
        "ok": True,
        "folder": str(folder.resolve()),
        "checks": checks,
        "errors": errors,
        "material": material.model_dump() if material else None,
    }


def _visual_index_status(folder_value: str) -> dict:
    folder = Path(str(folder_value or "").strip().strip('"')).expanduser()
    if not folder.is_dir():
        return {
            "ok": False,
            "ready": False,
            "exists": False,
            "message": f"素材文件夹不存在：{folder}",
            "frame_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "progress": 0,
        }
    path = folder / "_source_visual_index.json"
    if not path.exists():
        return {
            "ok": True,
            "ready": False,
            "exists": False,
            "message": "等待视觉识别",
            "frame_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "progress": 0,
        }
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "ok": False,
            "ready": False,
            "exists": True,
            "message": "视觉索引文件损坏，请重新识别",
            "frame_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "progress": 0,
        }
    frame_count = int(payload.get("frame_count") or len(payload.get("frames") or []) or 0)
    success_count = int(payload.get("success_count") or 0)
    failed_count = int(payload.get("failed_count") or 0)
    status_value = str(payload.get("status") or "")
    if status_value in {"extracting_frames", "recognizing_frames"}:
        progress = int(payload.get("progress") or 0)
    else:
        progress = int(round(min(100, max(0, (success_count + failed_count) * 100 / max(1, frame_count)))))
    ready = frame_count > 0 and success_count > 0 and status_value not in {"extracting_frames", "recognizing_frames"}
    message = str(payload.get("message") or "").strip() or (
        f"视觉识别完成：成功 {success_count}/{frame_count} 帧"
        if ready else
        f"视觉识别进行中：成功 {success_count}/{frame_count} 帧"
        if frame_count else
        "视觉索引没有可用识别帧，请重新识别"
    )
    return {
        "ok": True,
        "ready": ready,
        "exists": True,
        "message": message,
        "status": status_value,
        "model": payload.get("model", ""),
        "frame_interval": payload.get("frame_interval", 0),
        "frame_count": frame_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "progress": progress,
        "errors": payload.get("errors", [])[-3:],
    }


@app.post("/api/materials/status")
def material_status(payload: dict):
    try:
        folder = str(payload.get("folder", "")).strip()
        if not folder:
            settings = load_settings(mask_keys=False)
            folder = settings.material_folder
        if not folder:
            return {"ok": True, "folder": "", "checks": {}, "errors": {}, "material": None}
        return _material_status(folder)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/source-index/status")
def source_index_status(payload: dict):
    try:
        folder = str(payload.get("folder", "")).strip()
        if not folder:
            folder = load_settings(mask_keys=False).material_folder
        return _visual_index_status(folder)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/materials/upload")
async def upload_material(
    kind: str = Form(...),
    folder: str = Form(""),
    file: UploadFile = File(...),
):
    try:
        folder_path = _upload_folder(folder)
        suffix = Path(file.filename or "").suffix.lower()
        allowed = {
            "video": {".mp4", ".mkv"},
            "subtitle": {".srt", ".ass"},
            "script": set(SCRIPT_EXTENSIONS),
        }
        if kind not in allowed:
            raise ValueError("未知上传类型")
        if suffix not in allowed[kind]:
            raise ValueError(
                "文件格式不支持："
                + ("原片只支持 mp4/mkv" if kind == "video" else "字幕只支持 srt/ass" if kind == "subtitle" else "文案支持 txt/md/rtf/docx")
            )
        safe_name = Path(file.filename or f"{kind}{suffix}").name
        if kind == "video":
            for old in [folder_path / "原片.mp4", folder_path / "原片.mkv"]:
                if old.suffix.lower() != suffix:
                    old.unlink(missing_ok=True)
            target = folder_path / f"原片{suffix}"
        elif kind == "subtitle":
            for old in [folder_path / "字幕.srt", folder_path / "字幕.ass"]:
                if old.suffix.lower() != suffix:
                    old.unlink(missing_ok=True)
            target = folder_path / f"字幕{suffix}"
        else:
            for old in folder_path.glob("解说文案.*"):
                if old.suffix.lower() != suffix:
                    old.unlink(missing_ok=True)
            target = folder_path / f"解说文案{suffix}"
        # Preserve the original extension but keep deterministic names so new users do
        # not have to manage folders manually.
        data = await file.read()
        if not data:
            raise ValueError("上传文件为空")
        target.write_bytes(data)
        check = _inspect_uploaded_material(kind, target)
        _clear_derived_materials(folder_path, clear_visual=kind in {"video", "subtitle"})
        settings = load_settings(mask_keys=False)
        settings.material_folder = str(folder_path.resolve())
        save_settings(settings)
        return {
            "ok": True,
            "kind": kind,
            "filename": safe_name,
            "saved_path": str(target.resolve()),
            "folder": str(folder_path.resolve()),
            "check": check,
        }
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/materials/detect-manual")
def detect_manual(payload: dict):
    try:
        if payload.get("settings"):
            settings = AppSettings.model_validate(payload["settings"])
        else:
            settings = load_settings(mask_keys=False)
            folder = str(payload.get("folder", "")).strip()
            if folder:
                settings.material_folder = folder
        if not settings.material_folder:
            raise ValueError("material_folder is required")
        material = detect_materials(settings.material_folder, settings.drama.source_count)
        visual_status = _visual_index_status(settings.material_folder)
        if not visual_status.get("ready"):
            raise ValueError("请先完成“视觉帧识别”，确认有可用识别帧后再生成脚本表")
        table = generate_manual_script_table(settings, str(payload.get("script_path", "")).strip() or None)
        save_settings(settings)
        return {
            "ok": True,
            "material": material.model_dump(),
            "script_table": table,
            "narration_text": table.get("narration_text", ""),
            "validation": table.get("validation", {}),
            "visual_reused": True,
            "visual_status": visual_status,
        }
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/source-index/run")
def run_source_index(payload: dict):
    try:
        if payload.get("settings"):
            settings = AppSettings.model_validate(payload["settings"])
        else:
            settings = load_settings(mask_keys=False)
            folder = str(payload.get("folder", "")).strip()
            if folder:
                settings.material_folder = folder
        if not settings.material_folder:
            raise ValueError("material_folder is required")
        save_settings(settings)
        supplied_key = str(payload.get("dashscope_api_key", "") or payload.get("siliconflow_api_key", "")).strip()
        settings_key = settings.api.dashscope_api_key
        if settings_key == MASK:
            settings_key = ""
        api_key = (
            supplied_key
            or settings_key
            or read_secrets().get("dashscope_api_key", "")
            or read_env().get("DASHSCOPE_API_KEY", "")
            or read_secrets().get("siliconflow_api_key", "")
            or read_env().get("SILICONFLOW_API_KEY", "")
        )
        return build_source_index(
            settings,
            siliconflow_api_key=api_key,
            visual_model=str(payload.get("visual_model", "")).strip() or settings.api.visual_model,
            frame_interval=float(payload.get("frame_interval", 6.0) or 6.0),
            visual_batch_size=int(payload.get("visual_batch_size", 8) or 8),
            visual_delay_sec=float(payload.get("visual_delay_sec", 1.0) or 1.0),
            visual_workers=int(payload.get("visual_workers", 1) or 1),
            force_visual=bool(payload.get("force_visual", False)),
            enable_visual_model=bool(payload.get("enable_visual_model", True)),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/voices")
def voices():
    profile_file = ROOT / "voice_dabao_bailian.json"
    clone = ""
    if profile_file.exists():
        import json
        clone = json.loads(profile_file.read_text("utf-8")).get("voice", "")
    return {
        "system": [
            {"id": "Cherry", "name": "芊悦 · 阳光自然女声"},
            {"id": "Serena", "name": "苏瑶 · 温柔女声"},
            {"id": "Chelsie", "name": "千雪 · 稳重女声"},
            {"id": "Ethan", "name": "晨煦 · 自然男声"},
        ],
        "clones": ([{"id": clone, "name": "电视剧解说克隆音色"}] if clone else []),
    }


@app.get("/api/voices/list")
def list_bailian_voices():
    key = read_secrets().get("dashscope_api_key") or read_env().get("DASHSCOPE_API_KEY", "")
    if not key:
        raise HTTPException(400, "百炼 API Key 未配置")
    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/api/v1/tts/voices",
        headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        voices = []
        for v in data.get("voices", []):
            voices.append({
                "id": v.get("voice_id", ""),
                "name": v.get("voice_name", ""),
                "gender": v.get("gender", ""),
                "description": v.get("description", ""),
            })
        return {"voices": voices}
    except Exception as exc:
        raise HTTPException(502, f"获取百炼音色列表失败: {exc}")


def _bailian_profile() -> dict:
    profile_file = ROOT / "voice_dabao_bailian.json"
    if not profile_file.exists():
        return {}
    try:
        return json.loads(profile_file.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@app.post("/api/voices/test-qwen-clone")
def test_qwen_clone(payload: dict):
    key = read_secrets().get("dashscope_api_key") or read_env().get("DASHSCOPE_API_KEY", "")
    if not key:
        raise HTTPException(400, "百炼 API Key 未配置")

    profile = _bailian_profile()
    voice_id = str(payload.get("voice_id", "") or profile.get("voice", "")).strip()
    model = str(payload.get("model", "") or profile.get("target_model", DEFAULT_QWEN_CLONE_MODEL)).strip()
    reference_audio = str(payload.get("reference_audio", "") or profile.get("reference_audio", "") or DEFAULT_QWEN_REFERENCE_AUDIO).strip()
    reference_text_path = str(
        payload.get("reference_text_path", "")
        or profile.get("reference_text_path", "")
        or DEFAULT_QWEN_REFERENCE_TEXT_PATH
    ).strip()
    speed = float(payload.get("speed", 1.0))
    volume = max(0, min(100, int(payload.get("volume", 55))))
    pitch = max(0.5, min(2.0, float(payload.get("pitch", 1.0))))
    if is_qwen_realtime_model(model) and not voice_id:
        raise HTTPException(400, "未配置百炼克隆音色 ID")

    test_dir = RUNTIME / "qwen_clone_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_text = "她以为自己只是被误会，可对方冲到单位当众质问，这场关系里的真相终于藏不住了。"
    if not is_qwen_realtime_model(model):
        try:
            voice_id, created, _ = ensure_bailian_clone_voice(
                key,
                model,
                voice_id,
                reference_audio,
                reference_text_path,
                ROOT / "voice_dabao_bailian.json",
            )
            settings = load_settings(mask_keys=False)
            if (
                settings.voice.clone_voice_id != voice_id
                or settings.voice.qwen_clone_model != model
                or settings.voice.qwen_reference_audio != reference_audio
                or settings.voice.qwen_reference_text_path != reference_text_path
            ):
                settings.voice.clone_voice_id = voice_id
                settings.voice.qwen_clone_model = model
                settings.voice.qwen_reference_audio = reference_audio
                settings.voice.qwen_reference_text_path = reference_text_path
                save_settings(settings)
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

        reference_text = read_reference_text(reference_text_path)
        fingerprint = hashlib.sha1(json.dumps({
            "voice_id": voice_id,
            "model": model,
            "text": test_text,
            "reference_audio": reference_audio,
            "reference_text": reference_text,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        output_audio = test_dir / f"test_{fingerprint}.wav"
        if not output_audio.exists() or output_audio.stat().st_size <= 1000:
            try:
                synthesize_bailian_http_to_file(key, model, voice_id, test_text, output_audio)
            except Exception as exc:
                raise HTTPException(500, str(exc)) from exc
        return FileResponse(output_audio, media_type="audio/wav", filename="qwen_clone_test.wav")

    fingerprint = hashlib.sha1(json.dumps({
        "voice_id": voice_id,
        "model": model,
        "text": test_text,
        "speed": speed,
        "volume": volume,
        "pitch": pitch,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    output_audio = test_dir / f"test_{fingerprint}.wav"
    if output_audio.exists() and output_audio.stat().st_size > 1000:
        return FileResponse(output_audio, media_type="audio/wav", filename="qwen_clone_test.wav")

    if not QWEN_CLONE_TEST_LOCK.acquire(blocking=False):
        raise HTTPException(409, "百炼克隆音色正在生成另一段测试配音，请稍候")
    try:
        try:
            import dashscope
            from dashscope.audio.qwen_tts_realtime import (
                AudioFormat,
                QwenTtsRealtime,
                QwenTtsRealtimeCallback,
            )
        except ImportError as exc:
            raise HTTPException(500, "缺少 dashscope，请先安装依赖") from exc

        class Callback(QwenTtsRealtimeCallback):
            def __init__(self):
                self.done = threading.Event()
                self.audio = bytearray()
                self.error = None

            def on_event(self, response):
                kind = response.get("type")
                if kind == "response.audio.delta":
                    self.audio.extend(base64.b64decode(response["delta"]))
                elif kind == "response.done":
                    self.done.set()
                elif kind == "error":
                    self.error = response
                    self.done.set()

            def on_close(self, code, message):
                if code not in (None, 1000):
                    self.error = {"code": code, "message": message}
                self.done.set()

        dashscope.api_key = key
        cb = Callback()
        tts = None
        try:
            tts = QwenTtsRealtime(model=model, callback=cb)
            tts.connect()
            tts.update_session(
                voice=voice_id,
                response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                sample_rate=24000,
                speech_rate=speed,
                pitch_rate=pitch,
                volume=volume,
                language_type="Chinese",
                mode="commit",
            )
            tts.append_text(test_text)
            tts.commit()
            if not cb.done.wait(180):
                raise HTTPException(504, "百炼克隆音色合成超时")
            if cb.error:
                raise HTTPException(500, f"百炼克隆音色合成失败：{cb.error}")
            if not cb.audio:
                raise HTTPException(500, "百炼克隆音色没有返回音频")
            with wave.open(str(output_audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(cb.audio)
        finally:
            if tts is not None:
                try:
                    tts.finish()
                except Exception:
                    pass
    finally:
        QWEN_CLONE_TEST_LOCK.release()

    return FileResponse(output_audio, media_type="audio/wav", filename="qwen_clone_test.wav")


@app.post("/api/voices/test-gpt-sovits")
def test_gpt_sovits(payload: dict):
    engine_path = Path(str(payload.get("engine_path", "")))
    reference_audio = Path(str(payload.get("reference_audio", "")))
    reference_text = str(payload.get("reference_text", ""))
    speed = float(payload.get("speed", 1.0))
    polish = bool(payload.get("polish", False))
    seed = int(payload.get("seed", 20260711))
    text_split_method = str(payload.get("text_split_method", "cut0"))
    temperature = float(payload.get("temperature", 0.75))
    top_p = float(payload.get("top_p", 0.9))
    top_k = int(payload.get("top_k", 10))
    repetition_penalty = float(payload.get("repetition_penalty", 1.3))

    if not engine_path.exists():
        raise HTTPException(400, f"引擎路径不存在: {engine_path}")
    if not reference_audio.exists():
        raise HTTPException(400, f"参考音频不存在: {reference_audio}")

    python_exe = gpt_sovits_python(engine_path)

    test_dir = RUNTIME / "gpt_sovits_test"
    test_dir.mkdir(parents=True, exist_ok=True)

    test_text = "她以为自己只是被误会，可对方冲到单位当众质问，这场关系里的真相终于藏不住了。"

    fingerprint = hashlib.sha1(json.dumps({
        "reference": str(reference_audio.resolve()),
        "reference_mtime": reference_audio.stat().st_mtime_ns,
        "reference_text": reference_text,
        "text": test_text,
        "speed": speed,
        "polish": polish,
        "seed": seed,
        "text_split_method": text_split_method,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "repetition_penalty": repetition_penalty,
        "device": "auto",
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    output_name = f"test_{fingerprint}.wav"
    output_audio = test_dir / output_name
    if output_audio.exists() and output_audio.stat().st_size > 1000:
        return FileResponse(output_audio, media_type="audio/wav",
                            filename="gpt_sovits_test.wav")

    job_data = {
        "engine": str(engine_path),
        "reference": str(reference_audio),
        "prompt_text": reference_text,
        "speed": speed,
        "polish": polish,
        "device": "auto",
        "seed": seed,
        "text_split_method": text_split_method,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "repetition_penalty": repetition_penalty,
        "output_dir": str(test_dir),
        "items": [{"text": test_text, "filename": output_name}],
    }

    job_file = test_dir / "jobs.json"
    job_file.write_text(json.dumps(job_data, ensure_ascii=False, indent=2), "utf-8")

    batch_script = ROOT / "gpt_sovits_batch.py"

    if not GPT_SOVITS_TEST_LOCK.acquire(blocking=False):
        raise HTTPException(409, "GPT-SoVITS 正在生成另一段测试配音，请稍候")
    log_file = test_dir / f"test_{fingerprint}.log"
    process = None
    try:
        with log_file.open("w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                [str(python_exe), str(batch_script), str(job_file)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                # CPU cold starts load four large models. Slow machines may need more than
                # five minutes, so use a bounded 15-minute ceiling and cache the result.
                return_code = process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    process.kill()
                raise HTTPException(504, "GPT-SoVITS 合成超时（超过15分钟），请检查测试日志")
        if return_code != 0:
            detail = log_file.read_text("utf-8", errors="replace")[-8000:]
            raise HTTPException(500, f"GPT-SoVITS 合成失败：\n{detail}")
    finally:
        GPT_SOVITS_TEST_LOCK.release()

    if not output_audio.exists() or output_audio.stat().st_size <= 1000:
        detail = log_file.read_text("utf-8", errors="replace")[-4000:]
        raise HTTPException(500, f"GPT-SoVITS 未生成有效音频文件：\n{detail}")

    return FileResponse(output_audio, media_type="audio/wav",
                        filename="gpt_sovits_test.wav")


@app.post("/api/api-test")
def test_api(payload: dict):
    provider = str(payload.get("provider", ""))
    supplied = str(payload.get("key", ""))
    field_map = {"dashscope": "dashscope_api_key", "siliconflow": "siliconflow_api_key"}
    env_map = {"dashscope": "DASHSCOPE_API_KEY", "siliconflow": "SILICONFLOW_API_KEY"}
    if provider not in field_map:
        raise HTTPException(400, "未知 API 服务")
    if supplied in ("", MASK):
        supplied = read_secrets().get(field_map[provider]) or read_env().get(env_map[provider], "")
    if not supplied:
        raise HTTPException(400, "API Key 未配置")
    urls = {
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "siliconflow": "https://api.siliconflow.cn/v1/models",
    }
    try:
        request = urllib.request.Request(urls[provider], headers={"Authorization": f"Bearer {supplied}"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return {"ok": response.status < 400, "provider": provider}
    except Exception as exc:
        raise HTTPException(400, f"连接失败：{exc}") from exc


@app.post("/api/jobs", response_model=JobInfo)
def create_job(payload: dict):
    try:
        settings = AppSettings.model_validate(payload.get("settings"))
        detect_materials(settings.material_folder, settings.drama.source_count)
        visual_status = _visual_index_status(settings.material_folder)
        if not visual_status.get("ready"):
            raise ValueError("请先完成“视觉帧识别”，确认有可用识别帧后再开始成片")
        table_path = Path(settings.material_folder) / SCRIPT_TABLE_FILE
        if not table_path.exists():
            raise ValueError("请先手动点击“生成脚本表”，确认脚本表生成成功后再开始成片")
        save_settings(settings)
        return manager.create(settings)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job.info


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not manager.cancel(job_id):
        raise HTTPException(409, "任务无法取消")
    return {"ok": True}


@app.websocket("/ws/jobs/{job_id}")
async def job_socket(websocket: WebSocket, job_id: str):
    await websocket.accept()
    job = manager.get(job_id)
    if not job:
        await websocket.send_json({"type": "error", "message": "任务不存在"})
        await websocket.close()
        return
    for line in job.logs[-200:]:
        await websocket.send_json({"type": "log", "level": "info", "line": line})
    await websocket.send_json({"type": "status", "job": job.info.model_dump()})
    queue = manager.subscribe(job_id)
    try:
        while queue:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        if queue:
            manager.unsubscribe(job_id, queue)


@app.get("/icon.png")
def serve_icon():
    icon = ROOT / "frontend" / "public" / "icon.png"
    if not icon.exists():
        icon = ROOT / "frontend" / "dist" / "icon.png"
    if icon.exists():
        return FileResponse(icon)
    raise HTTPException(404, "图标文件不存在")


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = FRONTEND / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")
else:
    @app.get("/")
    def frontend_missing():
        return {"message": "前端尚未构建，请运行 npm --prefix frontend run build"}
