# Vigilath vs Profound — GEO/AEO 产品对比

> 整理日期：2026-06-17（基于当前代码库实态重写）
> 数据来源：本仓库代码与设计文档（`backend/`、`services/`、`docs/`）、tryprofound.com 各功能页（实测抓取）
> 说明：文中带"举例"字样的数字为说明性示例，非真实数据。
> 重要变更：上一版把 Vigilath 描述为"纯静态审计 + 推断派、无任何实测能力"。**该结论已过时**——当前代码里 Vigilath 已具备多引擎实测、情绪监测、UGC 舆情、竞品情报、Agent 自动化等能力。本版按实际代码校正。

---

## 0. 一句话定位

- **Vigilath**：GEO 审计 + 多引擎实测 + 舆情/竞品监测 + 8 阶段自动内容流水线，外加对话式 Agent。一套从"诊断 → 实测 → 生产 → 监测 → 闭环"的自助/半托管平台。面向独立创始人、中小团队、代运营，向上够企业托管。
- **Profound**（tryprofound.com）：企业级 AI 搜索营销平台，把 GEO/AEO 做成"理解—分析—生产—度量"全栈。面向大品牌和代理商。

> 注意：`profound.com` 是另一家做市场研究报告聚合的公司（MarketResearch.com 旗下），同名不同司。GEO/AEO 领头羊是 **tryprofound.com**。

**核心范式差异（校正后）：**
- **两家都做"测量 + 制造"**，区别在**数据规模与护城河**，而非"一家测、一家不测"。
- **Profound 的真壁垒**：① 13 亿+ 真实 AI 对话语料库（Prompt Volumes）；② 10 万+ 页 citation 基准网络；③ 每天对 9 个引擎 × 海量 prompt × 多地区跑的工程规模。这三条 Vigilath 没有对应量级。
- **Vigilath 的真实形态**：自己也去问 10+ 引擎（含国产豆包/通义/文心/元宝）、做 13 维情绪、爬雪球/微博/知乎做舆情、抽竞品、再用 8 阶段流水线生产铺设——只是没有 Profound 那种"沉淀语料 + 基准网"的数据飞轮。

一句话：**Profound 靠"海量沉淀数据 + 大规模日跑"卖企业高客单；Vigilath 靠"全栈打通 + 自助低门槛 + 国产引擎覆盖"做穷人版全家桶，缺的是数据规模而非能力维度。**

---

## 1. Vigilath 产品全貌

### 1.1 审计能力（入口，免费）
丢进一个网址 → 0–100 AI Visibility Score + 字母等级 → 25 类 / 100+ 信号的分类拆解 → 优先级修复清单。
代码：`backend/geo_checker/`（活跃 package，`checks.py` 2958 行 + `modes/`）。
五大审计主题：
1. Crawl access — robots.txt、llms.txt、sitemap.xml、.well-known、对 AI 爬虫的放行规则
2. Content extractability — SSR vs 客户端渲染、语义化 HTML、标题结构、内容占比
3. Meta & structured data — title/description/canonical、Open Graph、JSON-LD（Organization/FAQ/HowTo/Breadcrumb）
4. Authority & trust — 安全头、作者署名、品牌实体知识图谱、sameAs、跨平台存在
5. Answer-format readiness — 定义块、分步、对比表、优缺点、TL;DR、FAQ、可引用统计

**额外免费模式**：`--crawl-check`（读 nginx/access 日志统计 AI bot 抓取，对标 Profound Agent Analytics 的抓取端，`modes/crawl_check.py`）、`--crawl-test`（探测站点是否挡 AI 爬虫，`modes/crawl_test.py`）、`--authority-audit`（GitHub/npm/PyPI/Wikipedia 等站外权威信号）。

