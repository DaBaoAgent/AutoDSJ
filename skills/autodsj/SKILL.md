---
name: autodsj
description: Windows 上的 AutoDSJ 电视剧/短剧全自动解说剪辑工作流。用户说“AutoDSJ”“剪辑某剧”“跑第N集”“出成片”、要求排查解说画面不准、建场景地图或复跑影视剪辑时使用。强制使用完整大场景地图、字幕/剧本混合检索、父段全局序列解码和60～120帧选择性云端视觉复核。
---

# AutoDSJ 工作流

## 唯一技能源与同步

只修改 Git 仓库中的 `D:\@kaifa\AutoDSJ\project\skills\autodsj`。不得把 Hermes 部署目录当作源文件直接迭代。每次修改后先校验并提交 Git，再运行：

```powershell
cd D:\@kaifa\AutoDSJ\project
.\.venv\Scripts\python.exe scripts\sync_autodsj_skill.py --sync
.\.venv\Scripts\python.exe scripts\sync_autodsj_skill.py --check
```

Hermes 部署目录固定为 `C:\Users\xxx13\AppData\Local\hermes\skills\media\autodsj`。同步工具会删除部署目录中的多余旧文件，并按每个文件 SHA-256 确认两边完全一致。

项目：`D:\@kaifa\AutoDSJ\project`

Python：`D:\@kaifa\AutoDSJ\project\.venv\Scripts\python.exe`

素材根目录示例：`D:\自动剪辑\玫瑰的故事`

换电脑或项目路径变化时，先读 [portable-deployment.md](references/portable-deployment.md)，不得照抄本机绝对路径。

本工作流无 WebUI、无端口，只走 `autodsj.py` 这一条 CLI 管线。配音只用百炼 Qwen 克隆音色。**音量硬性规范：`voice.volume=100`、`drama.source_play_volume=100`，配音与原片再分别经 `loudnorm=I=-16:TP=-1.5:LRA=11` 归一化到 -16 LUFS 等响**。不得用 120%/50% 等纯增益组合代替响度归一化。

## 不可跳过的顺序

### 1. 先审校文案

读取目标单集目录及子目录的 `.txt/.md/.docx` 源文案，不得直接开跑。检查：

- `原片：` / `解说：` 结构完整；
- 开头冲突或悬念足够强，信息递进、因果和结尾追更钩子合理；
- 角色名、他/她、人物关系、动作主体和字幕同音字正确；
- 每段主要推进一个事件，只有尾句可承接下一场景。

《玫瑰的故事》必须读 `references/rose-story-knowledge.md`，并查 `references/rose-story-episodes.json` 的对应集。

### 2. 先建文本和物理镜头索引

```powershell
cd D:\@kaifa\AutoDSJ\project
$PY = ".\.venv\Scripts\python.exe"
& $PY autodsj.py preflight --folder "<单集素材夹>"
& $PY autodsj.py script --folder "<单集素材夹>"
& $PY autodsj.py shots --folder "<单集素材夹>"
```

不要恢复旧的每 8～10 秒抽一帧、全片约 280 帧的 VL 扫描。先以 SRT、审校剧本和物理镜头关键图编完整场景地图；昂贵视觉只在文本缩小候选后运行。

新集还没有 `★ 匹配报告.json` 时，允许一次不渲染引导：

```powershell
& $PY autodsj.py run --folder "<单集素材夹>" --skip-visual --no-render
```

### 3. 建完整大场景地图

在正式匹配或渲染前，必须完整查看原片时间轴，写好 `<素材夹>\_scene_map.json`。原片有多少个完整大场景，`scenes` 就必须有多少个；不得只画文案提到的几个。

必须满足：

- `coverage_reviewed: true`；
- `scene_count == scenes.length`；
- `coverage_ranges` 全部被各大场景连续覆盖，无空洞；
- 每个解说镜头只能落在唯一的父段场景计划中；
- 一段解说默认只有一个主场景；
- 最多允许“整段主场景 → 更短的尾句承上启下场景”；
- 禁止三场景跳转、短前导后长跳转、候选扩窗越出指定大场景。

**效率技巧**：建场景地图时用 `execute_code` 批处理——一次性读取视觉索引用 `print` 输出完整时间轴（time+people+caption），据此划分场景边界，然后写 JSON 到磁盘并立即 `from backend.scene_map import validate_scene_map` 验证，避免多轮 terminal/read_file 来回。

