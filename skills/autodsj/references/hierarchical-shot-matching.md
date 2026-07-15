# 完整场景地图与分层镜头匹配

## 目标

将解说匹配从“全集找一张语义分最高的帧”改为：

`完整大场景 → 解说父段连续性 → 事件块 → 物理镜头 → 动作瞬间`

大场景是硬边界，人物、SRT、动作和 embedding 只负责边界内排序。

## `_scene_map.json` 契约

```json
{
  "version": 4,
  "coverage_reviewed": true,
  "coverage_ranges": [[120.0, 2600.0]],
  "excluded_ranges": [[0.0, 120.0], [2600.0, 2780.0]],
  "scene_count": 2,
  "scenes": [
    {
      "name": "办公室-甲与乙争执",
      "ranges": [[120.0, 300.0]],
      "characters": ["甲", "乙"],
      "keywords": ["办公室", "争执"]
    },
    {
      "name": "餐厅-乙与母亲谈话",
      "ranges": [[300.0, 480.0]],
      "characters": ["乙", "母亲"],
      "keywords": ["餐厅", "母亲"]
    }
  ],
  "parent_scene_plans": {
    "1": [
      {"from_shot": 1, "to_shot": 5, "scene": "办公室-甲与乙争执"},
      {"from_shot": 6, "to_shot": 6, "scene": "餐厅-乙与母亲谈话"}
    ],
    "2": [
      {"from_shot": 1, "to_shot": 4, "scene": "餐厅-乙与母亲谈话"}
    ]
  },
  "overrides": [
    {"contains": "独特的解说短语", "scene": "餐厅-乙与母亲谈话", "range": [330.0, 350.0]}
  ]
}
```

### 完整性

1. 先用视觉索引、SRT 和原片抽查划出正片起止。
2. 从正片起点走到终点，每次地点、人物组合、时间或事件明显改变就建新大场景。
3. `coverage_ranges` 的每一秒必须在某个 `scenes[].ranges` 内。
4. 片头、片尾、插片广告等不参与匹配的区间明确写入 `excluded_ranges`，同时从 `coverage_ranges` 和场景 `ranges` 中剔除。
5. 只有逐段复核完成才可设 `coverage_reviewed: true`。

### 父段连续性

- 一个 `tts_parent_id` 必须从 `shot 1` 开始连续覆盖到最后一镜。
- 默认全段只有一个主场景。
- 只有“为下一段原片承上启下”的连续尾句可切到第二场景。
- 第二组镜头数必须少于主场景组。
- 禁止第三场景；禁止“1 句旧场景 + 余下全跳新场景”。这种文案应该重分段，或将抽象过渡句一起留在整段主场景。

## 候选匹配

1. 只读当前父场景范围内的事件块。
2. 用 SRT/审校剧本 BM25＋文本向量筛事件 Top-K，再叠加人物、动作族、说话状态、地点和可选 CAM++ 声纹。
3. 把同一父段的全部子句候选交给 Viterbi 全局解码，确定连续事件路径；禁止逐句贪心跨场景跳跃。
4. 在解码事件块内排物理镜头和多关键时间点，并用30～60帧选择性视觉做最终歧义复核。
5. 根据真实配音时长在该镜头/事件内扩展裁切窗。
6. 裁切起止不得越过大场景边界；候选不足就报 `unresolved`，不得全片兜底。
7. 第一轮把原片引用、广告和已分配解说镜头全部视为不可用，遍历所有候选选择全局新画面。
8. 只有第一轮完全无解时，父段计划或人工 override 才可第二轮复用已引用原片；广告和已分配解说镜头仍绝对不可用。
9. 预演必须报告 `strict_fresh` 与 `source_reuse_fallback`，不得用场景计划标签隐式放宽复用规则。

## 门禁

`backend/scene_map.py` 负责完整覆盖、场景数、父段计划和主/尾场景结构校验。`shadow-match` 将地图 SHA-256 写入接管预演；`anchored_pipeline.py` 在渲染前再校验哈希。

任一不满足必须停止：

- 场景地图不存在或不完整；
- 解说镜头没有唯一父场景；
- 主场景/尾句结构违规；
- 预演哈希与当前地图不同；
- `safe_to_render != true` 或 `unresolved != 0`；
- 文案、分镜、配音时长或原片冲突校验失败。

## 复跑命令

```powershell
& $PY autodsj.py shots --folder "<素材夹>"
& $PY autodsj.py events --folder "<素材夹>"
& $PY autodsj.py shadow-match --folder "<素材夹>"
& $PY autodsj.py run --folder "<素材夹>" --skip-visual --no-render --hierarchical-match
& $PY autodsj.py run --folder "<素材夹>" --skip-visual --hierarchical-match
```

`shots --force` 只在原片或转场阈值变化时使用。文案变化不需重跑视觉索引，但必须重跑脚本表和影子匹配。
