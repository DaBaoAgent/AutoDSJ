from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

from backend.media import detect_materials
from backend.media_tools import ffmpeg
from backend.vision_api import parse_srt

VOICE_SCHEMA = "v1-campplus-character-voice-index"
VOICE_INDEX_FILE = "_source_voice_index.json"
VOICE_GALLERY_FILE = "_voice_gallery.json"
DEFAULT_MODEL = "iic/speech_campplus_sv_zh_en_16k-common_advanced"
VOICE_ROLE_ALIASES = {"玫瑰": "黄亦玫", "小玫": "黄亦玫", "Rosie": "黄亦玫"}


def _canonical_role(value: object) -> str:
    role = str(value or "").strip()
    return VOICE_ROLE_ALIASES.get(role, role)


def _signature(video: Path, refs: list[Path]) -> str:
    values = [str(video.resolve()), str(video.stat().st_size), str(video.stat().st_mtime_ns)]
    for path in refs:
        stat = path.stat()
        values.extend((str(path.resolve()), str(stat.st_size), str(stat.st_mtime_ns)))
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def _voice_refs(folder: Path) -> dict[str, list[Path]]:
    roots = [folder / "_voices", folder.parent / "_voices"]
    result: dict[str, list[Path]] = {}
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for role_dir in root.iterdir():
            if not role_dir.is_dir():
                continue
            files = []
            for pattern in ("*.wav", "*.mp3", "*.m4a", "*.flac"):
                for path in role_dir.glob(pattern):
                    resolved = path.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(resolved)
            if files:
                result.setdefault(role_dir.name, []).extend(sorted(files))
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    nl = math.sqrt(sum(a * a for a in left))
    nr = math.sqrt(sum(b * b for b in right))
    return dot / max(1e-12, nl * nr)


def load_voice_index(folder: Path) -> dict:
    path = folder / VOICE_INDEX_FILE
    if not path.exists():
        return {"schema": VOICE_SCHEMA, "status": "missing", "segments": []}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return {"schema": VOICE_SCHEMA, "status": "corrupt", "segments": []}


def voice_event_score(index: dict, start: float, end: float, characters: list[str],
                      *, speaking: bool = False) -> dict:
    duration_by_role: dict[str, float] = {}
    speaker_seconds = 0.0
    similarities: dict[str, float] = {}
    for item in index.get("segments", []):
        overlap = max(0.0, min(end, float(item.get("end", 0))) - max(start, float(item.get("start", 0))))
        if overlap <= 0:
            continue
        speaker_seconds += overlap
        role = _canonical_role(item.get("character"))
        if role:
            duration_by_role[role] = duration_by_role.get(role, 0.0) + overlap
            similarities[role] = max(similarities.get(role, 0.0), float(item.get("similarity") or 0))
    event_duration = max(0.1, end - start)
    target = max((duration_by_role.get(_canonical_role(role), 0.0) for role in characters), default=0.0)
    identity = min(1.0, target / max(0.1, min(event_duration, speaker_seconds))) if target else 0.0
    activity = min(1.0, speaker_seconds / event_duration)
    if characters:
        total = 0.82 * identity + 0.18 * activity
    else:
        total = activity if speaking else activity * 0.35
    return {"total": total, "identity": identity, "activity": activity,
            "roles": duration_by_role, "similarities": similarities}


