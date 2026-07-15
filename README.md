# AutoDSJ 工作流

面向电视剧、短剧解说的单线自动剪辑管线。正式成片只允许以下路径：

`文案审校 → 字幕/剧本索引 → 完整大场景地图 → 事件/物理镜头索引 → 父段全局序列解码 → 60～120帧选择性云端视觉复核 → 场景门禁 → Qwen 克隆配音 → 渲染 → 发布交付门禁`

核心原则不是在整集里逐句乱搜，也不是先做数百帧全片视觉扫描。先用 SRT、审校剧本和场景知识把整段解说缩到大场景/事件块，再以父段为单位做全局连续序列解码，最后只对歧义候选、动作镜头和场景覆盖点复核 60～120 帧。

## 唯一正式管线

- CLI 入口：`autodsj.py`
- 匹配内核：`anchored_pipeline.py`
- 配音后端：百炼 Qwen 克隆音色
- 默认配音音量：100%
- 默认原片对白音量：100%（与配音等响，手机正常音量直接播放）
- 正式渲染必须启用 `--hierarchical-match`
- 正式渲染必须存在通过校验的 `_scene_map.json`
- 正式渲染成功后必须自动生成发布信息、剪映字幕和交付清单；缺任一交付物均视为失败
- GPT-SoVITS、CosyVoice、系统音色和旧 UI 管线均不再属于本项目

## 素材目录

每一集单独一个目录，至少包含：

```text
第N集/
├── 原片.mkv 或 原片.mp4
├── 原片字幕.srt 或 ass
├── 原片解说文案.txt / md / docx
├── _scene_map.json                 # 正式渲染前强制存在
├── _source_visual_index.json
├── _source_shot_index.json
├── _source_event_index.json
├── _subtitle_event_index.json
├── _source_voice_index.json          # 可选：CAM++ 说话人/角色声纹
├── _selective_visual_plan.json       # 每集 60～120 帧云端复核计划
├── ★ 分层接管预演报告.json
├── ★ 成片.mp4
├── ★ 字幕.srt
├── ★ 匹配报告.json
├── ★ 发布信息.txt
├── ★ 剪映字幕导入.txt
└── _AutoDSJ工作文件/                 # 场景图、索引、报告、TTS、SRT及交付清单
```

成片完成后，单集根目录只保留原片、原片字幕、源文案、成片、发布信息、剪映字幕导入文件和 `_AutoDSJ工作文件` 这一个工作目录。再次运行处理命令时会自动把工作文件恢复到根目录供旧内核复用，交付完成后重新归档。不要把最终成片当作新原片。

## 标准执行顺序

在项目目录执行：

```powershell
cd D:\@kaifa\AutoDSJ\project
.\.venv\Scripts\python.exe autodsj.py doctor
```

### 1. 先审校文案

必须先查看素材目录及其子目录里的文案，确认：

- 开头有冲突、悬念或结果前置，能在数秒内建立观看理由；
- 每段只推进一个主要事件，人物姓名、关系、动作和因果正确；
- “谁说话、谁接电话、谁走路、谁摔倒、谁吃饭”等主体明确；
- 转折句确实承接下一大场景；
- 角色名错字、同音字和字幕识别错误已修正。

文案未通过时不得继续自动成片。

### 2. 建立字幕/剧本与物理镜头基础

新集优先使用并行准备命令：

```powershell
.\.venv\Scripts\python.exe autodsj.py prepare --folder "D:\自动剪辑\某剧\第N集"
```

它会并行建立脚本表与物理镜头索引，继续生成事件索引、`_scene_map.draft.json`
场景草案和自适应视觉复核计划，并以有界并发完成稀疏抽帧及批量视觉识别。视觉预算默认
按风险在 60/90/120 帧间选择；只有显式传入 `--target-frames N` 才固定帧数。视觉完成后会
自动把人物/动作描述回填到镜头及事件索引，不会重新检测镜头边界。

场景草案永远保持 `coverage_reviewed=false`，不会覆盖正式 `_scene_map.json`。必须人工核对
边界、广告排除、名称、人物和父段计划后，另存为 `_scene_map.json` 并设置
`coverage_reviewed=true`，正式渲染门禁没有放宽。

需要只生成索引和场景草案、不调用视觉模型时：

```powershell
.\.venv\Scripts\python.exe autodsj.py prepare --folder "D:\自动剪辑\某剧\第N集" --skip-visual
```

以下分步命令仍可用于诊断：

```powershell
.\.venv\Scripts\python.exe autodsj.py preflight --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe autodsj.py script --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe autodsj.py shots --folder "D:\自动剪辑\某剧\第N集"
```

先用 SRT、剧本和物理镜头关键图完成场景地图。不要为了建图恢复每 8～10 秒一帧的全片 VL 扫描。

