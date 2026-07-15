# AutoDSJ 匹配与剪辑内部机制（调试地图）

> 当前正式匹配主路径已改为 `text_retriever.py → voice_index.py（可选）→ sequence_decoder.py → selective_visual.py`。下文 `visual_matcher.py` 的“全片单次扫描/帧向量”仅用于理解旧报告和诊断，不得重新接管正式成片。先读 `hybrid-evidence-matching.md`。

改「解说→画面匹配」或「原片剪辑」时，先看这张图定位到具体文件/函数，别从头摸。
所有路径相对 `D:\@kaifa\AutoDSJ\project\`。

## 数据流

```
文案(原片:/解说:) + 字幕SRT + 视觉索引
  → backend/manual_script.py   生成 _drama_script_table.json（原片对白定位 + 解说场景锚定）
  → anchored_pipeline.py       TTS → 分镜 → 视觉分配 → 剪辑成片
      backend/visual_matcher.py  解说画面语义/人物匹配
```

## 关键文件 / 函数

- `backend/manual_script.py`
  - `match_source_block(block, subtitles, min_start)`：把一段 `原片:` 对白定位到字幕时间区间。
    **`min_start` 是软偏好不是硬门**（两处循环各加 `score -= 0.08` 惩罚，不再 `continue` 硬排除更早字幕）。
    原因：文案里 `原片:` 台词常非时序；硬排除会导致一段跑偏后，后续全部 fallback 到末尾同一条字幕 → 区间塌缩重叠 → 渲染报「素材区间重复/命中广告禁区」。
  - `generate_manual_script_table(...)` 末尾的解说循环：把每段 `解说:` 的 `source_start/source_end` **锚定到相邻 `原片:` 所在剧情场景**（`anchor_start-8` 到 `anchor_end+80`，别用"两原片间隙"——非时序会倒挂），供下游作为画面搜索首选窗口。

- `backend/visual_matcher.py`
  - `register_character_aliases(records)`：从视觉索引 `people/caption` 的正则 `演员（饰演角色）` **数据驱动**建角色别名组（每组 = {演员, 角色, 昵称}）。`load_visual_frames` 里自动调用，换剧自适应。**兜底补组（2026-07）**：某角色本集只被索引直标角色名、没有「演员（饰演角色）」标注时（如第4集的**庄国栋、苏更生**），只要其本名在 `_NICKNAME_BRIDGE` 的 value 里且在帧文本出现过，也为它建组，让昵称桥接与人物加分仍生效——否则这类主角/配角拿不到 `_character_hits` 命中，人物加减分对他们失效。判断某集哪些角色成组：`for i,g in enumerate(vm._CHAR_GROUPS): print(i, sorted(g))`。
  - `dominant_character_group(narration_texts)` + `VisualIntervalAllocator(protagonist_group=)`：**隐含主角回退**（2026-07）。统计全部解说文字里出现最多的角色组=该剧主角（数据驱动，非硬编码），`allocate_visual_all` 算出后传给分配器。当某条解说**不点名任何角色**（纯代词/无主语，如「但她也不是…」），`allocate()` 用主角组当隐含主体，给主角画面**温和 +0.2**、别的点名主角 **-0.15**（比显式点名的 +0.4/-0.3 弱，语义仍能压过）。效果：无点名解说优先出现主角（玫瑰）而非随便一帧。日志「隐含主角回退：无点名解说默认偏向角色组 #N」。局限：主角最佳帧被前面镜头预留抢占时，该条会退到语义次优（个别残留可接受）。
  - `_NICKNAME_BRIDGE`：文案昵称→剧名的手工桥接（如 `玫瑰→黄亦玫`）。换剧若解说用昵称而索引用本名，在这加一条。
  - `_expanded_query(text)`：查询词扩展，叠加静态 `_CONCEPT_ALIASES` + 动态角色别名。
  - `VisualIntervalAllocator.allocate(...)` 打分（**2026-07 重构：全片单次扫描，语义为主，锚定窗为辅**）：对**整条可用时间轴**逐候选窗打分 = 语义分（有 DashScope key 用 embedding 余弦，否则 `_semantic_score` n-gram）+ **人物身份加减分**（`_character_hits` 命中同一角色组 **+0.4**，画面出现别的主角 **-0.3**）+ **就近微调**（落在锚定首选窗内 +0.1，窗外按距离 150s 线性衰减到 0）+ 时序小 bonus(+0.04)。取全局最高分。**关键：锚定窗只是弱 tiebreaker，不再是硬约束。** 旧设计是 [首选窗/±45s/全片] 三档 + 命中首选档就 break，等于把每个解说镜头锁死在相邻`原片`的位置——解说讲的角色/场景若在别处（会议在713-1109、姜雪琼在299-546），画面永远够不着，只能抓窗口内随便一帧（打字手/食堂路人）。改成全片语义主导后「说到谁就出现谁、说到什么场景就出现什么场景」才成立（第4集实测：会议/姜雪琼/项目策划书/庄国栋会后 全部命中，分1.0）。`_window_evidence` 已加 bisect（帧按时间排序，`self._frame_times`）加速全片扫描。性能：93解说镜头全片分配 ~3-4 分钟，可接受（同帧余弦跨重叠窗有重复计算，若日后嫌慢可按 query 预算一次 time→cosine 缓存）。
  改原片预留逻辑时勿退回 `reserve`（会把有意重复引用当错误崩）。

  - `VisualIntervalAllocator.allocate(..., scene_ranges=)` + `anchored_pipeline.py::_load_scene_map/_classify_scene`：**场景段硬锁定（大镜头匹配，2026-07，凌驾于全片语义之上）**。素材夹放 `_scene_map.json`（每场景=`name`+`ranges`原片秒段列表+`keywords`强/`characters`弱）。`allocate_visual_all` 加载后，逐解说镜头用**父句全文**（按 `tts_parent_id` 把同句各分镜文字拼回，避免单个 clause 漏掉兄弟 clause 的关键词）经 `_classify_scene` 打分（keyword×2+character×1，需≥1关键词命中）分类到场景，命中就把 `scene_ranges` 传给 `allocate()`。`allocate()` 内 `_scan(restrict)`：restrict 给了就只在场景 ranges 内选候选窗（语义/人物照常打分，但**不加就近微调**——场景内纯语义决定）；场景内无空闲才 `_scan(None)` 回退全片（永不崩）。日志「[场景锁定] 行N-镜M → 「场景名」」。**建/调场景图**：先通读 `_source_visual_index.json` 时间轴（time+people+caption）识别各场景原片秒段。**坑**：① keyword 别太宽（"项目组"会误锁"正式进入项目组"进会议）；② 场景太短会被同类多条解说占满→回退全片抓烂帧，把同类镜头的多段 ranges 并进一个场景防耗尽（如"会议"并入全部会议室镜头 5 段）；③ 硬锁定牺牲原始余弦均分（0.8→0.6级）换场景正确，是用户要的取舍。\n\n  - **`--no-render`（只匹配不成片，审匹配用）**：`autodsj.py run --skip-visual --no-render` → `runner.render(no_render=True)` → `anchored_pipeline.py --no-render`。main() 在 `allocate_visual_all`+`build_timeline` 后、`render_video` 前 `write_outputs`（写 `★匹配报告.json`/`★字幕.srt`）就 return，**不编码视频**。用于改 `_scene_map.json`/匹配逻辑后快速看落点对不对（`★匹配报告.json` 的 `clip_start` 是否落进目标场景 ranges），OK 再去掉 `--no-render` 正式成片。改动落在 `autodsj.py`(cmd_run+argparse)、`backend/runner.py`(build_pipeline_command+render 的 no_render)、`anchored_pipeline.py`(main 的 `--no-render` 早退)。

  - `anchored_pipeline.py`
  - `NarrationSegment.keep_ranges`：原片片段剪停顿后要保留的绝对时间子区间列表（空=整段）。
  - `_speech_keep_ranges(subtitles, clip_start, clip_end, max_pause=1.0, pad=0.15)`：**字幕驱动**的停顿检测。字幕间空档 >max_pause 就切开（跳切）；带 pad 防切到话头话尾。**别改回 silencedetect**（BGM 场景失效）。
  - `trim_source_clip_pauses(source, source_clips, subtitles, max_pause)`：在 `main()` 里于 `build_timeline` **之前**调用，改 `audio_duration=保留总时长` 以保持时间轴/字幕同步。
  - `render_video._cut_clip`：source_clip 分支按 `keep_ranges` 逐段切片，多段则 concat（`-c copy`）；单段直接切。
  - `build_timeline`：按 `audio_duration` 累加算 `output_start/end` 和字幕时间——所以剪停顿必须在它之前改 `audio_duration`。
  - `MIN_SHOT_SECONDS`（=0.5）+ `_merge_short_shots(clauses, ranges)`：**单个画面 >0.5s 硬约束**。`expand_narration_visual_shots` 拆完分镜后调用，把不足 0.5s 的相邻分镜累加合并、末尾余量并回上一镜；`_speech_keep_ranges` 里对原片跳切子片段同样兜底（过短片段用相邻停顿空间补足到 0.5s）。改分镜/停顿逻辑时别绕过它。

  - `_semantic_score(query, evidence)`：**分母用原始文案词（`_terms(x, expand=False)`），分子用扩展词匹配（`expand=True`）**。这样扩 `_CONCEPT_ALIASES` 只加分不稀释——早期版本分子分母都用扩展词，别名越多分母越大、分越被稀释到 0，所有帧塌到地板分 0.32（=纯窗口 bonus，语义≈0）。`_CONCEPT_ALIASES` 已从旧剧专用词表扩成通用「场景/动作/景别→视觉同义词」大表（进入/活动现场/喝多/送医/预订…）。

## n-gram 的天花板 & embedding 语义匹配（已原生集成）

n-gram/别名匹配有硬上限：解说是**故事概括**（「设法进入活动现场」「玫瑰见到滕先生」），视觉 caption 是**字面描述**（「女子站在冰箱旁」「车内两人说话」），两者常一个共同字都没有——纯字面重叠为 0，落到地板分。跨语义鸿沟只能靠向量语义匹配，**已内置进主流程**：

- `backend/embed_match.py`：DashScope **text-embedding-v4** 封装。`embed_texts()` 分批 + **6 并发**（`ThreadPoolExecutor`，结果按输入序拼回）；`frame_embeddings(folder, frame_texts, key)` 把帧向量缓存到素材夹 `_frame_embeddings.json`（签名=帧文本 sha1，索引变了自动失效）——**一次计算永久复用**，重渲染走缓存。踩过的坑（都已处理，别踩回去）：① **批量必须 ≤10 条**（25 条报 400 Bad Request）；② Key 在加密的 `config/secrets.bin` → `read_secrets()['dashscope_api_key']`，只读明文 `.env` 会 401；③ Windows 需 `_force_ipv4()`，否则 IPv6 路由导致 SSL 握手超时；④ 串行太慢，必须并发。
- `frame_embeddings` 缓存签名基于**帧 evidence 文本**——预构建缓存脚本务必用 `load_visual_frames(folder)` 取 `f.evidence`（和主流程同源），否则字段拼法不同→签名不符→缓存不命中白算一遍。
- `anchored_pipeline.py::allocate_visual_all`：有 DashScope key 且有解说段时，自动嵌入全部帧（缓存）+ 全部解说分镜文字，把 `frame_vecs`（time→向量）传给 `VisualIntervalAllocator`，逐段传 `query_vec`。**无 key 或异常时自动回退 n-gram**，不阻塞成片。
- `VisualIntervalAllocator.allocate(..., query_vec=)`：窗口打分时，若有 `query_vec`+`frame_vecs`，语义分 = 窗口内各帧向量与 query 的**最大余弦**；否则用 `_semantic_score`（n-gram）。人物加减分/时序/首选窗口 bonus 照旧叠加。
- 实测（第3集6个错配点）：余弦 0.42–0.59，全部命中正确场景（「见到滕先生」精准命中字幕含"滕先生"的帧）；n-gram 版全是 0.32 地板分（窗口内乱选）。
- **迭代姿势**：改匹配逻辑后 `dy run --folder "<素材>" --skip-visual`（视觉索引+配音复用，帧向量首轮构建后也复用，只重切画面）。
- **预建帧缓存脚本**：`scripts/build_frame_embeddings.py "<素材夹>"`（项目 venv 运行，项目根已硬编码在脚本里）——提前构建 `_frame_embeddings.json`，之后 `--skip-visual` 重渲染秒级，且能先确认缓存建好再渲染。
- **实测耗时（47分钟原片，第3集）**：帧向量首轮构建 ~4 分钟（286 帧，6 并发；未并发时 15-20 分钟）；解说分镜嵌入每次渲染都要 ~35 秒（~75 条）；余弦打分 <1 秒。合计——**新集首次出片 +4~5 分钟**（帧向量+分镜嵌入），**同集重渲染仅 +~35 秒**（帧向量走缓存）。相对视觉识别(15-20分钟,也一次性缓存)开销很小。

## 复跑姿势

视觉索引已缓存（`_source_visual_index.json`）时，改脚本表/匹配/剪辑逻辑后：
```
autodsj.py script --folder "<素材>"            # 若改了脚本表生成逻辑，先重生成
autodsj.py run --folder "<素材>" --skip-visual  # 跳过视觉重扫（很慢），直接 TTS→分配→渲染
```

## 校验

- `python scripts/check_source_clips.py "<素材文件夹>"`：检查 source_clip 区间有无重叠/塌缩。
- `★ 匹配报告.json` → `segments[].visual_match_evidence` / `match_confidence`：逐镜看解说匹配到的画面证据与置信度（A/B/C/S）。
- 成片时长应 ≈ Σ(解说配音时长 + 剪停顿后原片时长) + 片头尾留白；字幕末条时间应接近成片时长。

## 音量 / 响度归一化（2026-07，等响硬规范）

- **需求**：配音与原片"正常听音量一致"。纯增益(`voice.volume`/`drama.source_play_volume`)做不到——克隆音色与原片对白的原始 LUFS 差距很大。
- **正确实现**：两个纯增益都固定 100%（unity），再由 `anchored_pipeline.py::render_video` 的 `loudnorm="loudnorm=I=-16:TP=-1.5:LRA=11"` 分别归一化：① 原片分支 `_cut_range` 用 `-af f"{loudnorm},volume={source_volume}"`，其中 `source_volume` 必须为 1.0；② 配音纯语音分支用 `-af loudnorm`；③ 配音+背景混音分支用 `[1:a:0]{loudnorm}[voice]`。不得在 loudnorm 后把原片乘 0.5，否则会再次降低约 6 dB。
- **验证**：`ffmpeg -ss A -to B -i "★ 成片.mp4" -af loudnorm=print_format=json -f null -` 读 `input_i`，抽一段原片段和几段配音段比，应都 ≈ -16。
- **坑（改音量必重跑全链）**：`voice.volume>100` 走**增益+限幅后的 WAV**，`≤100` 走**裸 WAV**——两者响度不同，导致 `_speech_keep_ranges` 停顿检测结果不同→分镜切分不同→每镜 `audio_duration` 不同。所以只要改了 `voice.volume` 跨越 100 边界，**必须重跑** `run --no-render`(产新 ★匹配报告基线)→`shadow-match`(新接管预演)→`run --hierarchical-match`，否则接管门禁报"配音时长不一致：分镜 N"。loudnorm 本身只在渲染层、不改时长，单独加它不用重跑匹配。

## `_scene_map.json` overrides（把某句解说精确钉到源区间）

- 用途：某解说分镜需要精确画面(如"两个男人聊篮球"要**两人同框**)，但同场景内锚定/语义选到了单人镜。
- 写法：`"overrides":[{"contains":"解说独特短语","scene":"场景名","range":[起,止]}]`。`contains` 子串匹配该分镜文案；`range` 是源片秒区间(要 ≥ 该镜 audio_duration，且整段确认是想要的画面)。命中后 `hierarchical_matcher` 给该镜插入高分 manual_override 候选，但仍先走全局未用画面；只有严格候选无解时才允许复用已引用原片，且永远不能命中广告或已分配解说镜头。
- **关键坑**：override 的 `scene` **必须等于**该段 `parent_scene_plans` 里的场景名。因为 `_scene_hint` 里 `hint = planned_hint or text_hint`——planned_hint(父段计划)优先，只有当 `text_hint.name == planned_hint.name` 时才把 `manual_range` 合并进去(hierarchical_matcher.py:162-164)。名字不一致 → override 被无视。
- 找两人/多人同框区间：读 `_source_shot_index.json`，筛 `nearest_visual_frames[].people` 同时含目标角色的物理镜头，取连续无空档的一段做 range。改完 `run --skip-visual --no-render --hierarchical-match` 复测，用人脸库对成片抽帧确认同框。