def build_voice_index(folder: Path, *, force: bool = False, threshold: float = 0.48) -> dict:
    """Build an optional local CAM++ subtitle-interval character index.

    SRT intervals replace full-episode VAD/clustering: drama subtitles already
    identify the speech windows, making CPU inference much faster and avoiding
    the heavyweight pyannote diarization path on a laptop.
    """
    folder = folder.resolve()
    media = detect_materials(str(folder), 1)
    video = Path(media.video_path)
    refs_by_role = _voice_refs(folder)
    refs = [path for paths in refs_by_role.values() for path in paths]
    if not refs:
        raise RuntimeError(
            "CAM++ 需要角色参考音频。请放入 <剧集根>/_voices/<角色名>/*.wav，"
            "每个角色至少一段 2～12 秒的干净对白。"
        )
    subtitle_paths = [Path(path) for path in media.subtitle_paths]
    signature = _signature(video, [*refs, *subtitle_paths])
    output = folder / VOICE_INDEX_FILE
    if output.exists() and not force:
        cached = load_voice_index(folder)
        if cached.get("signature") == signature and cached.get("status") == "complete":
            return cached
    try:
        import torch
        import torchaudio
        from speakerlab.models.campplus.DTDNN import CAMPPlus
        from speakerlab.process.processor import FBank
        from speakerlab.utils.utils import download_model_from_modelscope
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "CAM++ 声纹依赖未安装。请安装 requirements-audio.txt，并把参考音频放入 "
            "<剧集根>/_voices/<角色名>/*.wav"
        ) from exc

    wav = folder / "_source_audio_16k_mono.wav"
    if force or not wav.exists():
        subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                       check=True, timeout=max(900, int(media.duration)))
    model_dir = Path(download_model_from_modelscope(DEFAULT_MODEL, "v1.0.0"))
    model = CAMPPlus(feat_dim=80, embedding_size=192)
    model.load_state_dict(torch.load(model_dir / "campplus_cn_en_common.pt", map_location="cpu",
                                     weights_only=True))
    model.eval()
    feature_extractor = FBank(80, sample_rate=16000, mean_nor=True)

    def load_audio(path: Path) -> torch.Tensor:
        audio, sample_rate = torchaudio.load(str(path))
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            audio = torchaudio.functional.resample(audio, sample_rate, 16000)
        return audio

    source_audio = load_audio(wav)

    def embedding(path: Path | None = None, span: tuple[float, float] | None = None) -> list[float]:
        if path is not None:
            audio = load_audio(path)
        else:
            assert span is not None
            left = max(0, int(span[0] * 16000))
            right = min(source_audio.shape[-1], int(span[1] * 16000))
            audio = source_audio[:, left:right]
        # Long reference files add music/other speakers and waste CPU. CAM++
        # needs only a clean few seconds; repeat very short clips safely.
        audio = audio[:, :12 * 16000]
        if audio.shape[-1] < 8000:
            repeats = math.ceil(8000 / max(1, audio.shape[-1]))
            audio = audio.repeat(1, repeats)[:, :8000]
        features = feature_extractor(audio).unsqueeze(0)
        with torch.no_grad():
            vector = model(features).detach().squeeze(0).cpu().tolist()
        return [float(value) for value in vector]

    gallery: dict[str, list[list[float]]] = {}
    for role, paths in refs_by_role.items():
        gallery[role] = [embedding(path=path) for path in paths]
    gallery_payload = {"schema": VOICE_SCHEMA, "model": DEFAULT_MODEL,
                       "roles": {role: {"reference_count": len(values), "vectors": values}
                                 for role, values in gallery.items()}}
    (folder / VOICE_GALLERY_FILE).write_text(
        json.dumps(gallery_payload, ensure_ascii=False), "utf-8")

    segments = []
    subtitle_entries = parse_srt(subtitle_paths[0])
    for subtitle in subtitle_entries:
        start, end = float(subtitle.start), min(float(subtitle.end), float(subtitle.start) + 8.0)
        if end - start < 0.25:
            continue
        vector = embedding(span=(start, end))
        role_scores = {role: max((_cosine(vector, ref) for ref in vectors), default=0.0)
                       for role, vectors in gallery.items()}
        character, similarity = (max(role_scores.items(), key=lambda item: item[1])
                                 if role_scores else ("", 0.0))
        if similarity < threshold:
            character = ""
        segments.append({"start": round(start, 3), "end": round(end, 3),
                         "speaker": character or "unknown", "character": character,
                         "subtitle": subtitle.text,
                         "similarity": round(float(similarity), 4), "role_scores": role_scores})
    payload = {"schema": VOICE_SCHEMA, "status": "complete", "model": DEFAULT_MODEL,
               "segmentation": "subtitle-intervals",
               "signature": signature, "threshold": threshold,
               "reference_roles": sorted(gallery), "segment_count": len(segments), "segments": segments}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return payload