### 1.2 实测能力（付费，需 API Key）—— 上一版漏掉的关键部分
Vigilath **真的去问 AI 引擎并解析回答**，不止"审计网页就绪度"：
- `--citation-check`：经 Perplexity 测品牌是否被引用（`modes/citation.py`）
- `--ai-visibility`：多引擎综合可见度审计，解析露出/排名/framing（`modes/visibility.py`）
- `--entity`：品牌/产品/人物实体 GEO 审计（8 维）
- 引擎助手：`ai.py` 内 `_query_perplexity / _query_openai / _query_anthropic / _query_deepseek / _query_doubao / _query_qwen / _query_wenxin / _query_yuanbao / _query_grok`
- 浏览器实测：`services/browser-service/app/engines/` 下 10 个引擎的浏览器自动化（chatgpt/claude/copilot/gemini/grok/deepseek/豆包/通义/文心/元宝）
- `_classify_framing(answer, brand)` 对 AI 描述做正/中/负 framing 分类

**引擎覆盖（实际 10+，比上一版列的更广）**：ChatGPT、Claude、Perplexity、Gemini、Copilot、Grok、DeepSeek + **国产四件套**豆包 / 通义千问 / 文心一言 / 元宝。**国产引擎覆盖是对 Profound 的差异化优势**（Profound 偏英文引擎，无国产）。

### 1.3 舆情与竞品监测（Sentinel + 情绪体系）—— 上一版完全没提
- **Sentinel 服务**（`services/sentinel-service/`）：对 UGC/财经平台做持续监测——东财股吧、新浪个股、财联社电报、雪球（StealthyFetcher 过 WAF）、微博/知乎（登录墙直采），加搜索引擎补盲。产出每日简报（含 PDF 导出）。
- **13 维情绪模型**（`backend/geo/models/sentiment.py`）：sentiment_label/score、emotions、topics、entities、stance、intent、factuality、risk_level、risk_signals、influence_potential、hidden_meaning 等，LLM 驱动（`sentiment_pipeline.py`）。
- **竞品情报**（`modes/competitive_intel.py`、`visibility.py`）：问引擎抽取同现竞品、品牌定位（leader/option/niche）、相对声量。
- 对标参考：`docs/sentiment-gap-analysis-vs-wisersone.md`、`docs/竞争情报功能方案.md`。

### 1.4 8 阶段 GEO 自动化内容铺设全流程（已落地为产品，非营销概念）
代码映射见 `docs/AI-telemetry/admin-workflow-8stage-pipeline.md`；`/workbench/cockpit` 有实时进度。
分 4 大类：采集 → 生产 → 观测 → 自适应。

| # | 阶段 | 类别 | 做什么 | 代码落点 |
|---|------|------|--------|----------|
| 1 | 种子提示 | 采集 | 从品牌资料抽种子问题 | `/api/ai-telemetry/topics/admin` |
| 2 | 意图扩展 | 采集 | 大模型扩展候选话题（4D，200+） | `query_expander.expand_one_scene()` |
| 3 | 提示采集/聚类 | 采集 | 聚类问题 + 输出关键词谱 | `solution_generator.py` |
| 4 | 内容生成 | 生产 | 每 query 自动生成 25+ 草稿 | `content_generator.py` |
| 5 | 提示分发 | 生产 | 选平台、发布草稿 | `ContentReview.tsx → publishDoc` |
| 6 | 引擎抓取 | 观测 | 10+ 引擎跑 query | `browser-service` |
| 7 | 遥测采集 | 观测 | 每日采品牌词排名/提及/被引 | telemetry-service + sentinel-service |
| 8 | 强化闭环 | 自适应 | 监测反馈触发重诊断/重生成，回 1 | 监测看板 |

= 把 GEO 从"一次性优化"做成"持续运营的增长引擎"。

### 1.5 对话式 Agent（已上线）—— 上一版没提
`backend/geo/agent/`：Pydantic AI Agent（DeepSeek 模型）+ 19 个工具（create_topic / run_geo_checks / probe_visibility / draft_articles / publish_drafts / configure_sentiment …），多租户隔离（account_id），SSE 对话 + 异步诊断，IM 集成（飞书/企微 bot）。设计见 `docs/实现设计-Agent.md`、`docs/最佳方案-GEO优化Agent.md`，称已部署 vm02:8010。

### 1.6 引擎自愈（browser-agent）—— 上一版没提
`docs/引擎自愈-browser-agent-设计.md`：选择器外置（Phase 1 已上线，`engine_selectors.py`）→ 健康哨兵检测 FAIL 后 LLM 重新发现选择器 + canary 验证门（Phase 2 开发中）。应对引擎前端频繁改版导致的浏览器实测失效。