具体 JSON 契约见 `references/hierarchical-shot-matching.md`。新集从零编写场景地图的实操
（先跑 `run --skip-visual --no-render` 引导出 `★匹配报告.json`，据它定 `parent_scene_plans`
的键=`tts_parent_id`、`to_shot`=各段真实分镜数；交叉剪辑剧集用「多 range 场景按剧情线分组」
但所有 range 须划分时间轴不重叠），以及渲染后用人脸库核验成片的完整方法，见
`references/scene-map-authoring-and-verification.md`。

### 4. 建分层镜头并影子匹配

```powershell
& $PY autodsj.py events --folder "<单集素材夹>"
& $PY autodsj.py shadow-match --folder "<单集素材夹>"
& $PY autodsj.py visual --folder "<单集素材夹>" --target-frames 90
& $PY autodsj.py shadow-match --folder "<单集素材夹>"
```

固定层级：`大场景 → 连续事件块 → 物理镜头 → 动作瞬间`。

固定证据顺序：SRT/审校剧本 BM25 → 文本向量 → 可选 CAM++ 角色声纹 → 父段 Viterbi 全局序列 → 60～120 帧选择性云端视觉复核 → 高风险候选前/中/后三帧对比。所有证据只能在父场景内排序，不能将镜头拉到场景外。详细契约见 `references/hybrid-evidence-matching.md`。

`narration_intent.py` 必须区分检索扩展词和视觉硬条件。关系“靠近”、心理“后退”等隐喻可以辅助文本召回，但不得写进 `hard_requirements.actions`；只有明确可见动词、人物、地点和道具才能成为 `must_have`。显式“不是/并非/不要/没有/而非”写入 `must_not_have`，不得反向扩展成正向要求。

时间线固定使用两轮分配：第一轮在全部候选中选择全局未用画面；只有第一轮完全无解时，父段计划才允许第二轮复用已引用的原片对白画面。`planning_summary.strict_fresh` 应尽量等于解说镜头数，`source_reuse_fallback` 应为 0 或极小。广告区在两轮中都是绝对硬禁区，父段计划、人工 override 和复用降级均不得绕过。

`shadow-match` 会生成 `_selective_visual_plan.json`。计划未完成时，候选或场景图变化会使旧视觉索引失效；一旦同一素材、同一场景地图的 60～120 帧计划完整识别，后续影子匹配必须锁定该通用计划，禁止因新视觉描述改变候选排序后反复推翻整套计划。人工修改场景地图 SHA 时锁定自动失效；普通候选变化交给 `_candidate_visual_review.json` 的独立多帧复核处理。运行 `visual` 后必须再跑一次 `shadow-match`。视觉 API 运行期间可读 `_source_visual_index.json` 的 `status/message` 监控进度。

高风险候选复核最多处理 `matching.candidate_review_max_segments` 个解说句；基础层每个候选取物理镜头前/中/后三帧，候选数不足时允许单候选硬确认，但绝不越过 `_scene_map.json` 补候选。人物身份只认 InsightFace；云端只能确认动作、地点、道具和可见事实。完整结果可缓存，`partial` 结果续跑时只重试失败组。

基础层仍 unresolved 时，默认只对这些段运行二级7帧复核（8%～92%均匀覆盖），写入 `_candidate_visual_escalation.json`，不得重跑已通过段或锁定的120帧通用索引。若21图请求被远端断开，保持本地7帧人脸核验，但云端自动均匀降为每候选5帧后重试。加帧后仍稳定出现错误人物，说明候选镜头错，应扩大同一父场景内候选数量，禁止继续无上限加帧或跨场景搜索。`candidate_visual_review_ready=false` 或任一复核句 `unresolved` 时，`safe_to_render` 必须为 false。

有干净角色对白参考时启用 CAM++：

```powershell
uv pip install --python $PY -r requirements-audio.txt
uv pip install --python $PY --no-deps speakerlab==0.0.6
# <剧集根>\_voices\<角色名>\*.wav
& $PY autodsj.py voices --folder "<单集素材夹>"
```

缺 `speakerlab` 或参考音频时允许字幕/剧本路径运行，但报告必须显示 `voice_index=false`，不得声称声纹已生效。

必须检查：

- `★ 分层影子匹配报告.json`；
- `★ 新旧匹配并排对比.json`；
- `★ 分层接管预演报告.json`。
- `_subtitle_event_index.json`、`_selective_visual_plan.json`；有声纹时还要 `_source_voice_index.json`。

只有 `safe_to_render=true` 且 `unresolved=0` 才能渲染。地图、文案、分镜或配音变化后必须重跑 `shadow-match`；不得手改报告绕过哈希和时间线校验。

### 5. 先预跑，再成片

```powershell
& $PY autodsj.py run --folder "<单集素材夹>" --skip-visual --no-render --hierarchical-match
& $PY autodsj.py run --folder "<单集素材夹>" --skip-visual --hierarchical-match
```

