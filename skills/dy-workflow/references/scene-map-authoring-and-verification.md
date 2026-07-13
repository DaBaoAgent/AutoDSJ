# 新集场景地图编写 + 成片核验实操

跑一集全新剧集（无任何索引）时，`_scene_map.json` 的 `parent_scene_plans`
不能凭空写——必须先跑一次引导匹配拿到每段解说的真实分镜结构，再据此下笔。
下面是实测顺序与两个关键技巧，配套「说谁→是谁」的成片视觉核验。

## 新集完整顺序（实测可用）

```
preflight → visual → script
  → run --skip-visual --no-render            # 引导：产 ★匹配报告.json 基线
  → 读 ★匹配报告.json 定 parent_scene_plans   # 见下「技巧1」
  → 写 _scene_map.json                        # 见下「技巧2」
  → shots → events → shadow-match             # 要 unresolved=0
  → run --skip-visual --no-render --hierarchical-match  # 预跑接管
  → run --skip-visual --hierarchical-match              # 正式成片
```

视觉复核现在是候选驱动的30～60帧，不再按8.86秒固定间隔扫约287帧。先用 SRT、
剧本和物理镜头关键图写完整场景地图，`shadow-match` 生成
`_selective_visual_plan.json` 后再运行 `visual`。后台运行时仍可用
`_source_visual_index.json` 的 `success_count/frame_count/status` 监控。

## 技巧1：parent_scene_plans 的键与镜头数从匹配报告读

`_scene_map.json` 里 `parent_scene_plans` 的**键是 `tts_parent_id`（通常 1..9），
不是 script_row_id**。校验（`backend/scene_map.py`）会逐条解说核对
`from_shot..to_shot` 覆盖该段所有 `shot_index`，所以 `to_shot` 必须等于该段真实分镜数。

先解析引导报告拿到结构，别猜：

```python
import json
d=json.loads(open(r"<单集>\★ 匹配报告.json",encoding='utf-8').read())
from collections import OrderedDict
g=OrderedDict()
for s in d["segments"]:
    if s.get("row_type")!="narration": continue
    key=s.get("tts_parent_id") or s.get("script_row_id")
    g.setdefault(key,[]).append(int(s.get("shot_index") or 0))
for k,shots in g.items():
    print(f'"{k}": to_shot={max(shots)}  shots={sorted(shots)}')
```

每段默认写单组 `[{"from_shot":1,"to_shot":N,"scene":"<场景名>"}]`。
scene 名必须在 `scenes[].name` 里存在，否则门禁失败。

## 技巧2：交叉剪辑剧集用「多 range 场景」按剧情线分组

《玫瑰的故事》这类剧把两条线（如玫瑰线↔黄振华线）交叉剪辑，同一剧情线的画面
散落在时间轴多处。做法：

- 场景按**人物对 + 剧情线**定义，一个场景可有**多个 range**，把交叉剪辑的碎片
  归到同一线。例：「庄国栋与玫瑰-定情交钥匙」ranges=[[149,222],[505,550],
  [1655,1746],[1825,1913]]。
- 但所有场景 range 合起来必须**划分（partition）时间轴**：不重叠、并集完整覆盖
  `coverage_ranges`（空洞>0.25s 就报错）。任一秒只能属于一个场景，否则
  `event_index._scene_for_time` 按 midpoint 取第一个命中会误标。
- 内嵌广告秒必须从 `coverage_ranges` 与相邻场景 `ranges` 中抠掉，并写进
  `excluded_ranges`。广告可打断同一场景形成多个 range；父段计划、人工 override
  和复用降级都不能绕过广告硬禁区。
- 只做覆盖、无解说锚定的场景（如本集「广西办公室」「车内」「行李」）照写，
  coverage 需要它们连续，没有解说指向也没关系。
- 写地图前**通读 `_source_visual_index.json` 时间轴**（time+people+caption，
  人脸库已注入「演员（饰角色）」）划分场景边界；正片可用区 =
  [trim_head后首帧, 原片时长-trim_tail]。

