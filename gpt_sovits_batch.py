"""Load GPT-SoVITS once and synthesise all anchored narration sentences."""

import json
import gc
import os
import hashlib
import subprocess
import sys
import time
import wave
from pathlib import Path

from backend.media_tools import ffmpeg, ffprobe


def write_wav(path: Path, audio, sample_rate: int) -> None:
    import numpy as np

    data = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    data = np.squeeze(data)
    if data.ndim == 1:
        channels = 1
    else:
        if data.shape[0] <= 8 and data.shape[0] < data.shape[-1]:
            data = data.T
        channels = data.shape[1]
    if data.dtype.kind == "f":
        data = np.clip(data, -1.0, 1.0)
        data = (data * 32767.0).astype("<i2")
    else:
        data = data.astype("<i2", copy=False)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(data.tobytes())


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=True, timeout=30,
    )
    return float(result.stdout.strip())


def _shorten_prompt_text_for_audio(prompt_text: str, source_duration: float, target_duration: float) -> str:
    prompt_text = " ".join(str(prompt_text or "").split()).strip()
    if not prompt_text or source_duration <= 0 or target_duration >= source_duration:
        return prompt_text
    ratio = max(0.1, min(1.0, target_duration / source_duration))
    estimate = max(8, min(len(prompt_text), round(len(prompt_text) * ratio)))
    floor = max(6, round(estimate * 0.72))
    ceiling = min(len(prompt_text), round(estimate * 1.2))
    best = -1
    for mark in "。！？；，,.!?;":
        pos = prompt_text.rfind(mark, floor, ceiling + 1)
        if pos > best:
            best = pos + 1
    if best < floor:
        best = estimate
    return prompt_text[:best].strip()