### 1.7 定价（透明、低门槛）
代码/文档：`docs/会员功能免费与付费功能项目列表.md`。

| 套餐 | slug | 价格 | 关键 |
|------|------|------|------|
| 匿名 | — | $0 | 5 次检查 / 3 runs/月 |
| Free | `free` | $0 | 注册版，25 类审计 + AI 可见度分，3 runs/月 |
| Pro | `pro` | $9.99/mo | 全量审计、历史、修复清单排序、20 runs/月 |
| Starter | `starter` | $999/mo | 托管 GEO，解锁 Fix 建议，含 LLM 优化服务 |
| Growth | `growth` | $2,500/mo | + PR、付费内容位、排名位 |
| Scale | `scale` | 定制 | 企业 GEO、声誉管理、专属顾问 |

支付：信用卡（Stripe）+ MoltsPay。Managed 服务（$999+）才动手帮你实施修复 + 内容铺设。

---

## 2. Profound 产品全貌（四面包抄的测量体系）

覆盖引擎：ChatGPT、Perplexity、Claude、Gemini、Grok、Copilot、Meta AI、DeepSeek、Google AI Overviews（偏英文引擎，**无国产豆包/通义/文心/元宝**）。
平台分两层：**Monitor（监测）** = Answer Engine Insights / Prompt Volumes / Shopping / Agent Analytics；**Create（生产）** = Agents。

### 2.1 Answer Engine Insights —— "AI 现在怎么谈你的品牌"（回答端）
旗舰监测模块，持续实测各引擎对你品牌的真实表现：
- **Visibility Score / Share of Voice**：出现频率 + 相对竞品的声量占比
- **Sentiment & Keyword Insights**：AI 描述你时的情绪 + 反复出现的主题叙事
- **Citation Authority**：AI 回答时引用了哪些站当信源，并追踪其权威度
- **Trends Over Time**：按时间/地区/语言/平台看可见度变化
- **Competitive Benchmarking + Platform Comparisons**

> 对照 Vigilath：§1.2 的 `--ai-visibility` + §1.3 的情绪/竞品已覆盖相近维度，差距在**跑的规模与持续性**（Profound 每天大规模跑、取分布），不在"有没有这个能力"。

### 2.2 Prompt Volumes —— "数百万人真实在问 AI 什么"（需求端 / 最强护城河）
核心是一个 **13 亿+（1.3B+）真实 AI 对话语料库**：
- 给你品类里人们真实问 AI 的问题 + 每个话题的"提问量"
- 按平台/受众/人群切片（量级示例：ChatGPT 2.1m、Perplexity 90k、Copilot 60k）
- 按量排优先级，并给追踪列表做 Prompt Volume Validation

**护城河（仍然成立）**：这种真实 AI 查询量数据需长期大规模采集。Vigilath 有 `ai_telemetry` 追踪体系（topics/runs/responses/query_hits + `query_expander.py`），能积累**自家账户**的 query→命中数据，但**没有 Profound 那种跨全网的 13 亿对话语料库**。这是两家最实质的差距。

### 2.3 Agent Analytics —— "AI 爬虫怎么抓你的站"（抓取端）
装在你网站基础设施上的服务器/CDN 日志侧分析（AWS/Cloudflare/Fastly/Vercel/WordPress…）：
- AI Crawler Visibility / Attribution & Traffic / Content Performance
- Benchmarking：跟 Profound Network 的 10 万+ 页比 citation，每日刷新
- Submit to AI Search、企业级 SOC 2 Type II / SSO / RBAC / GDPR

> 对照 Vigilath：`--crawl-check` 能读日志识别 AI bot（GPTBot/ClaudeBot…），覆盖"抓取端"基础诊断；但**没有** ① 跨客户聚合的 10 万+ 页基准网、② CDN 直连集成、③ 真人流量归因、④ 企业合规认证。

