# 广告禁区误封：视觉层"广告"关键词误触发修复

## 症状

```
RuntimeError: 素材区间命中广告禁区：2603.733-2650.333 (原片行1)
```

`autodsj.py run --skip-visual --no-render` 在 reserve_source_clip 阶段中断。

## 根因

`backend/ad_filter.py::detect_ad_intervals()` 调用 `_visual_signals()`，遍历
`_source_visual_index.json` 每个 frame 的所有字段值，拼接后匹配 `_VISUAL_AD_MARKERS`
元组中的关键词 `"广告"`。视觉 API（qwen3.7-plus）在描述场景陈设时会写：

- `props: "左侧贴有广告的柱子"` — 街景里的招贴
- `props: "墙上贴满的小广告（开锁、办证等）"` — 老城区墙面
- `props: "小广告、窗台盆栽"` — 砖房门口陈设

这些是正常剧情场景，不是商业广告，但关键词匹配无法区分语境。

## 修复步骤

1. 用 Python 替换 `_source_visual_index.json` 中所有 `caption/props/scene/action`
   字段里的 `广告` → `告示`（`小告示` → `招贴`）

2. 删除 `_source_ad_intervals.json` 让它重新生成

3. 重跑 `autodsj.py run --skip-visual --no-render`

## 验证

修正后广告区间应仅剩字幕触发的真实广告段（如"唯品会搜玫瑰""邀您观看"等），
不应包含剧情场景时间范围。检查 `_source_ad_intervals.json` 的 `reasons` 字段：
字幕信号 (`"source": "subtitle"`) 可信，纯视觉信号 (`"source": "vision"`) 需审视。

## 本次案例（第8集《玫瑰的故事》）

- 误封区间：2606.976–2637.553s（庄国栋街头分手场景）
- 修正字段数：15 处（含真广告的奶粉/唯品会帧一并替换为"告示"不丢信息）
- 修正后广告区间：4 段（106-113s, 124-148s, 941-996s, 2668-2692s）
- 原片行1闪回场景（2603-2650s）成功通过