首次处理新集、尚无旧匹配报告时，可运行一次不渲染的引导匹配：

```powershell
.\.venv\Scripts\python.exe autodsj.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --no-render
```

`--no-render` 只用于生成基础报告，不代表通过正式门禁。

### 3. 建完整大场景地图

逐段查看完整原片时间轴，把正片中每一个完整大场景全部登记到 `_scene_map.json`。场景数量必须等于原片实际的大场景数量，不能只登记文案提到的几个场景。

最小结构：

```json
{
  "version": 4,
  "coverage_reviewed": true,
  "coverage_ranges": [[150.0, 2665.0]],
  "excluded_ranges": [[0.0, 150.0], [2665.0, 2835.0]],
  "scene_count": 2,
  "scenes": [
    {
      "name": "公司-玫瑰与庄国栋对质",
      "ranges": [[150.0, 320.0]],
      "characters": ["黄亦玫", "庄国栋"],
      "keywords": ["公司", "对质", "关机"]
    },
    {
      "name": "餐厅-庄国栋与母亲谈话",
      "ranges": [[320.0, 500.0]],
      "characters": ["庄国栋", "母亲"],
      "keywords": ["餐厅", "母亲", "感情"]
    }
  ],
  "parent_scene_plans": {
    "1": [
      {"from_shot": 1, "to_shot": 5, "scene": "公司-玫瑰与庄国栋对质"},
      {"from_shot": 6, "to_shot": 6, "scene": "餐厅-庄国栋与母亲谈话"}
    ]
  },
  "overrides": []
}
```

硬性契约：

1. `coverage_reviewed` 必须为 `true`。
2. `scene_count` 必须与 `scenes` 数量一致。
3. `coverage_ranges` 内不能有未归属场景的时间空洞。
4. 每个大场景必须有唯一名称和合法时间范围。
5. 每个解说镜头必须且只能命中一个 `parent_scene_plans` 组。
6. 每段解说默认只允许一个主场景。
7. 最多允许第二个场景，且只能位于连续尾句，镜头数必须少于主场景。
8. 不允许“前场景→中间场景→第三场景”，也不允许短引子后整段跳到另一场景。
9. 所有候选画面和最终裁切区间都必须包含在指定大场景内，不得用时间窗向场景外扩张。

地图被修改后，旧接管预演的 SHA-256 会失效，必须重跑影子匹配，避免拿旧约束渲染。

### 4. 建分层索引并影子匹配

```powershell
.\.venv\Scripts\python.exe autodsj.py events --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe autodsj.py shadow-match --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe autodsj.py visual --folder "D:\自动剪辑\某剧\第N集" --target-frames 90
.\.venv\Scripts\python.exe autodsj.py shadow-match --folder "D:\自动剪辑\某剧\第N集"
```

匹配层级固定为：

`大场景 → 连续事件块 → 物理镜头 → 动作瞬间`

候选证据按以下顺序工作：

1. BM25 字面检索合并原片 SRT、场景人物/关键词和已审校剧本；
2. `text-embedding-v4` 补足解说概括与字幕字面之间的语义差，事件和查询向量均按内容签名缓存；
3. CAM++ 在 SRT 对白区间内与角色参考音频比对，提供“谁正在说话”的声纹证据；无需跑全片 VAD/聚类；
4. 父段 Viterbi 解码整组候选，奖励同一/相邻事件的顺序推进，重罚倒序、跨大场景跳转；
5. 只对动作、低分差歧义候选和每个大场景覆盖点调用云端视觉模型，单集硬限制 60～120 帧。

`shadow-match` 会写出 `_selective_visual_plan.json`。候选、场景图或计划时间变化后，旧视觉索引会自动失效；重新执行 `visual` 后再跑一次 `shadow-match`，让识别结果进入最终候选。任何证据都只能在父场景内排序，不能把候选带出场景边界。

