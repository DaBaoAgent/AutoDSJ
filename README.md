# DY 工作流

面向电视剧、短剧解说的单线自动剪辑管线。正式成片只允许以下路径：

`文案审校 → 视觉索引 → 完整大场景地图 → 事件/物理镜头索引 → 分层影子匹配 → 场景门禁 → Qwen 克隆配音 → 渲染`

核心原则不是在整集里逐句乱搜，而是先确定整段解说所属的大场景，再在这个大场景内部按人物、动作、对白字幕和关键帧寻找动作瞬间。

## 唯一正式管线

- CLI 入口：`dy.py`
- 匹配内核：`anchored_pipeline.py`
- 配音后端：百炼 Qwen 克隆音色
- 默认配音音量：100%
- 默认原片对白音量：100%（与配音等响，手机正常音量直接播放）
- 正式渲染必须启用 `--hierarchical-match`
- 正式渲染必须存在通过校验的 `_scene_map.json`
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
├── ★ 分层接管预演报告.json
└── ★ 成片.mp4
```

不要把最终成片当作新原片。素材检测会优先选择源视频，但目录仍应保持清晰。

## 标准执行顺序

在项目目录执行：

```powershell
cd D:\@kaifa\DaobaoAI-DY\project
.\.venv\Scripts\python.exe dy.py doctor
```

### 1. 先审校文案

必须先查看素材目录及其子目录里的文案，确认：

- 开头有冲突、悬念或结果前置，能在数秒内建立观看理由；
- 每段只推进一个主要事件，人物姓名、关系、动作和因果正确；
- “谁说话、谁接电话、谁走路、谁摔倒、谁吃饭”等主体明确；
- 转折句确实承接下一大场景；
- 角色名错字、同音字和字幕识别错误已修正。

文案未通过时不得继续自动成片。

### 2. 建立视觉与脚本基础

```powershell
.\.venv\Scripts\python.exe dy.py preflight --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe dy.py visual --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe dy.py script --folder "D:\自动剪辑\某剧\第N集"
```

首次处理新集、尚无旧匹配报告时，可运行一次不渲染的引导匹配：

```powershell
.\.venv\Scripts\python.exe dy.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --no-render
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
.\.venv\Scripts\python.exe dy.py shots --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe dy.py events --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe dy.py shadow-match --folder "D:\自动剪辑\某剧\第N集"
```

匹配层级固定为：

`大场景 → 连续事件块 → 物理镜头 → 动作瞬间`

候选证据综合：关键识别帧、人脸参考比对、原片 SRT、剧本知识、人物/动作/地点意图。字幕与人物证据用于大场景内排序，不能把候选带出场景边界。

检查以下报告：

- `★ 分层影子匹配报告.json`
- `★ 新旧匹配并排对比.json`
- `★ 分层接管预演报告.json`

必须确认 `unresolved=0`，且没有越界候选、缺失父段计划或第三场景跳转。

### 5. 先预跑，再正式渲染

```powershell
.\.venv\Scripts\python.exe dy.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --no-render --hierarchical-match
.\.venv\Scripts\python.exe dy.py run --folder "D:\自动剪辑\某剧\第N集" --skip-visual --hierarchical-match
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
  }
}
```

101%–200% 的配音音量由本地增益和限幅器实现，避免直接削波；100% 为原样输出。原片对白段与配音统一按 100% 播放，两者等响，适合手机正常音量直接观看；解说覆盖处原片声默认静音。

## 清理规则

完成渲染并验收后，可清理可重建产物：

```powershell
.\.venv\Scripts\python.exe dy.py clean --folder "D:\自动剪辑\某剧\第N集"
```

会删除：旧裁切片、拼接中间视频、旧配音 WAV/TTS 分段、诊断帧、渲染日志和临时清单。

始终保留：

- 原片、原字幕、用户文案；
- `_scene_map.json`；
- 视觉、人脸、字幕、镜头、事件和帧嵌入索引；
- 分层匹配审计报告；
- `★ 成片.mp4`、`★ 字幕.srt`、`★ 匹配报告.json` 和发布文件。

## 常用诊断

```powershell
.\.venv\Scripts\python.exe dy.py status --folder "D:\自动剪辑\某剧\第N集"
.\.venv\Scripts\python.exe dy.py doctor
.\.venv\Scripts\python.exe dy.py faces list --folder "D:\自动剪辑\某剧\第N集"
```

画面不准时先检查大场景地图和父段计划，不要先扩大搜索时间窗。人物不准时补充 `_faces/<角色>/` 参考照并重建人脸库；动作不准时检查事件块与物理镜头关键帧。

## 主要模块

```text
dy.py                           唯一 CLI 入口与正式门禁
anchored_pipeline.py            Qwen 配音、受约束时间线与渲染
backend/scene_map.py            完整场景地图校验与哈希门禁
backend/narration_intent.py     结构化人物/动作/地点/转场意图
backend/shot_index.py           物理镜头和多关键帧索引
backend/event_index.py          大场景内连续事件块
backend/hierarchical_matcher.py 分层候选与影子报告
backend/timeline_planner.py     主场景/尾句承接计划
backend/cleanup.py              安全清理可重建产物
```

任何新功能都应接入这条管线；不得另建平行渲染入口或静默回退到无场景约束的匹配方式。