正式渲染不带 `--hierarchical-match`、缺场景地图、地图不完整或预演哈希失效时，必须停止，不允许降级到旧匹配方法。

## 人脸库

换新剧或人物容易混淆时，在剧集根目录放 `_faces/<角色>/*.jpg`，每个主要角色 3–5 张清晰正脸，再运行：

```powershell
& $PY autodsj.py faces build --folder "<单集素材夹>"
```

## 成片交付

正式渲染完成后，管线必须自动执行发布交付，不得等用户再次提醒。只有以下文件全部生成并通过门禁，才允许报告“成片完成”：

- `★ 成片.mp4`：可读取时长和视频流；
- `★ 字幕.srt`：可解析，末条字幕不得越过片尾；
- `★ 匹配报告.json`；
- `★ 发布信息.txt`：3 个标题、剧情简介、互动问题、严格 5 个标签、封面文案和实际成片参数；
- `★ 剪映字幕导入.txt`：同时包含原片对白和解说，删除分段标签与空行，保持源文案顺序；
- `★ 交付清单.json`：记录上述文件、视频参数、字幕/文案行数及待复核指代词行数，状态必须为 `ready`。

交付门禁通过后，单集根目录必须只保留原片、原片字幕、源文案、`★ 成片.mp4`、`★ 发布信息.txt`、`★ 剪映字幕导入.txt` 和 `_AutoDSJ工作文件`。`★ 字幕.srt`、匹配报告、场景地图、索引、TTS、裁切片及交付清单全部自动移入 `_AutoDSJ工作文件`。下一次执行处理命令时自动恢复这些资产，成片后再次归档；禁止因整理目录而删除可复用的场景图、声纹或缓存。

交付门禁已接入 `autodsj.py run` 的正式渲染末尾。已有成片缺交付文件、或人工修改文案后只需重建发布包时，运行：

```powershell
& $PY autodsj.py deliver --folder "<单集素材夹>"
```

`scripts/audit_pronouns.py` 和 `scripts/make_jianying_srt_txt.py` 仅用于人工诊断/单文件修复，不再是正式流程的必需手工步骤。向用户交付时报告成片、SRT、匹配报告、发布信息、剪映字幕和交付清单路径。

用户要求保留旧成片时，先复制到单集子目录，再渲染；不要在主目录放多个可被误识别的源视频。

## 安全清理

只在成片存在且验收后执行：

```powershell
& $PY autodsj.py clean --folder "<单集素材夹>"
```

该命令不再删除资产，而是把根目录中的工作文件统一归档到 `_AutoDSJ工作文件`。必须保留原片、原片字幕、源文案和三项公开交付文件。

## 快速排错

- 整段乱跳：先查 `_scene_map.json` 和 `parent_scene_plans`，不要扩大时间窗。
- 人物错：检查父场景是否选错，再补人脸参考照。
- 动作错：检查事件块、物理镜头的多关键帧和 SRT，不得跳出父场景找高分帧。
- 改了文案：重跑脚本表与 `shadow-match`。若新的 `_selective_visual_plan.json` 使视觉索引过期，再跑默认 `visual`（仍只有60～120帧），不要恢复密集全片扫描。
- 匹配慢：先检查 `_event_text_embeddings.json` 和 `_query_text_embeddings.json` 是否存在；内容不变时第六集93镜实测重匹配约7秒。
- 多集任务：串行渲染，不要同时吃满笔记本 CPU/内存/磁盘。
- **场景地图覆盖空洞**：`validate_scene_map` 报 `覆盖存在空洞` 时，常见原因：(a) `coverage_ranges` 包含了没有视觉帧的广告区间——把广告区从 `coverage_ranges` 移除，或拆成多段 `[[150, 978], [1001, 2668]]`；(b) 某场景 `ranges` 结尾与下个场景开头有间隔——逐场景检查 `scenes[].ranges` 的连续性，确保相邻场景首尾相接（允许广告区打断）。`excluded_ranges` 不影响覆盖校验，只影响匹配，广告区必须从 `coverage_ranges` 移出而非只写在 `excluded_ranges`。
- **场景地图写入顺序**：先用 Python 写 `_scene_map.json` 到磁盘，再 `validate_scene_map` 验证。`validate_scene_map` 从磁盘读取场景地图，不是从内存。先写后验，不要反过来。

三个「别慌」信号（都不是故障，详见 `references/scene-map-authoring-and-verification.md`）：

