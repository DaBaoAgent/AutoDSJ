export type Settings = {
  material_folder: string
  ui: { language: 'zh' | 'en' }
  api: Record<string, string>
  video: {
    trim_head: number; trim_tail: number; padding_head: number; padding_tail: number
    target_minutes: number; resolution: string; video_crf: number; preset: string
  }
  voice: {
    mode: string; provider: string; system_voice: string; clone_voice_id: string
    qwen_clone_model: string; qwen_reference_audio: string; qwen_reference_text_path: string
    speech_rate: number; volume: number; pitch: number
    gpt_sovits_engine_path: string; gpt_sovits_reference_audio: string
    gpt_sovits_reference_text: string; gpt_sovits_seed: number
    gpt_sovits_text_split_method: string; gpt_sovits_temperature: number
    gpt_sovits_top_p: number; gpt_sovits_top_k: number
    gpt_sovits_repetition_penalty: number; polish_audio: boolean
  }
  drama: {
    source_count: number; keep_source_audio: boolean
    source_play_volume: number; narration_source_volume: number
  }
}

export type MaterialCheck = {
  ok: boolean
  kind: 'video' | 'subtitle' | 'script'
  title: string
  summary: string
  details: string[]
}

export type MaterialChecks = Partial<Record<'video' | 'subtitle' | 'script', MaterialCheck>>

export type VisualStatus = {
  ok: boolean
  ready: boolean
  exists: boolean
  status?: string
  message: string
  model?: string
  frame_interval?: number
  frame_count: number
  success_count: number
  failed_count: number
  progress: number
  errors?: Array<Record<string, unknown>>
}

export type Material = {
  video_path: string; video_paths: string[]; subtitle_paths: string[]
  duration: number; total_duration: number; selected_video_count: number; total_video_count: number
  width: number; height: number
  video_codec: string; audio_codec?: string; warnings: string[]
}

export type ScriptTableRow = {
  row_id: number
  row_type: 'source_clip' | 'narration'
  insert_role: string
  insert_role_label: string
  text: string
  matched_clip_id: number
  source_index: number
  source_file: string
  source_start: number
  source_end: number
  source_time_text: string
  source_audio_mode: string
  visual_intent: string
  match_score: number
  match_reason: string
  locked: boolean
  alternatives: Array<{
    clip_id: number
    source_index: number
    source_file: string
    source_start: number
    source_end: number
    source_time_text: string
    source_audio_mode: string
    visual_intent: string
    match_score: number
    match_reason: string
  }>
}

export type ScriptTable = {
  ok: boolean
  folder: string
  style: string
  row_count: number
  source_clip_count: number
  source_block_count?: number
  narration_count?: number
  ad_exclusion_count?: number
  narration_text: string
  rows: ScriptTableRow[]
  generated_file: string
}

export type Job = {
  id: string; status: string; stage: string; progress: number; message: string
  output_path: string; error: string
  title: string; tags: string[]; description: string
  narration_text: string
  created_at: number; started_at: number; finished_at: number; elapsed_seconds: number
}

export type SystemStats = {
  cpu_percent: number
  cpu_temperature: number | null
  memory_percent: number
  memory_used_gb: number
  memory_total_gb: number
  net_upload_bps: number
  net_download_bps: number
}