写完先本地验一遍再往下跑：
```python
from pathlib import Path; from backend.scene_map import validate_scene_map
import json
folder=Path(r"<单集>")
rep=json.loads((folder/"★ 匹配报告.json").read_text("utf-8"))
validate_scene_map(folder, rep.get("segments",[]))   # 抛异常即门禁失败
```

## 成片视觉核验：说谁→是谁（人脸库直接查成片抽帧）

渲染后别只看 JSON。从 `★ 成片.mp4` 按解说 `output_start` 抽帧，用给源帧打标的
**同一个 InsightFace 人脸库**识别，确认画面真出现了对的人：

```python
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path; from backend import face_gallery as fg
g=fg.load_gallery(Path(r"D:\自动剪辑\<剧>\_face_gallery.json"))
r=fg.FaceIdentifier(g, threshold=0.36)       # 类名 FaceIdentifier
idt=r.identify(str(frame_jpg))
print(fg.render_people_field(idt) if idt else "(未检出人脸)")
```

- 抽帧时间用 `★匹配报告.json` 里 narration 段的 `output_start`（成片有 padding_head
  留白，落点会偏移~0.5-1s，抽到邻镜是正常的，多抽几个偏移或对源 clip 秒直接查）。
- 广角/背身帧「未检出人脸」不算错，场景对就行。
- 交叉核对：拿该段 `clip_start-clip_end` 去源片同秒抽帧再识别，两边一致即确认。

## 三个「别慌」信号（都不是故障）

1. **渲染日志刷一堆 `[场景锁定] 行N-镜M → 「某场景」`，且和你的
   parent_scene_plans 不一致**——那是旧分配器 `allocate_visual_all` 的过渡噪声，
   随后的 `分层匹配正式接管：N 个解说分镜` 会用预演报告**整段覆盖**这些落点。
   以接管后的 `★匹配报告.json`（置信度全 `H`）为准，不是那些日志。

2. **接管后出现 `interval_overlap_count>0 / global_no_reuse=false`**——必须拆分统计。
   解说画面与任何已用片段重叠都先修复；正常目标是
   `planning_summary.source_reuse_fallback=0`。只有文案明确重复引用同一句原片对白、
   且必须保持口型和原声时，才允许保留为必要例外并向用户说明。

3. **「独立爆款版」文案出片只有 2-3 分钟**——正确。成片时长 = Σ解说配音时长 +
   Σ原片对白时长，由文案长度决定；引导日志里的「解说目标约2490s(90%)」只是
   管线按原片长度的默认假设，短文案不适用，别当成漏渲染。

## 精确锁某句到指定画面（可选）

某句解说落在了本场景但非字面最贴的一帧（如「两个男人聊篮球」落到黄振华认门段而非
两人球场同框），若必须精确：在 `_scene_map.json` 的 `overrides` 加
`{"contains":"一聊到篮球","scene":"<场景名>","range":[943.8,962.0]}`，重跑 shadow-match 起。
overrides 是人工复核的一等约束，优先级高于语义相似。两条实测必守：

1. **override 的 `scene` 必须等于该段 `parent_scene_plans` 里的场景名**。因为
   `hierarchical_matcher._scene_hint` 里 `hint = planned_hint or text_hint`，父段计划
   优先，只有 `text_hint.name == planned_hint.name` 时才把 `manual_range` 合并进去
   （见 hierarchical_matcher.py:162-164）。名字对不上 → override 被静默无视。
2. **`range` 要是一段"整段都是想要画面"的连续区间**，别贪大。`timeline_planner.fit_window`
   在 range 内滑窗放 audio_duration 长的 clip，range 里混了单人镜就可能落到单人镜。
   找法：读 `_source_shot_index.json`，筛 `nearest_visual_frames[].people` **连续**含目标
   两角色的物理镜头段（本集实测 [943.8,962.0] 是 13 个连续两人同框镜），取那段做 range。
   `contains` 用该句独有短语（"一聊到篮球"），确保只命中这一镜、不误伤别段。

## 交付脚本位置

`audit_pronouns.py`、`make_jianying_srt_txt.py` 在**技能目录 scripts/** 下
（`<技能>/scripts/`），不在 DY 项目里。用项目 venv 跑，参数是源文案路径。