- 渲染日志刷 `[场景锁定] 行N-镜M → 「场景」` 且与 `parent_scene_plans` 不符——那是旧分配器过渡噪声，随后的 `分层匹配正式接管：N 个解说分镜` 会整段覆盖，以接管后置信度全 `H` 的 `★匹配报告.json` 为准。
- 接管后必须分别检查解说复用和原片明确复引。解说画面与任何已用片段重叠都应先消除；只有文案明确重复引用同一句原片对白、且必须保持口型与原声时，才允许记录为必要例外。
- 「独立爆款版」短文案出片仅 2-3 分钟——正确。成片时长 = Σ解说配音 + Σ原片对白，由文案长度决定；引导日志「解说目标约2490s(90%)」只是按原片长度的默认假设，不代表漏渲染。

- **广告禁区误封**：`autodsj.py run` 报 `RuntimeError: 素材区间命中广告禁区` 时，先查 `_source_ad_intervals.json`。视觉 API 描述中的"贴满小广告的墙""贴有广告的柱子"等场景陈设词会被 `backend/ad_filter.py` 关键词匹配为广告信号，把正常剧情封掉。修复：用 Python 把 `_source_visual_index.json` 中 `caption/props/scene/action/people` 字段里的 `广告` 替换为 `招贴` 或 `告示`，再删掉 `_source_ad_intervals.json` 让它重新生成。注意 `props` 字段也常含"广告"（如"左侧贴有广告的柱子""小广告、窗台盆栽"），必须一并处理，不能只修 `caption`：
  ```powershell
  cd D:\@kaifa\AutoDSJ\project
  & $PY -c "
  import json; from pathlib import Path
  p = Path(r'<单集素材夹>\_source_visual_index.json')
  d = json.loads(p.read_text('utf-8'))
  for f in d.get('frames', []):
      for k in ('caption','props','scene','action'):
          if isinstance(f.get(k), str) and '广告' in f[k]:
              f[k] = f[k].replace('广告','告示').replace('小告示','招贴')
  p.write_text(json.dumps(d, ensure_ascii=False, indent=2), 'utf-8')
  print('done')
  "
  rm "<单集素材夹>\_source_ad_intervals.json"
  ```
  修完重跑 `autodsj.py run --skip-visual --no-render`。注意：仅修正视觉层不会丢失信息——广告区判定仍靠字幕信号（如"唯品会""搜玫瑰"等）正常工作。详细案例见 `references/ad-filter-false-positive-fix.md`。

项目的完整命令与数据结构以 `D:\@kaifa\AutoDSJ\project\README.md` 为准。

## 耗时参考

单集全流程端到端耗时约 **40-80 分钟**，主要瓶颈：

| 阶段 | 耗时 | 备注 |
|------|------|------|
| 初始 visual（36帧） | 5-10分钟 | SiliconFlow Qwen VL API |
| 引导 scaffold（TTS+匹配） | 5-10分钟 | 百炼 TTS 偶尔 SSL 重试 |
| 场景地图编写 | 1-2分钟 | execute_code 一次性完成 |
| events + shadow-match | 2-5分钟 | 文本嵌入是主要开销 |
| 选择性 visual（60～120帧） | 5-25分钟 | 取决于帧数、并发与云端长尾；可续跑 |
| shadow-match 再跑 | 2-3分钟 | |
| 预跑 + 正式渲染 | 20-40分钟 | TTS 复用缓存 + FFmpeg 编码 |

**多集批量（4集实测）**：串行交错模式，总耗时约 **4-5小时**。
具体做法：在上一集渲染（FFmpeg 编码约15-30分钟）期间，为下一集做全部准备工作（preflight→visual→script→shots→scaffold→场景地图→events→shadow-match→visual selective），等上一集成片时下一集已就绪可立即开跑。

**常见延时因素**：
- 视觉 API 超时（SiliconFlow 429/500），重试可续跑
- 百炼 TTS SSL EOF 错误，管线内置重试自动恢复
- 广告关键词误封需手动清洗 `_source_visual_index.json` 后重跑

## 新集并行准备（2026-07）

新集默认先运行：

```powershell
$PY autodsj.py prepare --folder "<单集文件夹>"
```

该命令并行建立脚本表与物理镜头索引，生成事件索引、
`_scene_map.draft.json` 和风险自适应的 60/90/120 帧云端视觉计划，再以有界并发执行
稀疏抽帧和批量视觉识别。视觉完成后自动把识别证据回填到镜头/事件索引，不重跑镜头边界。

草案始终为 `coverage_reviewed=false`，不得直接用于正式成片。人工核对完整覆盖、场景边界、
广告排除和父段计划后，另存为 `_scene_map.json` 并设置 `coverage_reviewed=true`。
只生成索引和草案时使用 `autodsj.py prepare --skip-visual`；显式固定视觉预算时使用
`--target-frames 60..120`，否则保持风险自适应。
