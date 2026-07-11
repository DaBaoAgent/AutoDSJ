import { useEffect, useRef, useState } from 'react'
import {
  Activity, AudioLines, Check, Copy, Eye, FileUp, Film,
  KeyRound, LoaderCircle, RefreshCcw, Settings2,
  SlidersHorizontal, Square, Table2, Volume2, WandSparkles
} from 'lucide-react'
import type { Job, Material, MaterialCheck, MaterialChecks, ScriptTable, Settings, SystemStats, VisualStatus } from './types'

const BRAND_NAME = 'DaobaoAI-DY'
const APP_NAME = 'DaobaoAI-DY 大宝影视全自动智能剪辑工厂'
const QWEN37_PLUS_MAX_BATCH_FRAMES = 500

type Lang = 'zh' | 'en'
type SectionId = 'material' | 'script' | 'api'

const sections: { id: SectionId; icon: typeof Film }[] = [
  { id: 'material', icon: Film },
  { id: 'script', icon: AudioLines },
  { id: 'api', icon: KeyRound },
]

const i18n = {
  zh: {
    subtitle: '大宝影视全自动智能剪辑工厂',
    loading: '正在启动 DaobaoAI-DY...',
    save: '保存参数',
    saved: '参数已保存到本机',
    localConnected: '本地服务已连接',
    finished: '成片完成',
    currentTask: '当前任务',
    ready: '准备就绪',
    readyMessage: '配置完成后即可开始智能成片。',
    start: '一键智能成片',
    startWithScript: '一键智能成片',
    running: '正在智能成片',
    cancel: '取消任务',
    outputSaved: '成片已保存',
    publishTitle: '发布标题',
    tags: '精准标签',
    description: '视频描述',
    copy: '复制',
    clearScript: '清空文案',
    log: '操作日志',
    requestFailed: '请求失败',
    apiSuccess: '连接成功',
    sections: {
      material: ['上传剧集素材', '上传原片、SRT/ASS 字幕和手写解说文案，每项上传后会自动检测。'],
      script: ['脚本表与配音', '按你上传的原片/解说文案严格合成，不再自动生成文案。'],
      api: ['API 设置', '密钥仅保存在本机后端，页面不会显示明文。'],
    },
    nav: {
      material: '素材与时长',
      script: '文案与配音',
      api: 'API 设置',
    },
    stats: {
      title: '本机性能', temp: '温度', memory: '内存', used: '已用',
      download: '下载', upload: '上传', missing: '未检测',
      elapsed: '本任务已进行', totalElapsed: '本次任务耗时', noTask: '未开始',
    },
    material: {
      folder: '素材保存文件夹',
      placeholder: '例如：D:\\电视剧\\第2集新跑',
      pathTip: '路径提示',
      pathTipText: '也可以直接填写已放好原片、SRT/ASS 和解说文案的本机文件夹。',
      detect: '检测素材',
      uploadVideo: '上传剧集原片',
      uploadSubtitle: '上传 SRT/ASS 字幕',
      uploadScript: '上传解说文案',
      videoHint: '支持 MP4 / MKV',
      subtitleHint: '支持 SRT / ASS',
      scriptHint: '支持 TXT / MD / RTF / DOCX',
      uploadBusy: '正在上传',
      detected: '已检测',
      uploadDetected: '上传并检测通过',
      pending: '等待上传检测',
      minutes: '分钟',
      subtitles: '个字幕',
      trim: '原片裁切',
      trimHead: '跳过片头',
      trimTail: '跳过片尾',
      paddingHead: '成片片头缓冲',
      paddingTail: '成片片尾缓冲',
      visual: '视觉帧识别',
      visualMode: '识别帧策略',
      visualThree: '前中后三帧/关键间隔',
      visualDense: '密集识别（更准更慢）',
      visualBatchSize: '每批识别帧数',
      visualBatchHint: 'qwen3.7-plus 官方限流为 30000 RPM，折算 RPS 上限 500；默认小批量更稳。',
      visualStart: '开始视觉识别',
      visualBusy: '正在视觉识别',
      visualReady: '视觉识别已完成',
      visualPending: '完成素材检测后，先做视觉识别，再生成脚本表。',
      visualNeed: '请先上传并检测原片、SRT/ASS 字幕和解说文案。',
      output: '输出设置',
      target: '目标成片时长',
      targetHint: '用于规划文案篇幅，不会通过增加句间停顿或整体变速强行凑时长。',
      resolution: '输出分辨率',
      sourceVolume: '原片段音量固定 100%',
      dramaPlan: '电视剧插片规则',
      sourceCount: '参与剪辑的原片数量',
      insertClipCount: '插入原片片段数量',
      clipLength: '每段原片长度',
      keepSourceAudio: '插入原片对白段（原声100%，无配音）',
      audioRule: '合成规则：按上传文案里的“原片/解说”顺序往复剪辑；原片段不叠配音，解说段配音 100%。',
      sourcePlayVolume: '播放原片时音量',
      narrationSourceVolume: '解说时原片音量',
      hookClipFirst: '片头优先插入钩子原片',
      suspenseEnding: '片尾保留悬疑反转',
      selectedVideos: '已选原片',
      totalDuration: '总时长',
      warnings: '检测提示',
      advanced: '高级编码设置',
      seconds: '秒',
    },
    script: {
      style: '解说风格',
      baseStyle: '电视剧类型',
      factual: '事实严谨',
      conversational: '口语程度',
      humor: '幽默程度',
      scriptTable: '手写文案脚本表',
      generateTable: '生成脚本表',
      generatingTable: '正在生成脚本表',
      tableReady: '脚本表已生成，可锁定关键画面或快速替换候选画面',
      sourceRow: '原片',
      narrationRow: '解说',
      matchScore: '匹配',
      lock: '锁定',
      unlock: '解锁',
      replaceClip: '换画面',
      tableEmpty: '先完成素材检测和视觉帧识别，再点击“生成脚本表”。',
      voice: '配音音色',
      system: '默认音色',
      clone: '百炼克隆音色',
      gpt: '本地 GPT-SoVITS',
      systemVoice: '百炼默认音色',
      referenceAudio: '被克隆音色的本机地址',
      referenceHint: '当前默认使用 yatou2.wav；参考文字来自 yatou1参考文字.txt，必须与音频逐字一致',
      referenceText: '参考音频对应文字',
      referenceTextHint: '必须与参考音频中说的话逐字一致',
      engine: '本地引擎地址',
      polish: '美化音色（提升音频质量）',
      gptSplit: '分句方式',
      gptTemperature: '采样温度',
      gptTopP: 'Top-P',
      gptTopK: 'Top-K',
      gptRepeat: '重复惩罚',
      gptSeed: '随机种子',
      gptInfo: 'GPT-SoVITS 优先使用 CUDA，并以固定种子整段配音；完成后再按自然停顿匹配画面，避免短句重复克隆造成音色漂移。',
      testVoice: '测试配音',
      testingVoice: '正在生成测试配音，请稍候...',
      voiceReady: '测试配音已生成',
      voiceFailed: '测试失败',
      cloneId: '克隆音色 ID',
      qwenModel: '百炼音频模型',
      qwenReferenceAudio: '被克隆音色的本机地址 / 公网 URL',
      qwenReferenceTextPath: '参考文本文件地址',
      qwenReferenceHint: 'Qwen-TTS 支持本机音频路径；CosyVoice 创建新音色需要公网可访问音频 URL，已有音色可直接填写克隆音色 ID。',
      speed: '语速',
      volume: '音量',
      pitch: '音高',
      fullScript: '上传文案中的解说稿',
      generate: '解析文案',
      generating: '正在生成',
      scriptReady: '文案已解析',
      scriptStart: '开始解析上传文案，请稍候。',
      scriptDone: '上传文案已解析。',
      scriptFailed: '生成失败',
      emptyHint: '生成脚本表后会显示从“解说：”段落提取出的配音稿',
      lineHelp: '这里仅预览“解说：”段落；配音时会自动去掉“原片：/解说：”标签。',
      placeholder: '这里显示上传文案中“解说：”后面的配音内容；配音时不会包含“原片：/解说：”标签。',
      pathTipText: '浏览器不能读取音频绝对路径，请右键音频文件复制完整路径后粘贴。',
      estimatedDuration: '预计成片',
      sentenceUnit: '句',
      charUnit: '字',
    },
    api: {
      language: '界面语言',
      chinese: '中文',
      english: 'English',
      voiceKey: '配音 · 阿里百炼',
      visionKey: '视觉 · 百炼',
      test: '测试连接',
      visionModel: '视觉模型',
    },
    colors: ['白', '黄', '红', '橙', '青', '绿', '蓝', '紫', '黑', '灰'],
    fontCategories: {
      current: '当前字体',
      system: '系统字体',
      english: '英文常用字幕',
      englishPoster: '英文标题字体',
      chinese: '中文常用字幕',
      chinesePoster: '中文标题字体',
    },
  },
  en: {
    subtitle: 'Fully Automated Drama Editing Factory',
    loading: 'Starting DaobaoAI-DY...',
    save: 'Save Settings',
    saved: 'Settings saved locally',
    localConnected: 'Local service connected',
    finished: 'Video complete',
    currentTask: 'Current Task',
    ready: 'Ready',
    readyMessage: 'Start when the settings are complete.',
    start: 'Create Video',
    startWithScript: 'One-click Smart Render',
    running: 'Creating Video',
    cancel: 'Cancel Task',
    outputSaved: 'Video saved',
    publishTitle: 'Publish Title',
    tags: 'Precise Tags',
    description: 'Description',
    copy: 'Copy',
    clearScript: 'Clear Script',
    log: 'Activity Log',
    requestFailed: 'Request failed',
    apiSuccess: 'connected',
    sections: {
      material: ['Upload Drama Assets', 'Upload source video, SRT/ASS subtitles, and your manual script.'],
      script: ['Script Table & Voice', 'Create strictly from your uploaded source/narration script.'],
      api: ['API Settings', 'Keys are stored only by the local backend and never shown in plain text.'],
    },
    nav: {
      material: 'Media & Length',
      script: 'Script & Voice',
      api: 'API Settings',
    },
    stats: {
      title: 'System Monitor', temp: 'Temp', memory: 'Memory', used: 'Used',
      download: 'Download', upload: 'Upload', missing: 'N/A',
      elapsed: 'Task Elapsed', totalElapsed: 'Task Total', noTask: 'Not started',
    },
    material: {
      folder: 'Asset Folder',
      placeholder: 'Example: D:\\Drama\\Episode 2',
      pathTip: 'Path Tip',
      pathTipText: 'You can also paste a local folder that already contains the video, SRT/ASS subtitles, and script.',
      detect: 'Detect Media',
      uploadVideo: 'Upload Video',
      uploadSubtitle: 'Upload SRT/ASS',
      uploadScript: 'Upload Script',
      videoHint: 'MP4 / MKV',
      subtitleHint: 'SRT / ASS',
      scriptHint: 'TXT / MD / RTF / DOCX',
      uploadBusy: 'Uploading',
      detected: 'Detected',
      uploadDetected: 'Uploaded and checked',
      pending: 'Waiting for upload check',
      minutes: 'min',
      subtitles: 'subtitle files',
      trim: 'Source Trim',
      trimHead: 'Skip Opening',
      trimTail: 'Skip Ending',
      paddingHead: 'Output Head Padding',
      paddingTail: 'Output Tail Padding',
      visual: 'Visual Frame Recognition',
      visualMode: 'Frame strategy',
      visualThree: 'Front/middle/end key frames',
      visualDense: 'Dense recognition',
      visualBatchSize: 'Frames per batch',
      visualBatchHint: 'qwen3.7-plus is listed at 30000 RPM, equivalent to 500 RPS. Smaller batches are steadier.',
      visualStart: 'Start visual recognition',
      visualBusy: 'Recognizing frames',
      visualReady: 'Visual recognition complete',
      visualPending: 'After media checks, run visual recognition before generating the table.',
      visualNeed: 'Upload and check the video, subtitles, and script first.',
      output: 'Output',
      target: 'Target Runtime',
      targetHint: 'Used for script planning. The system will not force duration by adding pauses or stretching audio.',
      resolution: 'Resolution',
      sourceVolume: 'Source clip volume fixed at 100%',
      dramaPlan: 'Drama Insert Rules',
      sourceCount: 'Source Videos',
      insertClipCount: 'Inserted Source Clips',
      clipLength: 'Clip Length',
      keepSourceAudio: 'Insert source dialogue clips (100% original audio, no narration)',
      audioRule: 'Render rule: follow the uploaded Source/Narration order exactly; source clips have no voiceover, narration clips use voiceover at 100%.',
      sourcePlayVolume: 'Source clip volume',
      narrationSourceVolume: 'Source volume under narration',
      hookClipFirst: 'Use source hook at the opening',
      suspenseEnding: 'Keep suspense at the ending',
      selectedVideos: 'Selected videos',
      totalDuration: 'Total runtime',
      warnings: 'Detection notes',
      advanced: 'Advanced Encoding',
      seconds: 'sec',
    },
    script: {
      style: 'Narration Style',
      baseStyle: 'Drama Type',
      factual: 'Factual',
      conversational: 'Conversational',
      humor: 'Humor',
      scriptTable: 'Manual Script Table',
      generateTable: 'Generate Table',
      generatingTable: 'Generating table',
      tableReady: 'Script table generated. Lock key shots or switch candidate clips.',
      sourceRow: 'Source',
      narrationRow: 'Narration',
      matchScore: 'Match',
      lock: 'Lock',
      unlock: 'Unlock',
      replaceClip: 'Replace',
      tableEmpty: 'Finish media checks and visual recognition, then click Generate Table.',
      voice: 'Voice',
      system: 'Default Voice',
      clone: 'Bailian Clone',
      gpt: 'Local GPT-SoVITS',
      systemVoice: 'Bailian Default Voice',
      referenceAudio: 'Reference Audio Path',
      referenceHint: 'Default reference is yatou2.wav; the reference text comes from yatou1 reference text and must match exactly',
      referenceText: 'Reference Text',
      referenceTextHint: 'Must exactly match the spoken reference audio',
      engine: 'Local Engine Path',
      polish: 'Polish voice quality',
      gptSplit: 'Text split',
      gptTemperature: 'Temperature',
      gptTopP: 'Top-P',
      gptTopK: 'Top-K',
      gptRepeat: 'Repeat penalty',
      gptSeed: 'Seed',
      gptInfo: 'GPT-SoVITS prefers CUDA and uses a fixed seed for each full narration block, then maps natural pauses to visual shots to prevent short-clip voice drift.',
      testVoice: 'Test Voice',
      testingVoice: 'Generating test voice...',
      voiceReady: 'Test voice generated',
      voiceFailed: 'Voice test failed',
      cloneId: 'Clone Voice ID',
      qwenModel: 'Bailian TTS Model',
      qwenReferenceAudio: 'Local Voice Sample Path',
      qwenReferenceTextPath: 'Reference Text File Path',
      qwenReferenceHint: 'Qwen-TTS uses the local audio sample to create or reuse a cloned voice; the text path is saved for local cache identity.',
      speed: 'Speed',
      volume: 'Volume',
      pitch: 'Pitch',
      fullScript: 'Narration From Uploaded Script',
      generate: 'Generate Script',
      generating: 'Generating',
      scriptReady: 'Script parsed.',
      scriptStart: 'Parsing uploaded script...',
      scriptDone: 'Uploaded script parsed.',
      scriptFailed: 'Generation failed',
      emptyHint: 'After table generation, narration text extracted from your script appears here',
      lineHelp: 'Preview only. The system removes Source/Narration labels before TTS.',
      placeholder: 'Narration extracted from the uploaded script appears here. Labels are removed before TTS.',
      pathTipText: 'Browsers cannot read local audio paths. Copy the full audio path and paste it here.',
      estimatedDuration: 'Estimated runtime',
      sentenceUnit: 'sentences',
      charUnit: 'chars',
    },
    api: {
      language: 'Interface Language',
      chinese: '中文',
      english: 'English',
      voiceKey: 'Voice · Bailian',
      visionKey: 'Vision · Bailian',
      test: 'Test',
      visionModel: 'Vision Model',
    },
    colors: ['White', 'Yellow', 'Red', 'Orange', 'Cyan', 'Green', 'Blue', 'Purple', 'Black', 'Gray'],
    fontCategories: {
      current: 'Current Font',
      system: 'System Fonts',
      english: 'Common English Subtitle Fonts',
      englishPoster: 'English Title Fonts',
      chinese: 'Common Chinese Subtitle Fonts',
      chinesePoster: 'Chinese Title Fonts',
    },
  },
} as const

