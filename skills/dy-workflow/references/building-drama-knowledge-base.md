# 给新剧建"分集剧情知识库"的做法

跑一部新剧前，先建两份参考让匹配/校验有剧情先验（本会话为《玫瑰的故事》建过，
见 `references/rose-story-knowledge.md` + `references/rose-story-episodes.json/.md`）：

1. **角色总表**（`references/<剧>-knowledge.md`）：演员·角色·昵称·关系、感情线/弧线、
   公司/地点、**名字变体**（视觉索引 OCR 常错，如 傅家明↔溥家明、方协文↔方谢文）。
   用于扩 `backend/visual_matcher.py::_NICKNAME_BRIDGE`（昵称→本名）。
2. **分集索引 JSON**（`references/<剧>-episodes.json`）：每集
   `{ep,title,arc,male_lead,characters,locations,summary}`。跑第N集取 `episodes_index[N-1]`，
   知道本集有谁/在哪/演什么；解说说"他/那个男人"时用 `male_lead` 判断指谁。

## 检索来源与工具

- 用户自备 Tavily key（问用户要，或复用会话里给过的）。**search**（有 answer+content）
  比 **extract** 稳，全 38 集分集剧情用电视猫（tvmao）逐集页。
- 权威站点：**维基百科**（结构化「演员/角色/介绍」表，最全）、豆瓣、电视猫（分集剧情）、
  HK01/TVB/痞客邦（港台分集）。

## 两个必踩的坑（都已验证）

1. **中文 UTF-8 编码**：git-bash/MSYS 里 `curl -d '{...中文...}'` 会把中文按 GBK 发出，
   Tavily 报 `Invalid JSON body: 'utf-8' codec can't decode`。**解决**：别用 shell 拼 JSON，
   写个 Python 小脚本用 `json.dumps(...).encode('utf-8')` + `urllib.request` POST，
   header `Authorization: Bearer <key>`。search 端点 `https://api.tavily.com/search`
   （body `{"query","search_depth":"advanced","include_answer":"advanced","max_results"}`），
   extract 端点 `https://api.tavily.com/extract`（body `{"urls":[...],"extract_depth":"advanced"}`）。
2. **tvmao 移动版分集页返回重复内容**：`m.tvmao.com/drama/<id>/episode/<x-N>` 靠 JS 加载正文，
   Tavily extract 只拿到默认页，**除第1集外全返回第2集的剧情**（会污染整个数据库！）。
   **解决**：用**桌面版** `www.tvmao.com/drama/<id>/episode/<x-N>`，逐集正确。
   **务必校验**：抽完后对比各集抽到的「### 第N集：标题」行，若大量重复=中招了，换桌面版重抽。

## 版权

分集剧情只做**自己归纳的事实要点**（人物/场景/关键情节），不照搬来源逐字原文。
