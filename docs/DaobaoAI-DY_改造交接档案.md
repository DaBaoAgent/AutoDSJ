# DaobaoAI-DY 最新管线说明

当前项目只有一条正式管线：上传原片、SRT 和手写“原片/解说”文案后成片。

- `backend/manual_script.py`：解析文案并逐行定位原片对白；相邻命中合并，隔着未指定对白的命中保持为独立小片段，禁止将首句到末句扩成连续长片段。
- `backend/drama_source_index.py`：抽帧并调用 SiliconFlow 视觉模型建立视觉索引。
- `backend/visual_matcher.py`：按人物、动作、地点和上下文为每个解说短句选镜，并全局禁止镜头复用。
- `backend/ad_filter.py`：综合视觉识别中的广告/品牌植入证据和 SRT 商业话术生成全局广告禁区；原片对白与解说画面都禁止进入这些区间。
- `anchored_pipeline.py`：每个完整解说段只调用一次 GPT-SoVITS；完成整段音频处理后，按自然停顿映射到语义分镜，再严格按文案顺序渲染。
- `gpt_sovits_batch.py`：试听和正式成片统一自动使用 CUDA，固定 `seed=20260711`、`cut0` 和 1.1 倍速，保证同一音色配置可复现。
- `backend/postprocess.py`：应用分辨率、CRF、编码预设和片头片尾留白，并同步修正 SRT 与匹配报告时间。

配音架构禁止回退为“每个视觉短句单独调用一次 GPT-SoVITS”。视觉短句只允许切整段成品音频的片内区间，否则会造成音色、气息和情绪漂移。

已删除纪录片、自动生文案、样片学习、封面、BGM、字幕烧录和发布信息管线。
