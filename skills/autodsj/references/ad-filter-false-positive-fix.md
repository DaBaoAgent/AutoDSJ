# 广告禁区误封：视觉层"广告"关键词误触发修复

## 症状

```
RuntimeError: 素材区间命中广告禁区：2603.733-2650.333 (原片行1)
```

`autodsj.py run --skip-visual --no-render` 在 reserve_source_clip 阶段中断。

## 根因

`backend/ad_filter.py::detect_ad_intervals()` 调用 `_visual_signals()`，遍历
`_source_visual_index.json` 每个 frame 的所有字段值。旧实现只要匹配到
`_VISUAL_AD_MARKERS` 中的 `"广告"` 就会封锁整个采样单元；同时旧的
`shadow-match` 只读取已有 `_source_ad_intervals.json`，可能在影子报告通过后，
由正式分配器重新生成不同的广告区间，导致“门禁通过、正式预跑失败”。

视觉 API（qwen3.7-plus）在描述场景陈设时常写：

- `props: "左侧贴有广告的柱子"` — 街景里的招贴
- `props: "墙上贴满的小广告（开锁、办证等）"` — 老城区墙面
- `props: "小广告、窗台盆栽"` — 砖房门口陈设

这些是正常剧情场景，不是商业广告，但关键词匹配无法区分语境。

## 当前修复

1. `_visual_ad_marker()` 自动排除“小广告、办证、刻章、搬家保洁、墙面/柱子广告字迹”等场景陈设。
2. 明确的“广告插播、广告画面、品牌/商品展示、产品宣传”等商业语境仍会被封锁。
3. `shadow-match` 每次从当前字幕和视觉索引重新运行 `detect_ad_intervals()`，
   使影子规划与正式分配器使用同一份广告硬约束。

修改广告识别逻辑或视觉索引后，应重新运行：

```powershell
& $PY autodsj.py shadow-match --folder "<单集素材夹>"
& $PY autodsj.py run --folder "<单集素材夹>" --skip-visual --no-render --hierarchical-match
```

## 验证

修正后广告区间应仅剩字幕触发的真实广告段（如"唯品会搜玫瑰""邀您观看"等），
不应包含剧情场景时间范围。检查 `_source_ad_intervals.json` 的 `reasons` 字段：
字幕信号 (`"source": "subtitle"`) 可信；纯视觉信号 (`"source": "vision"`)
必须能在 `reasons` 中看到明确商业语境，不能只有墙面招贴。

如果旧版本仍需人工处理，才使用“把场景陈设中的广告改写为招贴/告示并删除缓存”
的临时方法；当前版本不应再修改原始视觉索引来绕过误封。

## 本次案例（第8集《玫瑰的故事》）

- 误封区间：2606.976–2637.553s（庄国栋街头分手场景）
- 修正字段数：15 处（含真广告的奶粉/唯品会帧一并替换为"告示"不丢信息）
- 修正后广告区间：4 段（106-113s, 124-148s, 941-996s, 2668-2692s）
- 原片行1闪回场景（2603-2650s）成功通过