### 2.4 Shopping —— "你的产品在 ChatGPT 购物里怎么被推荐"（交易端）
专做 AI 电商导购可见性，主打 ChatGPT Shopping：Shopping Visibility / Attribute Accuracy / Shopper Sentiment / SKU-Level / Shopping Mode Rate / Merchant Layer + 产品 Feed 修复。

> 对照 Vigilath：**完全没有**。无 shopping/SKU/merchant 模块（仅 Stripe/MoltsPay 支付处理，非购物可见性）。这是 Profound 明确领先、Vigilath 空白的一块。

### 2.5 Agents（生产端）
自主营销 agent：AEO FAQ 生成、Brand/Content/Demand Gen Agent，模块化可编排。

> 对照 Vigilath：§1.5 的 Pydantic AI Agent（19 工具）+ §1.4 的 8 阶段流水线对应这部分。Vigilath 更"打包成一条流水线"，Profound 更"乐高式可编排"。

### 2.6 商业 / 品牌
VC 重注、企业客户案例、自办行业大会（Zero Click 2026）、品类定义者。定价不公开，Demo + 企业合同，高客单。

---

## 3. 逐维度对比（校正版）

| 维度 | Vigilath | Profound |
|------|----------|----------|
| 目标客户 | Solo / 中小 / 代运营自助，向上够企业托管 | 企业品牌 + 代理商，销售驱动（Demo） |
| 核心能力 | 审计 + 多引擎实测 + 舆情/竞品 + 8 阶段生产 + Agent | 真实对话监测 + 需求数据 + 爬虫分析 + 电商 + Agents |
| 是否真去问引擎 | **是**（10+ 引擎，API + 浏览器） | 是（9 引擎，大规模日跑） |
| 数据护城河 | 自家 `ai_telemetry` 追踪库（账户级） | **13 亿对话语料库 + 10 万+ 页 citation 基准网（全网级）** |
| 引擎覆盖 | 上述 + **国产豆包/通义/文心/元宝**（含中文生态） | 更广的英文引擎 + Meta AI/AI Overviews，**无国产** |
| 情绪分析 | **有**（13 维 LLM 模型 + UGC 舆情） | 有（回答端情绪） |
| 竞品/SoV | **有**（`competitive_intel`） | 有（更成熟的基准） |
| UGC/舆情监测 | **有**（Sentinel：雪球/微博/知乎/财经） | 偏 AI 回答端，非 UGC 舆情 |
| 抓取端/日志 | 有（`crawl-check` 日志 + `crawl-test`） | 有（CDN 直连 + 10 万页基准 + 流量归因，更强） |
| 内容自动化 | 8 阶段闭环，打包成产品 + Agent | Agents，模块化可编排 |
| 电商/Shopping | **无** | 有（ChatGPT Shopping 专模块） |
| 流量归因 | 无（仅 bot 抓取识别） | 有（AI 曝光接真实流量） |
| 引擎自愈 | 有（选择器外置 + LLM 自愈，Phase 2） | 未公开 |
| 价格 | $0 / $9.99 / $999 / $2500 / 定制，透明 | 不公开，企业合同，高客单 |
| 合规 | 未提 | SOC 2 Type II / SSO / RBAC / GDPR |
| 成熟度 | 全栈已搭，规模/案例待积累 | 重融资、企业案例、自办大会、品类定义者 |

---

## 4. 具体例子：Profound 怎么做 Shopping Visibility（基线可见度）

> 场景：你是跑鞋 DTC 品牌 "StriDe"，想知道消费者在 ChatGPT 问买跑鞋时，ChatGPT 推不推你、排第几。
> （数字为举例说明，非真实数据。）

1. **确定购物触发型提问集**：从 Prompt Volumes 真实对话数据里挑出会触发购物界面、且有量的 prompt，几十到几百条。如 "best running shoes for marathon training"、"affordable running shoes under $120"。
2. **大规模重复实测**：拿这批 prompt 反复问 ChatGPT 购物模式（同条多次、跨地区/语言），取分布——这是"实测"的关键。
3. **解析每次回答出现了谁**：本次出现哪些品牌/产品、你排第几、同时出现的竞品（HOKA/Brooks/On）。
4. **算出基线可见度**（举例）：
   - 80 条 prompt × 每条问 20 次 = 1600 次回答
   - StriDe 被推 320 次 → Shopping Visibility = 320/1600 = **20%**
   - Share of Voice：StriDe 占 **12%**（HOKA 31%、Brooks 24%）
   - prompt 级：under-$120 类出现率 45%，marathon 类仅 6%
   - SKU 级：几乎都推 StriDe Cloud，旗舰 StriDe Pro 几乎不出现
   → 这个基线以后每周重测看涨跌。
