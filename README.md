# DY 工作流 · 电视剧/短剧全自动智能剪辑（纯后端）

DY 工作流是一套面向电视剧解说、短剧二创与影视口播的 AI 智能剪辑管线。
它把**原片 + SRT/ASS 字幕 + 人工「原片/解说」文案**组合起来，经过视觉识别、
字幕定位、广告过滤、AI 克隆配音、精准画面匹配与自动成片，产出一条成片视频。

本版本**已移除所有前端 WebUI 与端口服务**，只保留纯后端命令行工作流，
由 Hermes 智能体驱动使用：对 Hermes 说「**DY**」即自动进入本工作流。

## 一条正式管线

```
素材文件夹（原片 + 字幕 + 原片/解说文案）
  → 检测素材 → 视觉识别 → 生成脚本表 → 配音 + 剪辑 + 后处理 → ★ 成片.mp4
```

## 快速开始

```bash
# 环境自检（ffmpeg / 依赖 / API Key）
python dy.py doctor

# 一键全流程（首次或换素材时指定文件夹）
python dy.py run --folder "D:\自动剪辑\某剧\第3集"

# 之后同一素材可直接
python dy.py run
```

Windows 可直接双击 `DY.bat`（会用项目 `.venv` 跑 `dy.py run`）。

## 命令一览

| 命令 | 作用 |
|------|------|
| `dy.py run` | 一键全流程：检测 → 视觉 → 脚本表 → 成片 |
| `dy.py preflight` | 一次性红绿灯：素材/文案/Key/FFmpeg 是否就绪 |
| `dy.py detect` | 检测素材（原片/字幕/文案是否齐全） |
| `dy.py visual` | 视觉识别（默认续跑；`--force` 清空重跑） |
| `dy.py script` | 生成脚本表（对齐字幕与文案） |
| `dy.py status` | 查看当前工作流进度 |
| `dy.py concurrency` | 查看/固定渲染并发（`--set N` / `--benchmark`） |
| `dy.py config` | 打印当前配置（API Key 掩码） |
| `dy.py set --folder <路径>` | 设置素材文件夹（也可设分辨率/视觉模型） |
| `dy.py set-key --dashscope <KEY>` | 加密保存 API Key |
| `dy.py doctor` | 环境自检 |

`run` 视觉识别策略：默认复用已就绪索引；若有失败帧则自动续跑抢救；
`--resume` 强制续跑（复用已识别帧、只重试失败帧），`--force-visual` 清空重跑，
`--skip-visual` 跳过（需已有索引）。抽帧间隔默认按视频时长自适应（`--interval` 覆盖），
视觉并发默认 3（`--workers` 覆盖），渲染并发用 `--concurrency` 覆盖。

**稳健性**：视觉识别支持断点续跑（按帧缓存，只重试失败帧）与失败帧单独小批重跑；
百炼视觉/配音调用统一走指数退避重试，坏索引/损坏 JSON 自动重建。

## 素材准备

在素材文件夹里放三类文件（文件名可含「原片/字幕/解说文案」等关键字，程序会自动识别）：

1. **原片**：`*.mp4` / `*.mkv` / `*.mov`
2. **字幕**：`*.srt` / `*.ass`（与原片对应）
3. **解说文案**：`*.txt` / `*.md` / `*.docx`，用「原片：」「解说：」标签分段：

```text
原片：
这里写需要保留播放的原片台词（会在字幕里定位到时间区间）

解说：
这里写口播解说文案（会生成克隆配音，不含标签文字）
```

## 目录结构

```text
project/
  dy.py                    # ★ 统一 CLI 入口
  anchored_pipeline.py     # 成片主流程（配音→分镜→渲染）
  gpt_sovits_batch.py      # 本地 GPT-SoVITS 引擎批处理（可选配音后端）
  backend/
    runner.py              # CLI 编排器（命令构建 + 子进程 + 后处理）
    config_store.py        # 配置与加密 API Key
    media.py               # 素材检测
    drama_source_index.py  # 视觉识别 / 视觉索引
    manual_script.py       # 脚本表生成（文案↔字幕对齐）
    visual_matcher.py      # 分镜画面匹配（全局禁复用）
    ad_filter.py           # 广告禁区过滤
    qwen_voice.py          # 百炼克隆音色 / TTS
    vision_api.py          # 抽帧 + 视觉模型调用
    postprocess.py         # 分辨率 / 片头片尾留白 / 字幕重定时
    schemas.py             # 配置模型
    media_tools.py         # ffmpeg/ffprobe 定位
    concurrency.py         # 并发自适应
  config/                  # user_config.json + 加密 secrets（不入库）
  tools/ffmpeg/bin/        # 内置 ffmpeg / ffprobe
  tests/                   # 单元测试
```

## 配置与密钥

- 视觉识别与百炼配音**共用**同一个百炼 DashScope API Key。
- Key 以加密形式保存在 `config/secrets.bin`（配 `config/.secret.key`），不写入明文、不入库。
- 也可用环境变量 `DASHSCOPE_API_KEY` / `SILICONFLOW_API_KEY`。

## 测试

```bash
python -m unittest discover -s tests
```

## 安全说明

- 不要提交 API Key、本机配置、运行缓存、视频/音频/字幕素材。
- 相关文件已在 `.gitignore` 中忽略，发布公开仓库前请复查。
