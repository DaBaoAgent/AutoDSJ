# 字幕/剧本混合检索、CAM++ 与父段序列解码

## 正式数据流

`SRT＋场景图＋审校剧本 → 事件文本索引 → BM25＋text-embedding-v4 → CAM++可选声纹 → 父段Viterbi → 候选驱动视觉复核`

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

## 30～60帧选择性复核

- `shadow-match` 生成 `_selective_visual_plan.json`；默认45帧，最少30、最多60。
- 优先复核动作/非说话状态、Top-2事件分差小的歧义镜头和人工范围；同时为每个完整大场景至少保留中心覆盖点。
- `autodsj.py visual` 按不规则时间点抽帧。计划时间与 `_source_visual_index.json.source_signature` 不一致时，旧索引自动判过期。
- 禁止把 `--interval` 密集抽帧用于正式链路；它只可用于明确的诊断实验。

## 验收

- `★ 分层影子匹配报告.json.matcher_schema == v3-hybrid-text-campplus-viterbi`。
- `planning_summary.sequence_decoder.decoded_groups == total_groups`。
- `planning_summary.unresolved == 0`。
- `30 <= _selective_visual_plan.json.frame_count <= 60`。
- 第二次相同内容重匹配应命中两类文本向量缓存；若仍慢，先查缓存签名而不是增加视觉帧。