5. **诊断为什么低 → 连到动作**：修 Feed 品类/材质/价格字段、补结构化标记、优化弱势 prompt 描述、补官网直购商户信息。

**对照 Vigilath**：§1.2 的 `--ai-visibility` 已能"真去问引擎、数你被提及/排第几、抽竞品"，**机制同源**；差距是 Vigilath 没有 ① 购物界面/SKU/Merchant 这层电商特化解析、② Prompt Volumes 那种"按真实提问量选 prompt"的输入。所以 Vigilath 能做通用可见度实测，但做不了 Shopping 这种交易端特化基线。

---

## 5. 总结判断（校正版）

- **不完全是正面竞品，但能力重叠比上一版认为的大得多**。两家都"测量 + 制造"，差在数据规模与企业化程度。
- **Profound 最难复制的**：① Prompt Volumes（13 亿真实对话）；② 10 万+ 页 citation 基准网；③ 每天 9 引擎大规模跑的工程化 + 企业合规。这是"数据飞轮 + 规模"护城河，Vigilath 短期造不出。
- **Vigilath 已经具备、上一版误判为"没有"的**：多引擎实测（含国产）、13 维情绪、UGC 舆情（Sentinel）、竞品情报、日志/爬虫诊断、8 阶段流水线（已落地）、对话 Agent、引擎自愈。
- **Vigilath 真正的差异化优势**：① **国产引擎覆盖**（豆包/通义/文心/元宝 + 雪球/微博/知乎舆情）——Profound 完全没有的中文生态；② 全栈打通"诊断→实测→生产→监测→闭环"且自助低门槛；③ Agent 把全流程会话化。
- **Vigilath 真正的短板（精确化后）**：
  1. **数据规模**：无全网对话语料库、无跨客户 citation 基准网，实测是"按需小批量"而非"每天海量取分布"。
  2. **电商/Shopping**：完全空白。
  3. **流量归因 + 企业合规**：无真人流量归因、无 SOC 2/SSO/RBAC，难接大客户日志。
  4. **规模化证据**：缺"实测被引用率从 X 涨到 Y"的公开硬案例。
- **战略位**：坐稳"**中文生态版 + 自助全家桶**"——用国产引擎覆盖 + 舆情 + 自动内容工厂打 Profound 够不到的中文中小市场；同时补 Shopping、流量归因、企业合规，才能向上够企业客单。

---

## 6. Profound 四大模块的实现机制（如何实现）+ Vigilath 对位

> 关键分野：Profound 4 模块里 3 个是"主动探测"，1 个"被动日志"，外加 1 条"沉淀语料库"。Vigilath 现在**主动探测**和**被动日志**两条都有了对位实现，**只差"沉淀语料库"这条全网级数据线**。

### 6.1 Answer Engine Insights —— 主动探测 + 解析
核心叫 **Daily Prompt Execution**：配一批 prompt → 每天自动问 ChatGPT/Perplexity/Gemini/AI Overviews（带地区/语言/persona）→ 每次抓回答正文 + citation + 前端卡片 → 解析露出/排名/SoV/情绪/citation。
子能力 **Query Fan-Out**：把引擎内部 RAG 的子查询逆向出来，给 share%，分析 word transformation / freshness pressure；用 Fetchable/Chosen/Extractable 三道闸评内容。

> **Vigilath 对位**：`modes/visibility.py` + `ai.py` 已做"问引擎→解析露出/排名/framing"；`browser-service` 抓前端结果。**未做**：Query Fan-Out 子查询逆向、每日大规模取分布、freshness/word-transformation 分析。

