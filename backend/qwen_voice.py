from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_QWEN_CLONE_MODEL = "qwen3-tts-vc-2026-01-22"
DEFAULT_QWEN_REFERENCE_AUDIO = r"D:\BaiduSyncdisk\18 艾伦全自动解说\克隆音色\yatou3.wav"
DEFAULT_QWEN_REFERENCE_TEXT_PATH = r"D:\BaiduSyncdisk\18 艾伦全自动解说\克隆音色\yatou1参考文字.txt"
DEFAULT_DASHSCOPE_HTTP_BASE = "https://dashscope.aliyuncs.com/api/v1"


def is_qwen_realtime_model(model: str) -> bool:
    return "realtime" in (model or "").lower()



def dashscope_http_base() -> str:
    value = (
        os.environ.get("DABAOAI_DASHSCOPE_BASE_HTTP_API_URL")
        or os.environ.get("DASHSCOPE_BASE_HTTP_API_URL")
        or DEFAULT_DASHSCOPE_HTTP_BASE
    )
    return value.strip().rstrip("/")



def dashscope_customization_url() -> str:
    base = dashscope_http_base()
    if base.endswith("/services/audio/tts/customization"):
        return base
    return f"{base}/services/audio/tts/customization"


def read_reference_text(value: str) -> str:
    raw = (value or "").strip().strip('"')
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_file():
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return path.read_text(encoding=encoding).strip()
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="replace").strip()
    return raw


def _load_profile(profile_file: Path) -> dict:
    if not profile_file.exists():
        return {}
    try:
        return json.loads(profile_file.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_profile(profile_file: Path, profile: dict) -> None:
    profile_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")


def _audio_mime_type(path: Path) -> str:
    suffix_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }
    return suffix_map.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "audio/wav"


def _preferred_name(path: Path) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", path.stem.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return (name or "dabao")[:16]



def qwen_reference_signature(model: str, audio_path: str, reference_text: str) -> str:
    path = Path((audio_path or "").strip().strip('"')).expanduser()
    digest = hashlib.sha1()
    digest.update((model or "").encode("utf-8", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(path.resolve() if path.exists() else path).encode("utf-8", errors="ignore"))
    digest.update(b"\0")
    if path.is_file():
        digest.update(path.read_bytes())
    digest.update(b"\0")
    digest.update((reference_text or "").encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def create_qwen_clone_voice(api_key: str, model: str, audio_path: str) -> str:
    path = Path((audio_path or "").strip().strip('"')).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"百炼克隆音色参考音频不存在：{path}")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Qwen-TTS 复刻音频不能超过 10 MB")

    data_uri = f"data:{_audio_mime_type(path)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    payload = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": model or DEFAULT_QWEN_CLONE_MODEL,
            "preferred_name": _preferred_name(path),
            "audio": {"data": data_uri},
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        dashscope_customization_url(),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"创建百炼 Qwen 克隆音色失败：{exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"创建百炼 Qwen 克隆音色失败：{exc}") from exc

    try:
        return str(data["output"]["voice"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"解析百炼 Qwen 克隆音色响应失败：{data}") from exc



def ensure_qwen_clone_voice(
    api_key: str,
    model: str,
    current_voice_id: str,
    reference_audio: str,
    reference_text_path: str,
    profile_file: Path,
) -> tuple[str, bool, dict]:
    model = (model or DEFAULT_QWEN_CLONE_MODEL).strip()
    reference_text = read_reference_text(reference_text_path)
    signature = qwen_reference_signature(model, reference_audio, reference_text)
    profile = _load_profile(profile_file)
    if (
        profile.get("voice")
        and profile.get("target_model") == model
        and profile.get("reference_signature") == signature
    ):
        return str(profile["voice"]), False, profile

    voice_id = create_qwen_clone_voice(api_key, model, reference_audio)
    profile = {
        "voice": voice_id,
        "target_model": model,
        "reference_audio": str(Path(reference_audio.strip().strip('"')).expanduser()),
        "reference_text_path": reference_text_path,
        "reference_text_sha1": hashlib.sha1(reference_text.encode("utf-8", errors="ignore")).hexdigest(),
        "reference_signature": signature,
        "created_at": int(time.time()),
    }
    _save_profile(profile_file, profile)
    return voice_id, True, profile



def synthesize_qwen_http_to_file(
    api_key: str,
    model: str,
    voice: str,
    text: str,
    output: Path,
    *,
    language_type: str = "Chinese",
) -> None:
    import dashscope

    output.parent.mkdir(parents=True, exist_ok=True)
    dashscope.api_key = api_key
    dashscope.base_http_api_url = dashscope_http_base()
    response = dashscope.MultiModalConversation.call(
        api_key=api_key,
        model=model or DEFAULT_QWEN_CLONE_MODEL,
        text=text,
        voice=voice,
        language_type=language_type,
        stream=False,
    )
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise RuntimeError(f"百炼 Qwen-TTS 合成失败：{getattr(response, 'code', '')} {getattr(response, 'message', '')}")
    audio = getattr(getattr(response, "output", None), "audio", None) or {}
    data = audio.get("data") if hasattr(audio, "get") else None
    url = audio.get("url") if hasattr(audio, "get") else None
    if data:
        output.write_bytes(base64.b64decode(data))
        return
    if not url:
        raise RuntimeError(f"百炼 Qwen-TTS 没有返回音频地址：{response}")
    try:
        with urllib.request.urlopen(url, timeout=180) as response_audio:
            output.write_bytes(response_audio.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载百炼 Qwen-TTS 音频失败：{exc}") from exc



