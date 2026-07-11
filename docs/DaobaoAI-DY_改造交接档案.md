# DY 工作流 · 管线说明（纯后端）

本项目已从「FastAPI + React WebUI」精简为**纯后端命令行工作流**，
统一入口是 `dy.py`，由 Hermes 智能体驱动（说「DY」即触发）。

## 唯一正式管线

素材文件夹（原片 + SRT/ASS + 手写「原片/解说」文案）
→ 检测 → 视觉识别 → 脚本表 → 配音 + 剪辑 + 后处理 → `★ 成片.mp4`

## 模块职责

- `dy.py`：CLI 入口。子命令 run / detect / visual / script / status / config / set / set-key / doctor。
- `backend/runner.py`：把配置翻译成 `anchored_pipeline.py` 命令，流式跑子进程，再做后处理。
- `backend/manual_script.py`：解析文案并逐行定位原片对白；相邻命中合并，隔着未指定对白的命中保持独立小片段，禁止把首句到末句扩成连续长片段。
- `backend/drama_source_index.py`：抽帧并调用百炼视觉模型建立视觉索引（抽帧图片走临时目录，自动清理）。
- `backend/visual_matcher.py`：按人物、动作、地点和上下文为每个解说短句选镜，全局禁止镜头复用。
- `backend/ad_filter.py`：综合视觉广告证据与 SRT 商业话术生成全局广告禁区；原片对白与解说画面都禁止进入这些区间。
- `anchored_pipeline.py`：每个完整解说段只调用一次配音；整段音频完成后按自然停顿映射到语义分镜，再严格按文案顺序渲染。
- `gpt_sovits_batch.py`：可选本地 GPT-SoVITS 后端，固定 `seed=20260711`、`cut0`，同配置可复现（运行在引擎自带 python 环境）。
- `backend/postprocess.py`：应用分辨率、CRF、编码预设与片头片尾留白，并同步修正 SRT 与匹配报告时间。

## 关键约束

- 配音架构禁止回退为「每个视觉短句单独调用一次配音」。视觉短句只允许切整段成品音频的片内区间，否则会造成音色/气息/情绪漂移。
- 渲染前必须有可用视觉索引（`_source_visual_index.json`）与脚本表（`_drama_script_table.json`）。

## 与旧版差异（本次改造）

- 删除：`app.py`（FastAPI）、`launch_dabaoai.py`、`backend/jobs.py`（WebSocket 任务管理）、整个 `frontend/`、Web 启动 bat。
- 新增：`dy.py`（CLI）、`backend/runner.py`（精简编排器）。
- 依赖瘦身：移除 fastapi / uvicorn / python-multipart / psutil / numpy（主环境）。
- schema 去冗余：移除 UiSettings、target_minutes、JobInfo、JobCreate。
- 环境重建：原 `.venv` 指向别的机器已失效，改用本机 Python 3.12 重建。

已删除（更早历史）：纪录片、自动生文案、样片学习、封面、BGM、字幕烧录和发布信息管线。