### 6.2 Prompt Volumes —— 靠对话语料库（护城河）
核心是 **13 亿+ 真实 AI 对话语料库**。在语料上做关键词/话题聚合 → 每话题"提问量"；驱动 prompt 推荐 + 给追踪列表做 Volume Validation。13 亿对话怎么来的官网未披露。

> **Vigilath 对位**：`backend/geo/models/ai_telemetry.py`（topics/runs/responses/query_hits）+ `query_expander.py` 能积累**账户级** query→命中数据，并做意图扩展。**根本差距**：没有跨全网的真实对话语料库，量级与"提问量"信号无法对标。**这条是两家最实质、最难追的差距。**

### 6.3 Agent Analytics —— 被动读日志
集成在 CDN/服务器日志层（无需埋 JS），点名直连 AWS/Akamai/Cloudflare/Fastly/GCP/Netlify/Vercel/WordPress；按 UA 识别 AI 爬虫、统计抓取频率/报错/被挡、真人流量归因；跟 10 万+ 页 Profound Network 比 citation；Submit to AI Search；SOC 2/SSO/RBAC。

> **Vigilath 对位**：`modes/crawl_check.py` 读日志识别 AI bot（GPTBot/ClaudeBot…）、`crawl_test.py` 探可达性。**未做**：CDN 直连集成、跨客户 10 万页基准网、真人流量归因、企业合规、Submit to AI Search。

### 6.4 Shopping —— 主动探测的电商特化版
机制同 6.1，针对购物：跑购物触发 prompt、抓 ChatGPT 商品卡 → 解析产品/SKU/排序/描述/结账入口 → 算 Shopping Visibility / Mode Rate / Attribute Accuracy / SKU-Level / Merchant Layer → 修 Feed。

> **Vigilath 对位**：**无**。这是 Vigilath 最干净的空白。

### 6.5 实现方式总表（含 Vigilath 对位）
| 模块 | 数据线 | Profound 采集方式 | Vigilath 现状 |
|------|--------|-------------------|----------------|
| Answer Engine Insights | 主动探测引擎 | 每日跑 prompt，抓回答+引用 | ✅ 有基础实测（`visibility.py`），❌ 无 Fan-Out/日跑取分布 |
| Prompt Volumes | 沉淀语料库（13 亿对话） | 持有/采集真实对话 | ❌ 无全网语料库，仅账户级追踪（`ai_telemetry`） |
| Agent Analytics | 被动读日志 | CDN/服务器日志集成 | ⚠️ 有日志/可达性诊断，❌ 无 CDN 直连/基准网/流量归因/合规 |
| Shopping | 主动探测 ChatGPT | 跑购物 prompt，抓商品卡 | ❌ 完全空白 |
| （Vigilath 独有）UGC 舆情 | 主动爬 UGC | — | ✅ Sentinel：雪球/微博/知乎/财经 + 13 维情绪 |
| （Vigilath 独有）国产引擎 | 主动探测 | — | ✅ 豆包/通义/文心/元宝浏览器实测 |

**最值得记**：Profound 的壁垒 = "主动探测的工程规模" + "13 亿对话语料库" + "10 万页基准网"。Vigilath 已补上"主动探测能力本身"和"被动日志诊断"，并在中文生态（国产引擎 + UGC 舆情）上反超；**仍缺的是数据规模（语料库/基准网）、电商特化、流量归因、企业合规**。差距从上一版的"能力维度差"收敛为"数据规模与企业化差"。

---

## 7. 对 Zen7 / MoltsPay 的启发（待办）

- 现在在 Moltbook / Dev.to / Farcaster 手动铺内容引流，可直接复用 Vigilath 已有的 8 阶段流水线 + Agent（`backend/geo/agent/` 19 工具）把"种子词→生成→分发→监测"自动化，而非从零搭。
- 可用 Vigilath 现成的 `--ai-visibility` / Sentinel，实测 moltspay.com、juai8.com 当前 AI 可见性 + 舆情位置；国产引擎覆盖正好契合中文市场。
- 若要逼近 Profound：优先补 ① 实测的"每日大规模取分布 + Query Fan-Out"、② 一条能沉淀的对话/查询语料线、③ Shopping 特化解析（若涉及电商）。
