# AutoDSJ — 大宝全自动智能电视剧解说剪辑神器

> 把原片、字幕和解说文案放进去，让 AI 自动找准剧情画面、克隆配音、剪辑成片并生成发布资料。
>
> Turn raw drama footage, subtitles, and a recap script into a scene-accurate narrated video and a ready-to-publish delivery package.

[![CI](https://github.com/DaBaoAgent/AutoDSJ/actions/workflows/ci.yml/badge.svg)](https://github.com/DaBaoAgent/AutoDSJ/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/DaBaoAgent/AutoDSJ)](https://github.com/DaBaoAgent/AutoDSJ/releases)
[![License](https://img.shields.io/github/license/DaBaoAgent/AutoDSJ)](LICENSE)
[![Docker](https://img.shields.io/badge/GHCR-AutoDSJ-2496ED?logo=docker&logoColor=white)](https://github.com/DaBaoAgent/AutoDSJ/pkgs/container/autodsj)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Claude%20Code%20%7C%20Codex%20%7C%20Hermes%20%7C%20OpenClaw%20%7C%20OpenCode-6B4EFF)](skills/autodsj/SKILL.md)

## 60 秒成品演示 / 60-Second Demo

<video src="https://github.com/DaBaoAgent/AutoDSJ/releases/download/v0.2.1/autodsj.mp4" controls width="100%"></video>

[▶️ 播放或下载 AutoDSJ 60 秒成品演示 / Watch or download the 60-second demo](https://github.com/DaBaoAgent/AutoDSJ/releases/download/v0.2.1/autodsj.mp4)

## 输入和输出对比 / Input → Output

| 输入 / Input | AutoDSJ | 输出 / Output |
|---|---|---|
| 电视剧或短剧原片 | 完整场景约束 + 字幕/剧本检索 + 选择性视觉复核 | `★ 成片.mp4` |
| SRT/ASS 原片字幕 | 人物、事件、动作和镜头全局匹配 | `★ 字幕.srt`、匹配报告 |
| TXT/MD/DOCX 解说文案 | Qwen 克隆配音、响度归一化、自动渲染 | 发布信息、剪映字幕、交付清单 |

## 一键安装 / One-Command Install

安装同名 `AutoDSJ` Skill 到 Claude Code、Codex、Hermes、OpenClaw、OpenCode 和通用 Agent 目录：

```powershell
git clone https://github.com/DaBaoAgent/AutoDSJ.git; Set-Location AutoDSJ; python scripts\install_autodsj_skill.py --agent all
```

Docker 一键自检 / One-command Docker check：

```powershell
.\scripts\docker-doctor.ps1
```

> ⭐ 如果 AutoDSJ 帮你少剪一条时间线，请点一个 **Star**。你的 Star 会让更多创作者搜到它。
>
> ⭐ If AutoDSJ saves you one manual timeline edit, please **Star this repository** so more creators can discover it.

## 核心能力 / Core Features

- **剧情级精准匹配 / Scene-accurate matching**：先锁定完整大场景，再在事件、物理镜头和动作瞬间中选画面，减少跨场景乱跳。
- **混合证据检索 / Hybrid retrieval**：融合字幕、审校剧本、BM25、文本向量、人脸、可选声纹及云端视觉证据。
- **全局连续解码 / Global sequence decoding**：按整段解说规划镜头顺序，而不是逐句贪心搜索。
- **节省视觉调用 / Selective vision review**：按风险选择 60–240 帧进行云端复核，无需固定间隔扫描整集。
- **克隆配音与自动渲染 / Voice cloning and rendering**：使用 Qwen 克隆音色，统一到 -16 LUFS，并保留所需原片对白。
- **完整交付 / Publishing deliverables**：自动生成成片、字幕、匹配报告、发布文案、剪映字幕和交付清单。
- **跨 Agent Skill / Reusable Agent Skill**：一份 `skills/autodsj` 同步给 Claude Code、Codex、Hermes、OpenClaw、OpenCode 等 Agent。
- **Docker 与 GitHub 自动发布 / Docker and GitHub releases**：每次推送验证项目与 Skill；版本标签同步发布 Skill 包和 GHCR 镜像。

## 30 秒快速开始 / 30-Second Quick Start

### Docker

```powershell
git clone https://github.com/DaBaoAgent/AutoDSJ.git
Set-Location AutoDSJ
.\scripts\docker-doctor.ps1
```

把单集素材放到 `data/episode-01/`，然后执行：

```powershell
docker compose run --rm autodsj prepare --folder /data/episode-01
docker compose run --rm autodsj run --folder /data/episode-01 --hierarchical-match
```

### 本地 Python / Local Python

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe autodsj.py doctor
```

Python 3.11+ 和 FFmpeg 为必需项。首次正式成片前需配置 API Key，并人工复核 `_scene_map.json`。

Python 3.11+ and FFmpeg are required. Configure API keys and review `_scene_map.json` before the first production render.

## Claude Code 使用方法 / Using with Claude Code

```powershell
python scripts\install_autodsj_skill.py --agent claude
python scripts\install_autodsj_skill.py --agent claude --check
```

安装位置：`~/.claude/skills/autodsj/SKILL.md`。在 Claude Code 中输入 `/autodsj`，或直接说：

> 用 AutoDSJ 处理 `D:\素材\第01集`，先检查素材和文案，再准备场景地图。

The Skill is installed at `~/.claude/skills/autodsj/SKILL.md`. Invoke `/autodsj` or ask Claude to prepare and edit an episode with AutoDSJ.

## Codex 使用方法 / Using with Codex

```powershell
python scripts\install_autodsj_skill.py --agent codex
python scripts\install_autodsj_skill.py --agent codex --check
```

安装位置：`$CODEX_HOME/skills/autodsj`，未设置 `CODEX_HOME` 时使用 `~/.codex/skills/autodsj`。新任务中输入：

> 使用 `$autodsj` 检查这一集的素材、场景地图和正式渲染门禁。

The installer respects `$CODEX_HOME` and falls back to `~/.codex/skills/autodsj`.

## Hermes 使用方法 / Using with Hermes

```powershell
python scripts\install_autodsj_skill.py --agent hermes
python scripts\install_autodsj_skill.py --agent hermes --check
```

安装位置：`~/.hermes/skills/autodsj`。启动 Hermes 后使用 `/autodsj`，或让 Hermes 用 AutoDSJ 运行指定剧集。

The Skill is installed at `~/.hermes/skills/autodsj` and can be selected with `/autodsj`.

## 其他 AI 编程软件和 Agent 使用方法 / Other AI Coding Agents

```powershell
python scripts\install_autodsj_skill.py --agent openclaw
python scripts\install_autodsj_skill.py --agent opencode
python scripts\install_autodsj_skill.py --agent shared
```

| 软件 / Agent | 默认位置 / Default location | 调用方式 / Invocation |
|---|---|---|
| OpenClaw | `~/.openclaw/skills/autodsj` | 让 Agent 使用 `autodsj` Skill |
| OpenCode | `~/.config/opencode/skills/autodsj`；Windows 为 `%APPDATA%\opencode\skills\autodsj` | `skill({ name: "autodsj" })` 或自然语言触发 |
| 通用 Agent Skills | `~/.agents/skills/autodsj` | 支持 Agent Skills 标准的软件自动发现 |

所有平台共用同一份 [SKILL.md](skills/autodsj/SKILL.md)。内部标识按标准使用小写 `autodsj`，展示名称统一为 **AutoDSJ**。

All platforms use the same canonical Skill. The standards-compliant identifier is `autodsj`; the human-facing name is **AutoDSJ**.

## 普通命令行使用方法 / Command-Line Usage

```powershell
# 环境检查 / Environment check
.\.venv\Scripts\python.exe autodsj.py doctor

# 检测素材 / Detect inputs
.\.venv\Scripts\python.exe autodsj.py preflight --folder "D:\素材\第01集"

# 建索引、场景草案和视觉复核计划 / Prepare indexes and scene draft
.\.venv\Scripts\python.exe autodsj.py prepare --folder "D:\素材\第01集"

# 影子匹配预演 / Preview hierarchical matching
.\.venv\Scripts\python.exe autodsj.py shadow-match --folder "D:\素材\第01集"

# 正式渲染 / Production render
.\.venv\Scripts\python.exe autodsj.py run --folder "D:\素材\第01集" --hierarchical-match

# 查看进度 / Inspect status
.\.venv\Scripts\python.exe autodsj.py status --folder "D:\素材\第01集"
```

正式渲染要求完整且人工复核过的 `_scene_map.json`。详细执行规则在 [AutoDSJ Skill](skills/autodsj/SKILL.md) 中维护。

A reviewed `_scene_map.json` is mandatory for production rendering. The canonical workflow lives in the AutoDSJ Skill.

## 效果案例 / Example Results

| 场景 / Scenario | 输入 / Input | 自动产物 / Generated result |
|---|---|---|
| 单集电视剧解说 | 原片 + 字幕 + 解说文案 | 配音成片、字幕、匹配报告、发布信息 |
| 画面匹配纠错 | 已有场景图、索引和问题文案 | 分层影子报告、新旧候选对比、修正后的成片 |
| 多集连续制作 | 每集独立素材目录 | 可复用索引、按集交付包、统一发布资料 |

公开演示素材和输入/输出截图将在 60 秒演示中补充。使用者必须确保拥有输入视频、字幕、声音和发布内容的合法授权。

Public demo assets and before/after screenshots will be added with the 60-second demo. Users are responsible for rights to all footage, subtitles, voices, and published content.

## 配置说明 / Configuration

本地配置写入 `config/user_config.json`；API Key 加密保存在本地，且已被 Git 忽略：

```powershell
.\.venv\Scripts\python.exe autodsj.py set-key --dashscope "YOUR_DASHSCOPE_KEY"
.\.venv\Scripts\python.exe autodsj.py set-key --siliconflow "YOUR_SILICONFLOW_KEY"
.\.venv\Scripts\python.exe autodsj.py set --resolution 1080P
.\.venv\Scripts\python.exe autodsj.py config
```

| 配置 / Setting | 默认值 / Default | 说明 / Notes |
|---|---:|---|
| `voice.provider` | `qwen` | 百炼 Qwen 克隆音色 / Qwen voice cloning |
| `voice.volume` | `100` | 配音纯增益；渲染时再做响度归一化 |
| `drama.source_play_volume` | `100` | 原片对白纯增益 |
| `visual.selective_min_frames` | `60` | 单集选择性视觉复核下限 |
| `visual.selective_max_frames` | `240` | 单集选择性视觉复核上限 |
| 输出响度 / Loudness | `-16 LUFS` | 配音和原片分别归一化 |

不要提交 `.env`、`config/user_config.json`、密钥、原片、声音样本或生成视频。

Never commit `.env`, `config/user_config.json`, API keys, source footage, voice samples, or generated videos.

## 路线图 / Roadmap

- [x] 完整场景地图与正式渲染门禁 / Reviewed scene-map gate
- [x] 字幕、剧本、向量、人脸和可选声纹混合检索 / Hybrid evidence retrieval
- [x] 父段全局序列解码与选择性视觉复核 / Global decoding and selective vision review
- [x] Claude Code、Codex、Hermes、OpenClaw、OpenCode 同名 Skill / Cross-agent Skill
- [x] Docker Compose、GitHub Actions、Release 和 GHCR / Automated packaging and releases
- [ ] 60 秒公开演示视频与可复现实例 / Public 60-second demo and reproducible sample
- [ ] Linux/macOS 端到端实测与安装脚本 / End-to-end Linux and macOS validation
- [ ] 更多影视类型、语言和配音提供商 / More genres, languages, and voice providers

欢迎通过 Issue 提交需求，通过 Pull Request 认领路线图任务。

Open an Issue for feature requests or a Pull Request to help complete the roadmap.

## 贡献指南 / Contributing

项目由 **Dabao** 发起并维护。社区贡献请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。

AutoDSJ was created and is maintained by **Dabao**. Read the contribution and security guidelines before opening a pull request.

每次修改 `skills/autodsj` 后必须验证并同步安装副本：

```powershell
python -m unittest discover -s tests -v
python scripts\install_autodsj_skill.py --agent all
python scripts\install_autodsj_skill.py --agent all --check
```

Repository topics / 搜索关键词：`ai-video-editing`, `drama-recap`, `automatic-video-editing`, `agent-skill`, `claude-code`, `codex`, `hermes-agent`, `openclaw`, `opencode`, `docker`, `ffmpeg`, `qwen`.

## License

[MIT License](LICENSE) © 2026 **Dabao**.

如果你在自己的项目、教程或视频中使用 AutoDSJ，欢迎保留项目链接并告诉我们你的作品。

If you use AutoDSJ in a project, tutorial, or video, attribution and a link back to this repository are appreciated.