def prepare_reference_audio(job: dict) -> tuple[str, str]:
    """Keep GPT-SoVITS reference audio inside its enforced 3-10 second window."""
    reference = Path(job["reference"])
    duration = probe_duration(reference)
    prompt_text = str(job.get("prompt_text", ""))
    if 3.0 <= duration <= 10.0:
        return str(reference), prompt_text

    output_dir = Path(job["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        f"{reference.resolve()}:{reference.stat().st_mtime_ns}:{duration:.3f}".encode("utf-8")
    ).hexdigest()[:12]
    fixed = output_dir / f"reference_gpt_sovits_{digest}.wav"
    if not fixed.exists() or fixed.stat().st_size <= 1000:
        if duration < 3.0:
            cmd = [ffmpeg(), "-y", "-i", str(reference), "-af", "apad", "-t", "3.200",
                   "-ac", "1", "-ar", "32000", str(fixed)]
        else:
            cmd = [ffmpeg(), "-y", "-i", str(reference), "-t", "9.500",
                   "-ac", "1", "-ar", "32000", str(fixed)]
        subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                       capture_output=True, check=True, timeout=120)
    fixed_duration = probe_duration(fixed)
    fixed_prompt_text = (
        prompt_text
        if duration < 3.0
        else _shorten_prompt_text_for_audio(prompt_text, duration, fixed_duration)
    )
    print(
        f"GPT-SoVITS reference audio {duration:.2f}s is outside 3-10s; "
        f"using temporary {fixed_duration:.2f}s clip: {fixed}",
        flush=True,
    )
    if fixed_prompt_text != prompt_text:
        print(
            f"GPT-SoVITS reference text shortened {len(prompt_text)} -> {len(fixed_prompt_text)} chars "
            "to match the temporary reference clip.",
            flush=True,
        )
    return str(fixed), fixed_prompt_text


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: gpt_sovits_batch.py jobs.json")
    job = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    polish = job.get("polish", False)
    max_workers = max(1, min(1, int(job.get("max_workers", 1) or 1)))
    cpu_threads = max(1, min(4, int(job.get("cpu_threads", 2) or 2)))
    cooldown_seconds = max(0.0, min(5.0, float(job.get("cooldown_seconds", 0.8) or 0.0)))
    clear_cuda_cache = bool(job.get("clear_cuda_cache", True))
    for env_name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[env_name] = str(cpu_threads)
    engine = Path(job["engine"])
    reference_audio, prompt_text = prepare_reference_audio(job)
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(engine / "GPT_SoVITS"))
    os.chdir(engine)
    print("Loading GPT-SoVITS Python modules...", flush=True)
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
    print("GPT-SoVITS Python modules loaded.", flush=True)

    config = TTS_Config(str(engine / "GPT_SoVITS" / "configs" / "tts_infer.yaml"))
    requested_device = str(job.get("device", "auto")).lower()
    seed = int(job.get("seed", 20260711))
    text_split_method = str(job.get("text_split_method", "cut0"))
    temperature = max(0.1, min(1.5, float(job.get("temperature", 0.75) or 0.75)))
    top_p = max(0.1, min(1.0, float(job.get("top_p", 0.9) or 0.9)))
    top_k = max(1, min(100, int(job.get("top_k", 10) or 10)))
    repetition_penalty = max(0.8, min(2.0, float(job.get("repetition_penalty", 1.3) or 1.3)))
    try:
        import torch
        torch.set_num_threads(cpu_threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
    except Exception:
        torch = None
    device = "cpu"
    if requested_device in ("auto", "cuda", "gpu"):
        if torch is not None and torch.cuda.is_available():
            device = "cuda"
    elif requested_device:
        device = requested_device
    config.device = device
    config.is_half = device.startswith("cuda")
    print(
        f"GPT-SoVITS device={config.device}, half={config.is_half}, "
        f"seed={seed}, split={text_split_method}, workers={max_workers}, "
        f"temperature={temperature:.2f}, top_p={top_p:.2f}, top_k={top_k}, "
        f"repetition_penalty={repetition_penalty:.2f}, "
        f"cpu_threads={cpu_threads}, cooldown={cooldown_seconds:.1f}s",
        flush=True,
    )
    tts = TTS(config)
    output = Path(job["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    items = job.get("items") or [
        {"text": text, "filename": f"tts_{index:04d}.wav"}
        for index, text in enumerate(job.get("texts", []), 1)
    ]
    start_index = max(1, int(job.get("start_index", 1) or 1))
    total_items = max(len(items), int(job.get("total_items", len(items)) or len(items)))
    for index, item in enumerate(items, 1):
        global_index = start_index + index - 1
        text = item["text"]
        target = output / item["filename"]
        if target.exists() and target.stat().st_size > 1000:
            print(f"GPT-SoVITS {global_index}/{total_items} cached", flush=True)
            continue
        generator = tts.run({
            "text": text, "text_lang": "zh", "ref_audio_path": reference_audio,
            "prompt_lang": "zh", "prompt_text": prompt_text,
            "temperature": temperature, "top_k": top_k, "top_p": top_p,
            "repetition_penalty": repetition_penalty, "speed_factor": float(job.get("speed", 1.0)),
            "text_split_method": text_split_method, "seed": seed, "media_type": "wav",
        })
        sample_rate, audio = next(generator)
        write_wav(target, audio, sample_rate)
        del audio
        gc.collect()
        if clear_cuda_cache:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        if polish:
            import subprocess
            polished_target = output / f"polished_{item['filename']}"
            subprocess.run([
                ffmpeg(), "-y", "-i", str(target),
                "-af", ("highpass=f=80,equalizer=f=3000:t=q:w=1:g=2,"
                        "compand=attacks=0.005:decays=0.05:"
                        "points=-80/-80|-30/-10|0/-3:gain=2,"
                        "loudnorm=I=-19:TP=-1.5:LRA=7"),
                "-ar", str(sample_rate), "-ac", "1", str(polished_target)
            ], capture_output=True, check=True, timeout=30)
            import shutil
            shutil.move(str(polished_target), str(target))
        print(f"GPT-SoVITS {global_index}/{total_items}", flush=True)
        if cooldown_seconds > 0 and index < len(items):
            time.sleep(cooldown_seconds)


if __name__ == "__main__":
    main()