async function jsonFetch(url: string, options?: RequestInit) {
  const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) } })
  const text = await response.text()
  const body = text ? (() => { try { return JSON.parse(text) } catch { return { detail: text } } })() : {}
  if (!response.ok) throw new Error(body.detail || 'Request failed')
  return body
}

function Field({ label, hint, children }: { label: string, hint?: string, children: React.ReactNode }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <small>{hint}</small>}</label>
}

function Range({ value, min, max, step = 1, suffix = '', onChange }: {
  value: number, min: number, max: number, step?: number, suffix?: string, onChange: (v: number) => void
}) {
  return <div className="range-row"><input type="range" min={min} max={max} step={step} value={value}
    onChange={e => onChange(Number(e.target.value))}/><output>{value}{suffix}</output></div>
}

function Toggle({ checked, onChange, label }: { checked: boolean, onChange: (v: boolean) => void, label: string }) {
  return <button type="button" className={`toggle-row ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)}>
    <span className="toggle"><i /></span><span>{label}</span>
  </button>
}

function MaterialStatus({ check, pending }: { check?: MaterialCheck, pending: string }) {
  if (!check) return <div className="upload-status pending"><span>{pending}</span></div>
  return <div className="upload-status ready">
    <div><Check size={16}/><b>{check.title}</b></div>
    <p>{check.summary}</p>
    {check.details?.length > 0 && <small>{check.details.join('；')}</small>}
  </div>
}

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const lang: Lang = settings?.ui?.language || 'zh'
  const t = i18n[lang]
  const [active, setActive] = useState<SectionId>('material')
  const [material, setMaterial] = useState<Material | null>(null)
  const [materialChecks, setMaterialChecks] = useState<MaterialChecks>({})
  const [visualStatus, setVisualStatus] = useState<VisualStatus | null>(null)
  const [visualMode, setVisualMode] = useState<'three' | 'dense'>('three')
  const [visualBatchSize, setVisualBatchSize] = useState(3)
  const [scriptTable, setScriptTable] = useState<ScriptTable | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [logs, setLogs] = useState<string[]>([`[系统] ${APP_NAME} 已就绪，等待素材。`])
  const [busy, setBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [visualBusy, setVisualBusy] = useState(false)
  const [scriptBusy, setScriptBusy] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [narrationText, setNarrationText] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [audioProvider, setAudioProvider] = useState<'qwen' | 'gpt_sovits' | ''>('')
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000)
  const [workflowStartedAt, setWorkflowStartedAt] = useState(0)
  const [workflowFinishedAt, setWorkflowFinishedAt] = useState(0)
  const [systemVoices, setSystemVoices] = useState<{id:string, name:string}[]>([
    { id: 'Cherry', name: 'Cherry / 阳光自然' },
    { id: 'Serena', name: 'Serena / 温柔' },
    { id: 'Ethan', name: 'Ethan / 自然男声' },
  ])
  const logRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  const refreshMaterialStatus = async (folder?: string) => {
    const target = (folder || settings?.material_folder || '').trim()
    if (!target) {
      setMaterialChecks({})
      setMaterial(null)
      return
    }
    const status = await jsonFetch('/api/materials/status', {
      method: 'POST',
      body: JSON.stringify({ folder: target })
    })
    setMaterialChecks(status.checks || {})
    setMaterial(status.material || null)
    await refreshVisualStatus(target)
    return status as { checks?: MaterialChecks; material?: Material | null }
  }

  const refreshVisualStatus = async (folder?: string) => {
    const target = (folder || settings?.material_folder || '').trim()
    if (!target) {
      setVisualStatus(null)
      return null
    }
    const status = await jsonFetch('/api/source-index/status', {
      method: 'POST',
      body: JSON.stringify({ folder: target })
    })
    setVisualStatus(status)
    return status as VisualStatus
  }

  useEffect(() => {
    jsonFetch('/api/config').then(data => {
      setSettings(data)
      if (data.material_folder) refreshMaterialStatus(data.material_folder).catch(() => {})
    }).catch(e => setNotice(e.message))
  }, [])
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' }) }, [logs])
  useEffect(() => { jsonFetch('/api/voices/list').then(data => {
    const list = Array.isArray(data) ? data : (data.voices || [])
    if (list.length > 0) setSystemVoices(list)
  }).catch(() => {}) }, [])
  useEffect(() => {
    const loadStats = () => jsonFetch('/api/system-stats').then(setStats).catch(() => {})
    loadStats()
    const timer = window.setInterval(loadStats, 2000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    const timer = window.setInterval(() => setNowSeconds(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const formatSpeed = (value: number) => {
    if (!Number.isFinite(value) || value <= 0) return '0 KB/s'
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB/s`
    return `${(value / 1024).toFixed(0)} KB/s`
  }

  const formatElapsed = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
    const total = Math.floor(seconds)
    const h = Math.floor(total / 3600)
    const m = Math.floor((total % 3600) / 60)
    const s = total % 60
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    return `${m}:${String(s).padStart(2, '0')}`
  }

  const taskElapsedValue = () => {
    if (workflowStartedAt > 0) {
      return formatElapsed((workflowFinishedAt || nowSeconds) - workflowStartedAt)
    }
    if (!job) return t.stats.noTask
    if (['success', 'failed', 'cancelled'].includes(job.status)) {
      const elapsed = job.elapsed_seconds || (job.finished_at && job.started_at ? job.finished_at - job.started_at : 0)
      return formatElapsed(elapsed)
    }
    const start = job.started_at || job.created_at
    return start ? formatElapsed(nowSeconds - start) : '0:00'
  }

  const narrationCharCount = () => narrationText.replace(/\s/g, '').length
  const narrationLineCount = () => narrationText.split(/\n/).filter(Boolean).length
  const estimatedNarrationSeconds = () => {
    if (!settings) return 0
    const baseCps = settings.voice.mode === 'clone' && settings.voice.provider === 'gpt_sovits'
      ? 2.4
      : settings.voice.mode === 'clone'
        ? 5.0
        : 4.1
    return narrationCharCount() / Math.max(0.1, baseCps * settings.voice.speech_rate)
  }
  const formatRuntimeMinutes = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds <= 0) return '0 分钟'
    return `${(seconds / 60).toFixed(1)} ${lang === 'zh' ? '分钟' : 'min'}`
  }

  const update = (group: keyof Settings, key: string, value: unknown) => setSettings(current => {
    if (!current) return current
    if (group === 'material_folder') return { ...current, material_folder: String(value) }
    return { ...current, [group]: { ...(current[group] as object), [key]: value } }
  })
  const workflowSettings = () => settings ? {
    ...settings,
    drama: { ...settings.drama, keep_source_audio: true },
  } : null
  const materialChecksReady = (checks?: MaterialChecks) => Boolean(checks?.video && checks?.subtitle && checks?.script)
  const materialsReady = materialChecksReady(materialChecks)
  const canGenerateScript = Boolean(settings?.material_folder && materialsReady && visualStatus?.ready)

  const startVisualIndex = async () => {
    const activeSettings = workflowSettings()
    if (!activeSettings) return
    if (!materialsReady) {
      setNotice(t.material.visualNeed)
      return
    }
    setVisualBusy(true)
    setBusy(true)
    setNotice('')
    setWorkflowStartedAt(Date.now() / 1000)
    setWorkflowFinishedAt(0)
    setScriptTable(null)
    setNarrationText('')
    const interval = visualMode === 'dense' ? 3.0 : 12.0
    const batchSize = Math.max(1, Math.min(QWEN37_PLUS_MAX_BATCH_FRAMES, visualBatchSize))
    setLogs(x => [...x, `[${t.material.visual}] 开始识别，策略：${visualMode === 'dense' ? t.material.visualDense : t.material.visualThree}，每批 ${batchSize} 帧。`])
    let lastProgress = -1
    const timer = window.setInterval(async () => {
      try {
        const status = await refreshVisualStatus(activeSettings.material_folder)
        if (status && status.progress !== lastProgress) {
          lastProgress = status.progress
          setLogs(x => [...x, `[${t.material.visual}] ${status.message}（${status.progress}%）`])
        }
      } catch {
        // The run request below owns the final error message.
      }
    }, 1500)
    try {
      const result = await jsonFetch('/api/source-index/run', {
        method: 'POST',
        body: JSON.stringify({
          settings: activeSettings,
          frame_interval: interval,
          visual_batch_size: batchSize,
          visual_workers: 1,
          force_visual: true,
          enable_visual_model: true,
        })
      })
      const status = await refreshVisualStatus(activeSettings.material_folder)
      setSettings(activeSettings)
      setLogs(x => [...x, `[${t.material.visual}] 完成：${result.visual_success_count}/${result.visual_frame_count} 帧可用，已生成 ${result.candidate_count} 个候选片段。`])
      setNotice(status?.ready ? t.material.visualReady : status?.message || t.material.visualReady)
    } catch (e) {
      setNotice((e as Error).message)
      setLogs(x => [...x, `[${t.material.visual}] 失败：${(e as Error).message}`])
    } finally {
      window.clearInterval(timer)
      setWorkflowFinishedAt(Date.now() / 1000)
      setBusy(false)
      setVisualBusy(false)
    }
  }

  const generateScriptTable = async () => {
    const activeSettings = workflowSettings()
    if (!activeSettings) return
    if (!visualStatus?.ready) {
      setNotice('请先完成“视觉帧识别”，再生成脚本表。')
      return
    }
    setScriptBusy(true); setBusy(true); setNotice('')
    setWorkflowStartedAt(Date.now() / 1000)
    setWorkflowFinishedAt(0)
    setLogs(x => [...x, `[${t.nav.script}] 开始生成脚本表。`])
    try {
      const result = await jsonFetch('/api/materials/detect-manual', {
        method: 'POST',
        body: JSON.stringify({ settings: activeSettings })
      })
      setSettings(activeSettings)
      setMaterial(result.material)
      setScriptTable(result.script_table)
      setNarrationText(result.narration_text || '')
      const sourceBlocks = result.script_table.source_block_count || result.script_table.source_clip_count
      const adText = result.script_table.ad_exclusion_count ? `，已排除 ${result.script_table.ad_exclusion_count} 段插片广告` : ''
      setLogs(x => [...x, `[${t.nav.script}] 已生成脚本表：${result.material.width}x${result.material.height}, ${sourceBlocks} 段原片文案拆为 ${result.script_table.source_clip_count} 个精准片段/${result.script_table.narration_count || sourceBlocks} 段解说${adText}。`])
      setNotice(t.script.tableReady)
    } catch (e) {
      setNotice((e as Error).message)
      setLogs(x => [...x, `[${t.nav.script}] 失败：${(e as Error).message}`])
    } finally {
      setWorkflowFinishedAt(Date.now() / 1000)
      setBusy(false)
      setScriptBusy(false)
    }
  }

  const uploadMaterial = async (kind: 'video' | 'subtitle' | 'script', file?: File | null) => {
    if (!file || !settings) return
    setUploadBusy(true)
    setNotice('')
    try {
      const form = new FormData()
      form.append('kind', kind)
      form.append('folder', settings.material_folder || '')
      form.append('file', file)
      const response = await fetch('/api/materials/upload', { method: 'POST', body: form })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || t.requestFailed)
      setSettings(current => current ? { ...current, material_folder: body.folder } : current)
      setMaterial(null)
      setScriptTable(null)
      setNarrationText('')
      if (kind === 'video' || kind === 'subtitle') setVisualStatus(null)
      try {
        await refreshMaterialStatus(body.folder)
      } catch {
        if (body.check) setMaterialChecks(current => ({ ...current, [kind]: body.check }))
      }
      setLogs(x => [...x, `[${t.nav.material}] 已上传并检测 ${file.name}`])
      setNotice(`${t.material.uploadDetected}：${file.name}`)
    } catch (e) {
      setNotice((e as Error).message)
    } finally {
      setUploadBusy(false)
    }
  }

  const start = async () => {
    const activeSettings = workflowSettings()
    if (!activeSettings || running) return
    setWorkflowStartedAt(Date.now() / 1000)
    setWorkflowFinishedAt(0)
    setBusy(true); setNotice(''); setLogs(['[工作流] 一键智能成片启动，正在检测当前进度。'])
    try {
      const materialStatus = await refreshMaterialStatus(activeSettings.material_folder)
      if (!materialChecksReady(materialStatus?.checks)) {
        throw new Error(t.material.visualNeed)
      }
      let currentVisual = await refreshVisualStatus(activeSettings.material_folder)
      if (!currentVisual?.ready) {
        setVisualBusy(true)
        setScriptTable(null)
        setNarrationText('')
        const interval = visualMode === 'dense' ? 3.0 : 12.0
        const batchSize = Math.max(1, Math.min(QWEN37_PLUS_MAX_BATCH_FRAMES, visualBatchSize))
        setLogs(x => [...x, `[工作流] 素材已齐，继续执行视觉帧识别，每批 ${batchSize} 帧。`])
        let lastProgress = -1
        const timer = window.setInterval(async () => {
          try {
            const status = await refreshVisualStatus(activeSettings.material_folder)
            if (status && status.progress !== lastProgress) {
              lastProgress = status.progress
              setLogs(x => [...x, `[${t.material.visual}] ${status.message}（${status.progress}%）`])
            }
          } catch {
            // The run request owns the final error message.
          }
        }, 1500)
        try {
          const result = await jsonFetch('/api/source-index/run', {
            method: 'POST',
            body: JSON.stringify({
              settings: activeSettings,
              frame_interval: interval,
              visual_batch_size: batchSize,
              visual_workers: 1,
              force_visual: true,
              enable_visual_model: true,
            })
          })
          currentVisual = await refreshVisualStatus(activeSettings.material_folder)
          setLogs(x => [...x, `[${t.material.visual}] 完成：${result.visual_success_count}/${result.visual_frame_count} 帧可用，已生成 ${result.candidate_count} 个候选片段。`])
        } finally {
          window.clearInterval(timer)
          setVisualBusy(false)
        }
      }
      if (!currentVisual?.ready) {
        throw new Error(currentVisual?.message || '视觉帧识别未完成')
      }
      let currentScriptTable = scriptTable
      if (!currentScriptTable) {
        setScriptBusy(true)
        setLogs(x => [...x, '[工作流] 视觉帧已可用，继续生成脚本表。'])
        try {
          const result = await jsonFetch('/api/materials/detect-manual', {
            method: 'POST',
            body: JSON.stringify({ settings: activeSettings })
          })
          setSettings(activeSettings)
          setMaterial(result.material)
          setScriptTable(result.script_table)
          setNarrationText(result.narration_text || '')
          currentScriptTable = result.script_table
          const sourceBlocks = result.script_table.source_block_count || result.script_table.source_clip_count
          const adText = result.script_table.ad_exclusion_count ? `，已排除 ${result.script_table.ad_exclusion_count} 段插片广告` : ''
          setLogs(x => [...x, `[${t.nav.script}] 已生成脚本表：${result.material.width}x${result.material.height}, ${sourceBlocks} 段原片文案拆为 ${result.script_table.source_clip_count} 个精准片段/${result.script_table.narration_count || sourceBlocks} 段解说${adText}。`])
        } finally {
          setScriptBusy(false)
        }
      }
      if (!currentScriptTable) {
        throw new Error('脚本表未生成，无法开始成片')
      }
      setLogs(x => [...x, '[工作流] 前置步骤已完成，开始智能成片。'])
      const created = await jsonFetch('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ settings: activeSettings })
      })
      setJob(created)
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${location.host}/ws/jobs/${created.id}`)
      socket.onmessage = event => {
        const data = JSON.parse(event.data)
        if (data.type === 'log') setLogs(x => [...x, data.line])
        if (data.type === 'status') {
          setJob(data.job)
          if (data.job.narration_text) setNarrationText(data.job.narration_text)
          if (['success', 'failed', 'cancelled'].includes(data.job.status)) {
            setWorkflowFinishedAt(Date.now() / 1000)
            setBusy(false)
          }
        }
      }
      socket.onclose = () => setBusy(false)
    } catch (e) {
      setWorkflowFinishedAt(Date.now() / 1000)
      setNotice((e as Error).message)
      setLogs(x => [...x, `[工作流] 失败：${(e as Error).message}`])
      setBusy(false)
      setVisualBusy(false)
      setScriptBusy(false)
    }
  }

  const cancel = async () => { if (job) await jsonFetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' }) }
  const save = async () => {
    if (!settings) return
    try { const saved = await jsonFetch('/api/config', { method: 'PUT', body: JSON.stringify(settings) }); setSettings(saved); setNotice(t.saved) }
    catch(e) { setNotice((e as Error).message) }
  }
  const testApi = async (provider: string, key: string) => {
    setNotice('')
    try { await jsonFetch('/api/api-test', { method: 'POST', body: JSON.stringify({ provider, key }) }); setNotice(`${provider} ${t.apiSuccess}`) }
    catch(e) { setNotice((e as Error).message) }
  }

  const testGptVoice = async () => {
    if (!settings) return
    setVoiceBusy(true); setNotice(t.script.testingVoice)
    try {
      const response = await fetch('/api/voices/test-gpt-sovits', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reference_audio: settings.voice.gpt_sovits_reference_audio,
          reference_text: settings.voice.gpt_sovits_reference_text,
          engine_path: settings.voice.gpt_sovits_engine_path,
          speed: settings.voice.speech_rate,
          polish: settings.voice.polish_audio,
          seed: settings.voice.gpt_sovits_seed,
          text_split_method: settings.voice.gpt_sovits_text_split_method,
          temperature: settings.voice.gpt_sovits_temperature,
          top_p: settings.voice.gpt_sovits_top_p,
          top_k: settings.voice.gpt_sovits_top_k,
          repetition_penalty: settings.voice.gpt_sovits_repetition_penalty
        })
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || t.script.voiceFailed)
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      if (audioUrl) URL.revokeObjectURL(audioUrl)
      setAudioUrl(url)
      setAudioProvider('gpt_sovits')
      setNotice(t.script.voiceReady)
    } catch(e) { setNotice((e as Error).message) } finally { setVoiceBusy(false) }
  }

  const testQwenCloneVoice = async () => {
    if (!settings) return
    setVoiceBusy(true); setNotice(t.script.testingVoice)
    try {
      const response = await fetch('/api/voices/test-qwen-clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voice_id: settings.voice.clone_voice_id,
          model: settings.voice.qwen_clone_model,
          reference_audio: settings.voice.qwen_reference_audio,
          reference_text_path: settings.voice.qwen_reference_text_path,
          speed: settings.voice.speech_rate,
          volume: settings.voice.volume,
          pitch: settings.voice.pitch
        })
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || t.script.voiceFailed)
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      if (audioUrl) URL.revokeObjectURL(audioUrl)
      setAudioUrl(url)
      setAudioProvider('qwen')
      setNotice(t.script.voiceReady)
    } catch(e) { setNotice((e as Error).message) } finally { setVoiceBusy(false) }
  }

  const running = job?.status === 'running' || job?.status === 'queued'

  if (!settings) return <div className="boot"><LoaderCircle className="spin"/> {t.loading}</div>

  const apiRows = [
    ['dashscope_api_key', `${t.api.voiceKey} / ${t.api.visionKey}`, 'dashscope'],
  ] as const

  return <div className="app-shell">
    <header><div className="brand"><div className="brand-mark"><img src="/icon.png" alt={APP_NAME} style={{width:36,height:36,borderRadius:8}}/></div>
      <div><h1>{BRAND_NAME}</h1><p>{t.subtitle}</p></div></div>
      <div className="header-actions"><button onClick={save}><Settings2 size={14}/>{t.save}</button><div className={`status-pill ${running ? 'working' : job?.status || ''}`}><Activity size={14}/>
        {running ? job?.stage : job?.status === 'success' ? t.finished : t.localConnected}</div></div></header>

    <main><aside>{sections.map(({ id, icon: Icon }) => <button key={id} className={active === id ? 'active' : ''}
      onClick={() => setActive(id)}><Icon size={17}/>{t.nav[id]}</button>)}
      <div className="system-panel">
        <div className="system-panel-title"><Activity size={14}/>{t.stats.title}</div>
        <div className="metric"><span>{workflowFinishedAt || (job && ['success', 'failed', 'cancelled'].includes(job.status)) ? t.stats.totalElapsed : t.stats.elapsed}</span><b>{taskElapsedValue()}</b></div>
        <div className="metric"><span>CPU</span><b>{stats ? `${stats.cpu_percent}%` : '--'}</b></div>
        <div className="metric"><span>{t.stats.temp}</span><b>{stats?.cpu_temperature == null ? t.stats.missing : `${stats.cpu_temperature}°C`}</b></div>
        <div className="metric"><span>{t.stats.memory}</span><b>{stats ? `${stats.memory_percent}%` : '--'}</b></div>
        <div className="metric small"><span>{t.stats.used}</span><b>{stats ? `${stats.memory_used_gb}/${stats.memory_total_gb} GB` : '--'}</b></div>
        <div className="metric small"><span>{t.stats.download}</span><b>{stats ? formatSpeed(stats.net_download_bps) : '--'}</b></div>
        <div className="metric small"><span>{t.stats.upload}</span><b>{stats ? formatSpeed(stats.net_upload_bps) : '--'}</b></div>
      </div>
    </aside>

      <section className="workspace"><div className="workspace-main">
        {active === 'material' && <>
          <div className="section-title"><div><span>01</span><h2>{t.sections.material[0]}</h2></div><p>{t.sections.material[1]}</p></div>
          <div className="card hero-card">
            <Field label={t.material.folder} hint={t.material.pathTipText}>
              <input value={settings.material_folder} placeholder={t.material.placeholder}
                onChange={e => { update('material_folder', '', e.target.value); setMaterialChecks({}); setMaterial(null); setVisualStatus(null); setScriptTable(null) }}
                onBlur={() => refreshMaterialStatus(settings.material_folder).catch(e => setNotice(e.message))}/>
            </Field>
            <div className="upload-grid">
              <div className="upload-slot"><label className={`upload-tile ${materialChecks.video ? 'ready' : ''}`}><input type="file" accept=".mp4,.mkv,video/mp4,video/x-matroska"
                disabled={uploadBusy || running} onChange={e => uploadMaterial('video', e.target.files?.[0])}/>
                {materialChecks.video ? <Check size={18}/> : <FileUp size={18}/>}<b>{t.material.uploadVideo}</b><small>{t.material.videoHint}</small></label>
                <MaterialStatus check={materialChecks.video} pending={t.material.pending}/></div>
              <div className="upload-slot"><label className={`upload-tile ${materialChecks.subtitle ? 'ready' : ''}`}><input type="file" accept=".srt,.ass"
                disabled={uploadBusy || running} onChange={e => uploadMaterial('subtitle', e.target.files?.[0])}/>
                {materialChecks.subtitle ? <Check size={18}/> : <FileUp size={18}/>}<b>{t.material.uploadSubtitle}</b><small>{t.material.subtitleHint}</small></label>
                <MaterialStatus check={materialChecks.subtitle} pending={t.material.pending}/></div>
              <div className="upload-slot"><label className={`upload-tile ${materialChecks.script ? 'ready' : ''}`}><input type="file" accept=".txt,.md,.markdown,.rtf,.docx"
                disabled={uploadBusy || running} onChange={e => uploadMaterial('script', e.target.files?.[0])}/>
                {materialChecks.script ? <Check size={18}/> : <FileUp size={18}/>}<b>{t.material.uploadScript}</b><small>{t.material.scriptHint}</small></label>
                <MaterialStatus check={materialChecks.script} pending={t.material.pending}/></div>
            </div>
          </div>
          <div className="grid two"><div className="card"><h3>{t.material.trim}</h3>
            <Field label={t.material.trimHead}><Range value={settings.video.trim_head} min={1} max={300} suffix={` ${t.material.seconds}`} onChange={v => update('video','trim_head',v)}/></Field>
            <Field label={t.material.trimTail}><Range value={settings.video.trim_tail} min={1} max={300} suffix={` ${t.material.seconds}`} onChange={v => update('video','trim_tail',v)}/></Field>
            <Field label={t.material.paddingHead}><Range value={settings.video.padding_head} min={0} max={5} step={0.5} suffix={` ${t.material.seconds}`} onChange={v => update('video','padding_head',v)}/></Field>
            <Field label={t.material.paddingTail}><Range value={settings.video.padding_tail} min={0} max={5} step={0.5} suffix={` ${t.material.seconds}`} onChange={v => update('video','padding_tail',v)}/></Field>
          </div><div className="card"><h3>{t.material.output}</h3>
            <Field label={t.material.resolution}><select value={settings.video.resolution} onChange={e => update('video','resolution',e.target.value)}>{['720P','1080P','2K','4K'].map(x=><option key={x}>{x}</option>)}</select></Field>
            <h3>{t.material.dramaPlan}</h3>
            <Field label={t.material.sourcePlayVolume}><Range value={settings.drama.source_play_volume} min={0} max={100} suffix="%" onChange={v=>update('drama','source_play_volume',v)}/></Field>
            <Field label={t.material.narrationSourceVolume}><Range value={settings.drama.narration_source_volume} min={0} max={100} suffix="%" onChange={v=>update('drama','narration_source_volume',v)}/></Field>
            <small>{t.material.audioRule}</small>
            <details><summary><Settings2 size={15}/>{t.material.advanced}</summary><Field label="CRF"><Range value={settings.video.video_crf} min={14} max={32} onChange={v=>update('video','video_crf',v)}/></Field></details>
          </div></div>
          <div className={`card visual-card ${visualStatus?.ready ? 'ready' : ''}`}>
            <div className="visual-head"><h3><Eye size={17}/>{t.material.visual}</h3>
              <span>{visualStatus?.ready ? t.material.visualReady : visualStatus?.message || t.material.visualPending}</span></div>
            <Field label={t.material.visualMode}><select value={visualMode} onChange={e => setVisualMode(e.target.value as 'three' | 'dense')} disabled={visualBusy || running}>
              <option value="three">{t.material.visualThree}</option>
              <option value="dense">{t.material.visualDense}</option>
            </select></Field>
            <Field label={t.material.visualBatchSize} hint={t.material.visualBatchHint}><select value={visualBatchSize} onChange={e => setVisualBatchSize(Number(e.target.value))} disabled={visualBusy || running}>
              {Array.from({ length: QWEN37_PLUS_MAX_BATCH_FRAMES }, (_, index) => index + 1).map(value => <option key={value} value={value}>{value}</option>)}
            </select></Field>
            <div className="visual-progress"><i style={{width:`${visualStatus?.progress || 0}%`}}/></div>
            <div className="visual-meta"><span>{visualStatus?.success_count || 0}/{visualStatus?.frame_count || 0} 帧可用</span><span>{visualStatus?.model || settings.api.visual_model}</span></div>
            <button className="primary" onClick={startVisualIndex} disabled={visualBusy || busy || uploadBusy || running || !materialsReady}>
              {visualBusy ? <LoaderCircle className="spin" size={14}/> : <Eye size={14}/>}
              {visualBusy ? t.material.visualBusy : t.material.visualStart}
            </button>
          </div>
        </>}

        {active === 'script' && <><div className="section-title"><div><span>02</span><h2>{t.sections.script[0]}</h2></div><p>{t.sections.script[1]}</p></div>
          <div className="card script-table-card"><div className="script-table-head"><h3><Table2 size={17}/>{t.script.scriptTable}</h3>
            <button onClick={generateScriptTable} disabled={scriptBusy || visualBusy || uploadBusy || running || !canGenerateScript}>
              {scriptBusy ? <LoaderCircle className="spin" size={14}/> : <RefreshCcw size={14}/>}
              {scriptBusy ? t.script.generatingTable : t.script.generateTable}
            </button></div>
            {scriptTable ? <div className="script-table-list">
              {scriptTable.rows.map(row => {
                const isSource = row.row_type === 'source_clip'
                return <div key={row.row_id} className={`script-row ${isSource ? 'source' : 'narration'}`}>
                  <div className="script-row-main"><div className="script-row-meta">
                    <span>{isSource ? t.script.sourceRow : t.script.narrationRow}</span>
                    <span>{row.insert_role_label}</span>
                    <span>{row.source_time_text}</span>
                    <span>{isSource ? `${t.script.matchScore} ${Math.round((row.match_score || 0) * 100)}%` : '成片时逐句视觉匹配'}</span>
                  </div>
                  <p>{row.text}</p>
                  <small>{isSource ? '按字幕精确时间码播放原片对白' : '在这个前后剧情区间内按人物、动作和场景选镜，全片禁止复用'}</small></div>
                </div>
              })}
            </div> : <p className="table-empty">{t.script.tableEmpty}</p>}
          </div>
          <div className="card"><h3>{t.script.voice}</h3><div className="segmented"><button className={settings.voice.mode==='system'?'selected':''} onClick={()=>update('voice','mode','system')}>{t.script.system}</button><button className={settings.voice.mode==='clone'&&settings.voice.provider!=='gpt_sovits'?'selected':''} onClick={()=>{update('voice','mode','clone');update('voice','provider','qwen')}}>{t.script.clone}</button><button className={settings.voice.mode==='clone'&&settings.voice.provider==='gpt_sovits'?'selected':''} onClick={()=>{update('voice','mode','clone');update('voice','provider','gpt_sovits');update('voice','speech_rate',1.1)}}>{t.script.gpt}</button></div>
            {settings.voice.mode==='system'?<Field label={t.script.systemVoice}><select value={settings.voice.system_voice} onChange={e=>update('voice','system_voice',e.target.value)}>{systemVoices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>:
              settings.voice.provider==='gpt_sovits'?<>
                <Field label={t.script.referenceAudio} hint={t.script.referenceHint}>
                  <div className="path-input"><input placeholder="D:\voice\reference.wav" value={settings.voice.gpt_sovits_reference_audio} onChange={e=>update('voice','gpt_sovits_reference_audio',e.target.value)}/>
                    <button type="button" onClick={() => setNotice(t.script.pathTipText)}><FileUp size={17}/>{t.material.pathTip}</button></div>
                </Field>
                <Field label={t.script.referenceText} hint={t.script.referenceTextHint}><textarea rows={3} value={settings.voice.gpt_sovits_reference_text} onChange={e=>update('voice','gpt_sovits_reference_text',e.target.value)}/></Field>
                <Field label={t.script.engine}><input value={settings.voice.gpt_sovits_engine_path} onChange={e=>update('voice','gpt_sovits_engine_path',e.target.value)}/></Field>
                <div className="gpt-tuning-row">
                  <Toggle checked={settings.voice.polish_audio} onChange={v=>update('voice','polish_audio',v)} label={t.script.polish}/>
                  <Field label={t.script.gptSplit}><select value={settings.voice.gpt_sovits_text_split_method} onChange={e=>update('voice','gpt_sovits_text_split_method',e.target.value)}>
                    <option value="cut0">cut0 · 不切分</option>
                    <option value="cut1">cut1 · 四句一切</option>
                    <option value="cut2">cut2 · 约50字一切</option>
                    <option value="cut3">cut3 · 中文句号</option>
                    <option value="cut4">cut4 · 英文句号</option>
                    <option value="cut5">cut5 · 标点切分</option>
                  </select></Field>
                  <Field label={t.script.gptSeed}><input type="number" value={settings.voice.gpt_sovits_seed} onChange={e=>update('voice','gpt_sovits_seed',Number(e.target.value))}/></Field>
                </div>
                <div className="grid four gpt-tuning-controls">
                  <Field label={t.script.gptTemperature}><Range value={settings.voice.gpt_sovits_temperature} min={0.1} max={1.5} step={0.05} onChange={v=>update('voice','gpt_sovits_temperature',v)}/></Field>
                  <Field label={t.script.gptTopP}><Range value={settings.voice.gpt_sovits_top_p} min={0.1} max={1} step={0.05} onChange={v=>update('voice','gpt_sovits_top_p',v)}/></Field>
                  <Field label={t.script.gptTopK}><Range value={settings.voice.gpt_sovits_top_k} min={1} max={100} onChange={v=>update('voice','gpt_sovits_top_k',v)}/></Field>
                  <Field label={t.script.gptRepeat}><Range value={settings.voice.gpt_sovits_repetition_penalty} min={0.8} max={2} step={0.05} onChange={v=>update('voice','gpt_sovits_repetition_penalty',v)}/></Field>
                </div>
                <div className="info-box">{t.script.gptInfo}</div>
                <button className="primary" style={{marginTop:12}} onClick={testGptVoice} disabled={voiceBusy || !settings.voice.gpt_sovits_reference_audio || !settings.voice.gpt_sovits_engine_path}>{voiceBusy ? <LoaderCircle className="spin" size={14}/> : <Volume2 size={14}/>} {t.script.testVoice}</button>
                {audioUrl && audioProvider === 'gpt_sovits' && <div className="audio-player"><audio controls ref={audioRef} src={audioUrl}/></div>}</>:
              <>
                <Field label={t.script.qwenModel}><input list="bailian-models" value={settings.voice.qwen_clone_model} onChange={e=>update('voice','qwen_clone_model',e.target.value)}/>
                  <datalist id="bailian-models"><option value="qwen3-tts-vc-2026-01-22"/><option value="cosyvoice-v3.5-plus"/></datalist></Field>
                <Field label={t.script.cloneId}><input value={settings.voice.clone_voice_id} onChange={e=>update('voice','clone_voice_id',e.target.value)}/></Field>
                <Field label={t.script.qwenReferenceAudio} hint={t.script.qwenReferenceHint}>
                  <div className="path-input"><input placeholder="D:\voice\reference.wav" value={settings.voice.qwen_reference_audio} onChange={e=>update('voice','qwen_reference_audio',e.target.value)}/>
                    <button type="button" onClick={() => setNotice(t.script.pathTipText)}><FileUp size={17}/>{t.material.pathTip}</button></div>
                </Field>
                <Field label={t.script.qwenReferenceTextPath}>
                  <div className="path-input"><input placeholder="D:\voice\reference.txt" value={settings.voice.qwen_reference_text_path} onChange={e=>update('voice','qwen_reference_text_path',e.target.value)}/>
                    <button type="button" onClick={() => setNotice(t.script.pathTipText)}><FileUp size={17}/>{t.material.pathTip}</button></div>
                </Field>
                <button className="primary" style={{marginTop:12}} onClick={testQwenCloneVoice} disabled={voiceBusy || !settings.voice.qwen_clone_model || (!settings.voice.clone_voice_id && !settings.voice.qwen_reference_audio)}>{voiceBusy ? <LoaderCircle className="spin" size={14}/> : <Volume2 size={14}/>} {t.script.testVoice}</button>
                {audioUrl && audioProvider === 'qwen' && <div className="audio-player"><audio controls ref={audioRef} src={audioUrl}/></div>}
              </>}
            <div className="grid three"><Field label={t.script.speed}><Range value={settings.voice.speech_rate} min={0.7} max={1.5} step={0.1} suffix="x" onChange={v=>update('voice','speech_rate',v)}/></Field>
              <Field label={t.script.volume}><Range value={settings.voice.volume} min={0} max={100} suffix="%" onChange={v=>update('voice','volume',v)}/></Field>
              <Field label={t.script.pitch}><Range value={settings.voice.pitch} min={0.5} max={2} step={0.1} suffix="x" onChange={v=>update('voice','pitch',v)}/></Field></div></div>
          <div className={`card script-editor`}><div className="script-editor-head">
            <div><h3>{t.script.fullScript}</h3><small>{narrationText ? `${narrationLineCount()} ${t.script.sentenceUnit} · ${narrationCharCount()} ${t.script.charUnit} · ${t.script.estimatedDuration} ${formatRuntimeMinutes(estimatedNarrationSeconds())}` : t.script.emptyHint}</small></div>
            <div style={{display:'flex', gap:6, alignItems:'center'}}>
              {narrationText && <button onClick={() => navigator.clipboard.writeText(narrationText)}><Copy size={14}/>{t.copy}</button>}
            </div></div>
            <textarea rows={18} value={narrationText} placeholder={t.script.placeholder} readOnly/>
            <p>{t.script.lineHelp}</p></div>
        </>}

        {active === 'api' && <><div className="section-title"><div><span>03</span><h2>{t.sections.api[0]}</h2></div><p>{t.sections.api[1]}</p></div>
          <div className="card api-card">
            <Field label={t.api.language}><div className="segmented language-toggle">
              <button className={settings.ui.language === 'zh' ? 'selected' : ''} onClick={() => update('ui','language','zh')}>{t.api.chinese}</button>
              <button className={settings.ui.language === 'en' ? 'selected' : ''} onClick={() => update('ui','language','en')}>{t.api.english}</button>
            </div></Field>
            {apiRows.map(([key,label,provider])=><Field key={key} label={label}><div className="secret"><input type="password" value={settings.api[key]||''} onChange={e=>update('api',key,e.target.value)}/><button onClick={()=>testApi(provider,settings.api[key]||'')}>{t.api.test}</button></div></Field>)}
            <Field label={t.api.visionModel}><input value={settings.api.visual_model||''} onChange={e=>update('api','visual_model',e.target.value)}/></Field></div>
        </>}
      </div>

      <div className="workspace-side"><div className="run-card"><div className="run-head"><div><small>{t.currentTask}</small><b>{job?.stage || t.ready}</b></div><span>{job?.progress || 0}%</span></div>
        <div className="progress"><i style={{width:`${job?.progress||0}%`}}/></div><p>{job?.message || t.readyMessage}</p>
        {!running ? <button className="primary" onClick={start} disabled={busy || uploadBusy}><WandSparkles size={19}/>{t.startWithScript}</button> :
          <button className="primary running" disabled><LoaderCircle className="spin" size={19}/>{t.running}</button>}
        {running && <button className="cancel" onClick={cancel}><Square size={14}/>{t.cancel}</button>}
        {job?.status==='success'&&<div className="success-output"><Check/>{t.outputSaved}<br/><small>{job.output_path}</small></div>}
        {job?.status==='success'&&job.title&&<div className="publish-result"><div><b>{t.publishTitle}</b><button onClick={()=>navigator.clipboard.writeText(job.title)}><Copy size={12}/></button></div><p>{job.title}</p>
          <div><b>{t.tags}</b><button onClick={()=>navigator.clipboard.writeText(job.tags.map(x=>'#'+x).join(' '))}><Copy size={12}/></button></div><p>{job.tags.map(x=>'#'+x).join(' ')}</p>
          <div><b>{t.description}</b><button onClick={()=>navigator.clipboard.writeText(job.description)}><Copy size={12}/></button></div><p>{job.description}</p></div>}
      </div>
      {notice && <div className="notice">{notice}</div>}
      <div className="log-card"><div className="log-head"><span><SlidersHorizontal size={15}/>{t.log}</span><button onClick={()=>navigator.clipboard.writeText(logs.join('\n'))}><Copy size={14}/>{t.copy}</button></div>
        <div className="logs" ref={logRef}>{logs.map((x,i)=><div key={i} className={x.includes('失败') || x.toLowerCase().includes('failed') ? 'error' : x.includes('完成') || x.toLowerCase().includes('done') ? 'success' : ''}>{x}</div>)}</div></div>
      </div></section>
    </main>
  </div>
}