可选声纹启用：

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r requirements-audio.txt
uv pip install --python .\.venv\Scripts\python.exe --no-deps speakerlab==0.0.6
# 放置 <剧集根>\_voices\<角色名>\*.wav（每人至少一段干净对白）
.\.venv\Scripts\python.exe autodsj.py voices --folder "D:\自动剪辑\某剧\第N集"
```

没有 `speakerlab` 或角色参考音频时，匹配器明确记录 `voice_index=false` 并继续使用字幕/剧本，不伪造角色声纹。

检查以下报告：

- `★ 分层影子匹配报告.json`
- `★ 新旧匹配并排对比.json`
- `★ 分层接管预演报告.json`

必须确认 `unresolved=0`，且没有越界候选、缺失父段计划或第三场景跳转。时间线分配固定先尝试全局未用画面；只有父段内所有新候选都无法容纳配音时，才允许复用已引用的原片对白画面，并在 `planning_summary.source_reuse_fallback` 中计数。广告区是绝对硬禁区，人工覆盖、父段计划和复用降级均不得绕过。

广告索引同时使用字幕商业话术和选择性视觉中的品牌/产品展示信号。审核出的广告必须从 `_scene_map.json.coverage_ranges` 与对应场景 `ranges` 中移除，并写入 `excluded_ranges`；修改广告区或场景图后必须重跑 `shadow-match`。

### 5. 先预跑，再正式渲染

```powershell
.\.venv\Scripts\python.exe autodsj.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --no-render --hierarchical-match
.\.venv\Scripts\python.exe autodsj.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --hierarchical-match
```

正式命令缺少 `--hierarchical-match`、场景地图不完整、父段计划不连续或地图与预演摘要哈希不一致时，程序必须停止，不能降级到旧匹配器继续渲染。

## 默认配置

配置文件：`config/user_config.json`

```json
{
  "voice": {
    "mode": "clone",
    "provider": "qwen",
    "volume": 100,
    "speech_rate": 1.0,
    "pitch": 1.0
  },
  "drama": {
    "keep_source_audio": true,
    "source_play_volume": 100,
    "narration_source_volume": 0
  },
  "visual": {
    "selective_target_frames": 90,
    "selective_min_frames": 60,
    "selective_max_frames": 120
  },
  "matching": {
    "use_dense_text": true,
    "use_voice_evidence": true,
    "voice_similarity_threshold": 0.48,
    "max_event_candidates": 8
  }
}
```

配音和原片的纯增益固定为 100%，渲染时再分别通过 `loudnorm=I=-16:TP=-1.5:LRA=11` 统一听感响度；解说覆盖处原片声默认静音。不要用 120%/50% 的增益组合代替响度归一化。

## AutoDSJ 技能同步

Git 中的唯一技能源是 `skills/autodsj`，Hermes 目录只是部署副本。所有规则迭代必须先修改仓库版本、测试并提交，再单向同步：

```powershell
.\.venv\Scripts\python.exe scripts\sync_autodsj_skill.py --sync
.\.venv\Scripts\python.exe scripts\sync_autodsj_skill.py --check
```

默认目标为 `C:\Users\xxx13\AppData\Local\hermes\skills\media\autodsj`。`--check` 会比较相对路径和 SHA-256；缺失、多余或内容不同都会返回非零退出码，避免 Codex 与 Hermes 使用不同版本。

## 清理规则

完成渲染并验收后，可清理可重建产物：

```powershell
.\.venv\Scripts\python.exe autodsj.py clean --folder "D:\自动剪辑\某剧\第N集"
```

会删除：旧裁切片、拼接中间视频、旧配音 WAV/TTS 分段、诊断帧、渲染日志和临时清单。

始终保留：

- 原片、原字幕、用户文案；
- `_scene_map.json`；
- 选择性视觉、人脸、字幕/剧本、声纹、镜头、事件和文本嵌入索引；
- 分层匹配审计报告；
- `★ 成片.mp4`、`★ 字幕.srt`、`★ 匹配报告.json` 和发布文件。

## 常用诊断

```powershell
.\.venv\Scripts\python.exe autodsj.py status --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe autodsj.py doctor
.\.venv\Scripts\python.exe autodsj.py faces list --folder "D:\自动剪辑\某剧\第N集"
```

画面不准时先检查大场景地图和父段计划，不要先扩大搜索时间窗。人物不准时补充 `_faces/<角色>/` 参考照并重建人脸库；动作不准时检查事件块与物理镜头关键帧。

## 主要模块

```text
autodsj.py                           唯一 CLI 入口与正式门禁
anchored_pipeline.py            Qwen 配音、受约束时间线与渲染
backend/scene_map.py            完整场景地图校验与哈希门禁
backend/narration_intent.py     结构化人物/动作/地点/转场意图
backend/shot_index.py           物理镜头和多关键帧索引
backend/event_index.py          大场景内连续事件块
backend/text_retriever.py       SRT/审校剧本 BM25＋向量混合检索
backend/voice_index.py          本地 CAM++ 字幕区间角色声纹证据
backend/sequence_decoder.py     父段候选 Viterbi 全局序列解码
backend/selective_visual.py     候选驱动 60～120 帧云端视觉复核计划
backend/hierarchical_matcher.py 分层候选与影子报告
backend/timeline_planner.py     主场景/尾句承接计划
backend/cleanup.py              安全清理可重建产物
```

任何新功能都应接入这条管线；不得另建平行渲染入口或静默回退到无场景约束的匹配方式。
