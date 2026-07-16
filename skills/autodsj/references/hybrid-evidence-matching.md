# 字幕/剧本混合检索、CAM++ 与父段序列解码

## 正式数据流

`SRT＋场景图＋审校剧本 → 结构化视觉意图 → 事件文本索引 → BM25＋text-embedding-v4 → CAM++可选声纹 → 父段Viterbi → 60～120帧通用复核 → 候选多帧对比`

## 文本主检索

- `backend/text_retriever.py` 为每个事件合并场景人物/关键词、事件字幕、重叠的 `source_clip` 剧本文字及稀疏视觉证据。
- BM25 用中文二元词和短完整词，保证角色名、动作和对白原句优先。
- `text-embedding-v4` 只补足概括与字面字幕之间的语义差。事件向量缓存为 `_event_text_embeddings.json`，解说/父段查询缓存为 `_query_text_embeddings.json`；内容签名变化才重算。
- API 异常必须回退 BM25＋精确人物/动作匹配，不能阻塞成片。

## CAM++ 声纹

- 安装 `requirements-audio.txt`，在剧集根或单集放 `_voices/<角色名>/*.wav|mp3|m4a|flac`。
- 运行 `autodsj.py voices --folder <单集>`，按原片 SRT 对白区间提取 CAM++ 向量，产出 `_source_voice_index.json` 和 `_voice_gallery.json`。不跑全片 VAD/聚类，适合 CPU 笔记本。
- 声纹只回答“这个事件里谁在说话”，不能替代人脸识别，也不能把候选带出父场景。
- 无参考音频时不能建立角色身份，报告 `voice_index=false` 时按未启用处理；不得把匿名说话区间当成角色。

## 父段全局序列

- `backend/sequence_decoder.py` 对每个 `continuity_group_id` 一次解码全部子句，不逐句贪心。
- 奖励同一事件、相邻事件和时间向前推进；重罚倒序、远距离跳跃和跨大场景。
- `backend/timeline_planner.py` 强优先 `sequence_selected` 候选；只有时长不足或素材冲突时才回退次选。
- 场景地图仍是最高硬约束：主场景整段连续，只有更短尾句组可承上启下。

## 60～120帧选择性云端复核

- `shadow-match` 生成 `_selective_visual_plan.json`；默认90帧，最少60、最多120。
- 优先复核动作/非说话状态、Top-2事件分差小的歧义镜头和人工范围；同时为每个完整大场景至少保留中心覆盖点。
- `autodsj.py visual` 按不规则时间点抽帧。计划时间与 `_source_visual_index.json.source_signature` 不一致时，旧索引自动判过期。
- 禁止把 `--interval` 密集抽帧用于正式链路；它只可用于明确的诊断实验。

## 验收

- `★ 分层影子匹配报告.json.matcher_schema == v3-hybrid-text-campplus-viterbi`。
- `planning_summary.sequence_decoder.decoded_groups == total_groups`。
- `planning_summary.unresolved == 0`。
- `60 <= _selective_visual_plan.json.frame_count <= 120`。
- 第二次相同内容重匹配应命中两类文本向量缓存；若仍慢，先查缓存签名而不是增加视觉帧。

## 候选多帧硬确认

- `narration_intent.py` 输出 `must_have`、`must_not_have`、`hard_requirements` 和 `temporal_type`。检索同义词不能自动升级为硬动作，尤其禁止把关系/心理隐喻当成可见动作。
- `selective_visual.py` 对明确动作候选优先加入前/中/后三帧；所有时间点必须在 source trim 和场景地图范围内。
- 完成的通用视觉计划必须按场景地图 SHA 锁定，避免视觉描述改变排序后产生“计划→索引→新计划”的循环失效；人工更新场景地图时锁定自动失效。
- `candidate_visual_review.py` 基础层在场景内比较最多三个候选；候选只有一个时仍可做硬确认，不得向相邻场景扩窗凑数。基础层 unresolved 后，二级复核把同一场景内候选扩大到五个。
- 角色名只取 InsightFace `known_people`。云端返回的姓名不能满足角色硬条件；角色、动作、地点、道具、否定项和最低置信度必须全部通过。
- `_candidate_visual_review.json` 对完整签名缓存；远端失败形成 `partial` 时，下次只抽取和重试失败组。
- 基础3候选×3帧仍 unresolved 时，只对这些段使用5候选×每候选7帧的 `_candidate_visual_escalation.json`；已通过段和120帧通用索引不得重跑。超大多图请求断开时，云端均匀降为每候选5帧、再降为3帧，本地身份核验仍保留7帧；基础请求断开时可降为每候选2帧、再降为1帧，但候选数量和身份硬门禁不变。
- 场景地图或视觉证据改变后，只能复用候选 ID 顺序与帧数完全一致的旧复核；候选列表有变化必须重新请求云端，不能仅按 segment_id 盲用缓存。
- 7帧后仍缺目标人物，结论应升级为“候选镜头错误”，下一步增加同一父场景内候选镜头，而不是继续增加同一错误镜头的帧数。
- 候选复核未完成或存在 `unresolved` 时，命令可以正常结束，但 `safe_to_render` 必须为 false。
