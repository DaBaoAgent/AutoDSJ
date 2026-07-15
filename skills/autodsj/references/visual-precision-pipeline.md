# AutoDSJ 视觉识别精度管线（2026-07 重构 · 认清「谁·在干嘛·在哪·细节·旁边谁」）

> 本文保留人脸库、高清单帧识别和结构化 prompt 细节；抽帧调度已由 `selective_visual.py` 接管。正式链路每集只用云端复核60～120帧，`VISUAL_SCHEMA="v3-selective-face-720p"`，禁止按固定8～10秒间隔恢复约287帧全片扫描。先读 `hybrid-evidence-matching.md`。

改视觉索引识别精度（抽帧/prompt/人脸库）时看这张图。所有路径相对
`D:\@kaifa\AutoDSJ\project\`。这一层是**上游**（画面识别），
下游「解说→画面匹配/剪辑」见 `matching-and-editing-internals.md`。

**核心原则：上游看不清，下游向量/身份匹配再强也白搭（garbage in garbage out）。**
把「身份（谁）」和「内容（在干嘛/场景/细节/旁边谁）」拆开，各用最擅长的工具：
身份→本地人脸识别（别让 VL 猜），内容→VL（给它高清图 + 每次少帧 + 把已知人名喂进去）。

## 数据流

```
原片 → _extract_frames(1280×720)         vision_api.py   高清抽帧
     → _build_identity_map(每帧 ArcFace)  drama_source_index.py + face_gallery.py
         (剧集根有 _face_gallery.json 时) → identity_map {frame_id:[{role,actor,score,box,position,prominence}]}
     → _annotate_source_frames            drama_source_index.py
         · pending 帧: record["known_people"]=render_known_people(...)  注入 VL prompt
         · _call_bailian_vision(batch≤2)  vision_api.py   逐人结构化描述
         · _apply_identity(收尾)          people="演员（饰角色）" 覆盖 + caption 前置角色名
     → _source_visual_index.json          带 identified / people / caption / scene / props / people_detail
