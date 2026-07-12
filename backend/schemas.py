from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .qwen_voice import (
    DEFAULT_QWEN_CLONE_MODEL,
    DEFAULT_QWEN_REFERENCE_AUDIO,
    DEFAULT_QWEN_REFERENCE_TEXT_PATH,
)


class ApiSettings(BaseModel):
    dashscope_api_key: str = ""
    siliconflow_api_key: str = ""
    visual_model: str = "qwen3.7-plus"


class VoiceSettings(BaseModel):
    mode: Literal["system", "clone"] = "clone"
    provider: Literal["qwen", "cosyvoice", "gpt_sovits"] = "qwen"
    system_voice: str = "Cherry"
    clone_voice_id: str = "qwen-omni-vc-dabao3-voice-20260706200103524-5126"
    qwen_clone_model: str = DEFAULT_QWEN_CLONE_MODEL
    qwen_reference_audio: str = DEFAULT_QWEN_REFERENCE_AUDIO
    qwen_reference_text_path: str = DEFAULT_QWEN_REFERENCE_TEXT_PATH
    speech_rate: float = Field(1.0, ge=0.7, le=1.5)
    volume: int = Field(55, ge=0, le=100)
    pitch: float = Field(1.0, ge=0.5, le=2.0)
    gpt_sovits_engine_path: str = r"D:\GPT-SoVITS"
    gpt_sovits_reference_audio: str = r"D:\BaiduSyncdisk\18 艾伦全自动解说\克隆音色\yatou2.wav"
    gpt_sovits_reference_text: str = "结婚前夜，相恋7年的未婚夫，居然为了别的女人直接逃婚，新娘体面尽失当场崩溃找第三者算账，没想到却被对方一句话彻底点醒。"
    gpt_sovits_seed: int = 20260711
    gpt_sovits_text_split_method: Literal["cut0", "cut1", "cut2", "cut3", "cut4", "cut5"] = "cut0"
    gpt_sovits_temperature: float = Field(0.75, ge=0.1, le=1.5)
    gpt_sovits_top_p: float = Field(0.9, ge=0.1, le=1.0)
    gpt_sovits_top_k: int = Field(10, ge=1, le=100)
    gpt_sovits_repetition_penalty: float = Field(1.3, ge=0.8, le=2.0)
    polish_audio: bool = True


class VideoSettings(BaseModel):
    trim_head: int = Field(6, ge=1, le=300)
    trim_tail: int = Field(15, ge=1, le=300)
    padding_head: float = Field(1.0, ge=0, le=5)
    padding_tail: float = Field(3.0, ge=0, le=5)
    resolution: Literal["720P", "1080P", "2K", "4K"] = "1080P"
    video_crf: int = Field(20, ge=14, le=32)
    preset: Literal["fast", "medium", "slow"] = "fast"


class DramaSettings(BaseModel):
    source_count: int = Field(1, ge=1, le=10)
    keep_source_audio: bool = True
    source_play_volume: int = Field(80, ge=0, le=100)
    narration_source_volume: int = Field(0, ge=0, le=100)


class VisualSettings(BaseModel):
    """视觉索引识别精度控制（2026-07 精度重构）。

    高清抽帧 + 少帧/批 + 本地人脸库，让索引看清「谁·在干嘛·在哪·细节·旁边谁」。
    """

    # 抽帧分辨率：旧值 480×270 人脸仅 30-60px，认不出演员；提到 720p 人脸 150-300px。
    frame_width: int = Field(1280, ge=480, le=1920)
    frame_height: int = Field(720, ge=270, le=1080)
    jpeg_q: int = Field(3, ge=2, le=8)  # ffmpeg -q:v，越小越清（2 最清，5 旧值）
    # 每次喂给 VL 的帧数：旧值 8 稀释注意力；1-2 帧描述更深。
    batch: int = Field(2, ge=1, le=8)
    # 本地人脸库（InsightFace/ArcFace）——认「谁」的主力，纯本地零 API 费。
    use_face_gallery: bool = True
    faces_dir: str = "_faces"          # 相对剧集根目录（全集共享）
    face_gallery_file: str = "_face_gallery.json"
    face_threshold: float = Field(0.38, ge=0.20, le=0.80)  # 余弦阈值，越高越严
    face_min_size: int = Field(46, ge=20, le=400)  # 人脸框最小边(px)，太小不信
    face_det_size: int = Field(640, ge=320, le=1280)  # 检测输入尺寸


class AppSettings(BaseModel):
    material_folder: str = ""
    api: ApiSettings = ApiSettings()
    video: VideoSettings = VideoSettings()
    voice: VoiceSettings = VoiceSettings()
    drama: DramaSettings = DramaSettings()
    visual: VisualSettings = VisualSettings()

    @model_validator(mode="after")
    def normalize_audio_options(self):
        self.drama.keep_source_audio = True
        return self


class MaterialInfo(BaseModel):
    folder: str
    video_path: str
    video_paths: list[str] = []
    subtitle_paths: list[str]
    duration: float
    total_duration: float = 0.0
    selected_video_count: int = 1
    total_video_count: int = 1
    width: int
    height: int
    video_codec: str
    audio_codec: str | None = None
    warnings: list[str] = []