```

## 关键文件 / 改动点

- `backend/schemas.py::VisualSettings`（挂在 `AppSettings.visual`）：
  `frame_width=1280, frame_height=720, jpeg_q=3, batch=2, use_face_gallery=True,`
  `faces_dir="_faces", face_gallery_file="_face_gallery.json", face_threshold=0.38,`
  `face_min_size=46, face_det_size=640`。老 config 无 `visual` 段时 pydantic 自动补默认。
- `backend/vision_api.py`
  - `_extract_frames(..., width, height, jpeg_q)`：分辨率参数化。旧值写死 480×270（人脸仅 30-60px 认不出演员）→ 720p（人脸 150-300px）。
  - `_vision_prompt(n)`：改**逐人结构化** —— 输出 `people:[{name,speaking,position,doing}]` + `scene/action/props/emotion/shot_scale`，并声明「每帧图前给出『已知人物』(人脸识别,可信)，直接采用，『无』时只描述外观不臆造姓名」。
  - `_call_bailian_vision(batch)`：每帧图前插一行 `已知人物：<known_people or 无>`；`batch` 由 `visual.batch`（1-2）控制，旧值 8 稀释注意力。
  - `_render_people(value)`：VL 的 people 可能是结构化 list 或字符串，渲染成可读串 + 保留 `people_detail`。
- `backend/drama_source_index.py`
  - `VISUAL_SCHEMA="v3-selective-face-720p"`：写进索引 + 缓存有效性判据。索引还记录精确 `source_signature` 时间点；与 `_selective_visual_plan.json` 不一致时自动重跑。
  - `_build_identity_map(folder, records, settings)`：对每帧 `image_path` 跑 `FaceIdentifier.identify`。人脸库**全集共享**：先查单集夹 `folder/_face_gallery.json`，再回退剧集根 `folder.parent/_face_gallery.json`。无库/没装 insightface → 返回 `{}`，管线退回纯 VL 描述（不阻塞）。
  - `_apply_identity(frames, identity_map)`：把 `render_people_field` 的「演员（饰角色）」写进帧 `people`（覆盖 VL 的），存 `identified`，并把角色名前置进 caption（`【黄亦玫、庄国栋】…`）。在收尾 + 缓存命中两条路径都调，保证 identity 总是最新。
- `backend/face_gallery.py`（新）：ArcFace 建库 + 识别。见下节。
- `autodsj.py`：`dy faces build/add/list`（`_faces_locate` 默认剧集根、`--here` 单集）；`run` 打印分辨率/批量/人脸库开关；`doctor`/`status` 加人脸库自检。`visual_batch_size=settings.visual.batch`。

## 人脸库 workflow（`backend/face_gallery.py`）

- **依赖**：`insightface`+`onnxruntime`+`opencv-python-headless`+`numpy`，已装项目 `.venv`（CPU）。首次 `build`/`run` 自动下 buffalo_l 模型 ~300MB 到 `~/.insightface`。
- **目录**：`<剧集根>/_faces/<角色名>/*.jpg`（每主演 3~5 张清晰正脸）+ `_faces/roster.json`（`{角色:演员}`）。建库产物 `<剧集根>/_face_gallery.json`。
- **建库** `build_gallery`：每张图取最大脸的 512 维 `normed_embedding`（已 L2 归一化）。存 `roles:{dirname:[vec...]}` + `canonical:{dirname:规范名}` + `roster`。
- **识别** `FaceIdentifier.identify(image_path, w, h)`：检测每张脸→与各角色向量矩阵批量点积（=余弦，因已归一化）取最大→>阈值(0.38)才认，认不出**返回空绝不瞎报**。按人脸面积排序（最大=`主体`，余 `次要`），框中心 x 判 `左/居中/右`。输出 `{role(规范名),actor,score,box,area_ratio,position,prominence,det_score}`。
- **年龄变体归一（canonical）**：同角色童年/成年脸 ArcFace 向量差异大，用户会分目录（如 `方太初小时候`/`方太初长大了`）→ 识别时**分年龄段比对更准**，但对外身份经 `_canonical_role` 归一到规范角色名 `方太初`（去 `小时候/长大了/长大/少年/成年…` 后缀）。这也满足 `visual_matcher._ACTOR_ROLE_RE` 角色名 **2-5 字** 的建组正则（6字的"方太初小时候"会漏）。
- **渲染两用**：`render_known_people` → 喂 VL 的提示（角色名+位置+主次，不含演员名以免干扰描述）；`render_people_field` → 写索引 people 字段的「演员（饰角色）」（有 actor 才这样写，否则退回角色名）。

## 身份 → 匹配器闭环（零改动 visual_matcher）

`_apply_identity` 把「演员（饰角色）」写进 `people` 后，下游全自动吃到：
`visual_matcher.register_character_aliases` 的 `_ACTOR_ROLE_RE` 为每个识别到的角色建组
（{演员,角色}+昵称桥接）→ `_frame_text` 含 people → 解说点名某角色时 `_character_hits`
命中同组帧 **+0.4**、演别的主角的帧 **-0.3**。这是根治张冠李戴的最终一环。
验证：`_apply_identity` 后 `register_character_aliases(frames)`，看 `vm._CHAR_GROUPS` +
`vm._character_hits("庄国栋…")` 与 `vm._character_hits(vm._frame_text(frame))` 有无交集。

## 阈值调参（ArcFace 跨域）

参考照是影棚/剧照，抽帧是电视画面（不同光照/角度/年代）——**跨域余弦天然偏低**。
实测正确匹配落 **0.48~0.85**，空镜/侧脸/背影正确留空。默认 `face_threshold=0.38`：
误认多（张冠李戴）就调高到 0.42~0.45，漏认多就调低。侧脸/背影 ArcFace 认不出属正常，
留空交给 VL 描外观，**不硬编造**。参数在 `config/user_config.json` 的 `visual` 段。

## 踩坑（本次踩过，别踩回去）

1. **项目 `.venv` 无 pip** → 装依赖用 `uv pip install --python .venv/Scripts/python.exe <pkg>`（uv 走预编译 wheel，Windows CPU 装 insightface 0.7.3 无需 C 编译器）。
2. **含中文路径读图** 用 `cv2.imdecode(np.fromfile(str(path),np.uint8), cv2.IMREAD_COLOR)`，**别用 `cv2.imread`**（中文路径返回 None）。已封装在 `face_gallery._read_image`。
3. **ffmpeg 抽帧报 `Non full-range YUV is non-standard`** → filter 末尾加 `,format=yuv420p`。
4. **ffmpeg 是原生 Windows 程序，不认 MSYS `/tmp` 路径** → 输出/输入用原生路径或 `tempfile.gettempdir()`。
5. **定位真源片别用 `ls *.mkv|head`** → 会抓到 `_anchored_muxed.mp4` 等渲染中间产物；用 `detect_materials(folder,1).video_path`。
6. **人脸库/身份是可选增强**：没建库/没装 insightface 也能跑（退回高清+结构化 VL 描述，已比旧版清楚很多）。别把「缺库」当报错。
7. **进度过程中帧无 `identified`** 是正常的：`_apply_identity` 在收尾统一覆盖；别以为身份注入没生效。看已完成帧的 `caption/people` 里有没有真名即可判断 VL 注入是否工作。
8. **后台 `dy run` 输出被 grep 管道缓冲** → 监控进度读 `_source_visual_index.json` 的 `status/progress/success_count`，别等 stdout。

## 换新剧建库

`_faces/<角色>/*.jpg`（每人 3~5 张正脸，最佳来源=从本剧截图，同妆造识别率最高）+
`roster.json`（角色→演员）→ `dy faces build --folder <任意一集>`（默认建到剧集根全集共享）。
`dy faces add <角色> <图...> --actor <演员>` 命令行加图并写 roster。

## 验证 / 复跑

- `dy faces list` 看库里各角色几条向量。
- 抽检识别：抽几帧跑 `FaceIdentifier.identify`，看认出的人与剧情是否吻合、分数有无区分度。
- 审匹配（先不渲染）：`dy run --force-visual --no-render` → 重建 720p+人脸索引+匹配+写 `★匹配报告.json`；核对 `visual_match_evidence` 里解说点名角色是否命中该角色帧、张冠李戴是否消除；OK 再去掉 `--no-render` 正式成片（`--skip-visual` 复用索引）。
- **量化命中率**：`scripts/analyze_match_identity.py "<素材夹>"`（复用 `visual_matcher` 的角色建组+命中判定，口径与匹配一致）→ 输出「点名角色分镜的 命中/张冠李戴/无标注」比例、置信度分布、身份注入帧覆盖率、疑似张冠李戴逐条。每次重匹配后跑它量化对比，别靠肉眼翻报告。

## 剩余张冠李戴排查：身份权重 vs 场景锁定（本会话实测结论）

建完人脸库、身份注入生效后，`★匹配报告.json` 里若**仍有**「解说点名A、画面是B」：

- **别先调身份权重**。本会话实测：把 `visual_matcher.allocate._scan` 里点名身份加分从 ±0.4/0.3 提到 ±0.5/0.5，命中率**零变化**。根因：`_scan(scene_ranges)` 的 scene-lock 是**硬约束**——候选帧被限制在该解说被分类到的场景 ranges 内，若那个场景**没有**正确角色的帧，身份加分/扣分再重也拉不进范围外的正确帧。身份权重只对「非 scene-locked 且正确帧在扫描范围内」的少数分镜有效。
- **真正的杠杆是场景分类**。这类残余错几乎全是 **C 级弱匹配**，根因是 `_scene_map` 把该解说锁进了含错误角色的场景段（如「玫瑰首次会议」被分到含白晓荷的食堂/聚餐段）。
- **排查步骤**：① `scripts/analyze_match_identity.py` 列出疑似张冠李戴 → ② 对每个错例，看它被锁到哪个场景 + 该场景 ranges 内/附近有没有正确角色的帧（有→极少数 embedding 盖过身份，可微调；无→场景分类错）→ ③ 场景分类错就给 `_scene_map.json` 加 `overrides`（`{"contains":解说独特短语,"scene":正确场景名}`）逐句钉（见 `scene-segment-matching.md`），`run --skip-visual --no-render` 重匹配复测。
- 若确要动身份权重，改完记得回退——避免为个别分镜牺牲全局匹配基线的稳定。
