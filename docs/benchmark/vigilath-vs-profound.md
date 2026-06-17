# Vigilath vs Profound — GEO/AEO 产品对比

> 整理日期：2026-06-17
> 数据来源：vigilath.com（含首页 8 阶段流程截图）、tryprofound.com 各功能页（实测抓取）
> 说明：文中带"举例"字样的数字为说明性示例，非真实数据。

---

## 0. 一句话定位

- **Vigilath**：自助式 GEO 审计工具 + 8 阶段自动化内容铺设流水线。面向独立创始人、中小团队、代运营。
- **Profound**（tryprofound.com）：企业级 AI 搜索营销平台，把 GEO/AEO 做成"理解—分析—生产—度量"全栈。面向大品牌和代理商。

> 注意：`profound.com` 是另一家做市场研究报告聚合的公司（MarketResearch.com 旗下），同名不同司。GEO/AEO 领头羊是 **tryprofound.com**。

**核心范式差异（最关键一条）：**
- **Profound = 测量派**：真的去查 ChatGPT/Perplexity/Gemini 等引擎，统计品牌在数百万真实 AI 对话里被怎么提及、被谁引用、情绪正负，还有独家的真实提问量数据。
- **Vigilath = 推断 + 制造派**：审计你网页的技术就绪度，打 0–100 分；再用 8 阶段流水线自动生产内容、铺渠道、监测收录。

一句话：**Profound 告诉你"AI 现在怎么看你"（基于真实数据）；Vigilath 帮你"造出能被 AI 引用的内容并铺出去"。**

---

## 1. Vigilath 产品全貌

### 1.1 审计能力（入口）
丢进一个网址 → 0–100 AI Visibility Score + 字母等级 → 25 类 / 100+ 信号的分类拆解 → 优先级修复清单。
五大审计主题：
1. Crawl access — robots.txt、llms.txt、sitemap.xml、.well-known 清单、对 AI 爬虫的放行规则
2. Content extractability — SSR vs 客户端渲染、语义化 HTML、标题结构、内容占比
3. Meta & structured data — title/description/canonical、Open Graph、JSON-LD（Organization/FAQ/HowTo/Breadcrumb）
4. Authority & trust — 安全头、作者署名、品牌实体知识图谱、sameAs、跨平台存在
5. Answer-format readiness — 定义块、分步、对比表、优缺点、TL;DR、FAQ、可引用统计

### 1.2 8 阶段 GEO 自动化内容铺设全流程（真正的杀手锏）
定位："种子词丢进去，闭环跑，每天自检，品牌资料填一次，后续全程自动。"
分 4 大类：采集 → 生产 → 观测 → 自适应。

| # | 阶段 | 类别 | 做什么 |
|---|------|------|--------|
| 1 | 种子提示 | 采集 | 从品牌资料抽种子问题，自动填一次 |
| 2 | 意图扩展 | 采集 | 大模型扩展候选话题，控制长尾覆盖（200+ 话题） |
| 3 | 提示采集 | 采集 | 聚焦各答案引擎，监测问题聚类（约 100 个高频问题） |
| 4 | 内容生成 | 生产 | 大模型生成品牌相关内容/短句 |
| 5 | 提示分发 | 生产 | 投放到主流商城网络、知乎、行业媒体等多渠道 |
| 6 | 引擎抓取 | 观测 | 等主流大模型完成对内容的抓取/收录 |
| 7 | 遥测采集 | 观测 | 每日采集品牌词排名、提及、被引情况 |
| 8 | 强化闭环 | 自适应 | 精炼种子词，重新迭代扩展，回到第 1 步 |

底部定位"品牌增长引擎"：一份品牌资料 → AI 自动扩展监测圈层 → 生成内容矩阵 → 走完审核与发布全流程。
= 把 GEO 从"一次性优化"做成"持续运营的增长引擎"。

### 1.3 定价（透明、低门槛）
| 套餐 | 价格 | 检查次数/月 | 关键 |
|------|------|-------------|------|
| Free | $0 | 3 | 基础 25 类审计 + AI 可见度分 |
| Pro | $9.99/mo | 20 | 全量审计、历史、修复清单、PDF 导出 |
| Starter | $999/mo | 不限 | 托管 GEO、AI 收录规格、文案合规 |
| Growth | $2,500/mo | 不限 | Starter + 付费 SEO 内容位、优先技术支持 |
| Scale | 定制 | 不限 | 企业 GEO、声誉管理、PR、专属顾问 |

支付：信用卡（Stripe）。Managed 服务（$999+）才动手帮你实施修复 + 内容铺设。

---

## 2. Profound 产品全貌（四面包抄的测量体系）

覆盖引擎：ChatGPT、Perplexity、Claude、Gemini、Grok、Copilot、Meta AI、DeepSeek、Google AI Overviews。
平台分两层：**Monitor（监测）** = Answer Engine Insights / Prompt Volumes / Shopping / Agent Analytics；**Create（生产）** = Agents。

### 2.1 Answer Engine Insights —— "AI 现在怎么谈你的品牌"（回答端）
旗舰监测模块，持续实测各引擎对你品牌的真实表现：
- **Visibility Score / Share of Voice**：出现频率 + 相对竞品的声量占比
- **Sentiment & Keyword Insights**：AI 描述你时的情绪（好/中/差）+ 反复出现的主题叙事
- **Citation Authority**：AI 回答你这个品牌时引用了哪些网站当信源，并追踪其权威度（→ 告诉你该去搞定哪几个站）
- **Trends Over Time**：按时间/地区/语言/平台看可见度变化
- **Competitive Benchmarking + Platform Comparisons**：跟竞品比，看同品牌在各引擎间差异
本质：把"AI 怎么看你"从黑盒变成可量化仪表盘。

### 2.2 Prompt Volumes —— "数百万人真实在问 AI 什么"（需求端 / 最强护城河）
传统 SEO 工具没有的数据，从答案引擎数百万真实对话提取关键词量、话题趋势、意图信号：
- 给你品类里人们真实问 AI 的问题 + 每个话题的"提问量"
- 按平台/受众/人群切片（量级示例：ChatGPT 2.1m、Perplexity 90k、Copilot 60k）
- 帮你判断哪些 prompt 值得追踪，按量排优先级
= "AI 时代的关键词规划师"，但数据来自真实 AI 对话。
**护城河**：这种真实 AI 查询量数据需长期大规模采集，新玩家短期造不出来。Vigilath 完全没有。

### 2.3 Agent Analytics —— "AI 爬虫怎么抓你的站"（抓取端）
装在你网站基础设施上的服务器/流量侧分析：
- **AI Crawler Visibility**：哪些 AI bot、何时、多频繁来抓
- **Attribution & Traffic**：多少真人访客从 AI 搜索转化而来
- **Content Performance**：哪些页面常被 AI 回答引用
- **Benchmarking**：跟 Profound 网络里 10 万+ 页面比 citation 表现，每日刷新
- **Submit to AI Search**：把新内容直接推给 AI 爬虫加快收录
- 企业级：SOC 2 Type II、SSO、RBAC、GDPR
本质：AEI 看"回答端"，Agent Analytics 看"抓取端"，两头夹。

### 2.4 Shopping —— "你的产品在 ChatGPT 购物里怎么被推荐"（交易端）
专做 AI 电商导购可见性，目前主打 ChatGPT Shopping：
- **Shopping Visibility**：产品在 ChatGPT 购物类回答里的基线可见度
- **Attribute Accuracy**：AI 怎么给产品归类、描述
- **Shopper Sentiment**：AI 购物描述如何影响买家感知
- **SKU-Level Analysis**：单 SKU 的引用、关键词
- **Shopping Mode Rate**：品类里多少查询触发 ChatGPT 购物界面
- **Merchant Layer**：哪些第三方零售商/自营店掌控你品牌的结账入口，份额怎么分
- 可落地：产品 Feed + 结构化数据修复、商户优化

### 2.5 Agents（生产端）
自主营销 agent：AEO FAQ 生成、Brand Agent、Content Agent、Demand Gen Agent。模块化、可编排——对应 Vigilath 8 阶段流水线的"生产"部分，但更像乐高、可自由组合。

### 2.6 商业 / 品牌
VC 重注、企业客户案例、自办行业大会（Zero Click 2026）、品类定义者。定价不公开，Demo + 企业合同，高客单。
（具体融资数字未现查，按"重融资、企业级"定性。）

---

## 3. 逐维度对比

| 维度 | Vigilath | Profound |
|------|----------|----------|
| 目标客户 | Solo / 中小 / 代运营，自助 | 企业品牌 + 代理商，销售驱动（Demo） |
| 核心能力 | 静态审计 + 自动内容铺设 | 真实对话监测 + 需求数据 + 爬虫分析 + 电商 |
| 数据性质 | 推断（审计网页就绪度） | 实测（真问引擎，测真实露出/引用） |
| 内容自动化 | 8 阶段闭环，打包成产品 | Agents，模块化可编排 |
| 引擎覆盖 | ChatGPT/Perplexity/Claude/Gemini/Copilot | 上述 + Grok/Meta AI/DeepSeek/AI Overviews（更广） |
| 数据护城河 | 几乎没有，评分是启发式 | Prompt Volumes + 10 万+ 页 citation 基准网络 |
| 电商/Shopping | 无 | 有（ChatGPT Shopping 专模块） |
| 流量归因 | 无 | 有（Agent Analytics 把 AI 曝光接真实流量） |
| 价格 | $0 / $9.99 / $999 / $2500 / 定制，透明 | 不公开，企业合同，高客单 |
| 合规 | 未提 | SOC 2 Type II / SSO / RBAC / GDPR |
| 成熟度 | 新、轻、暂无公开大客户 | 重融资、企业案例、自办大会、品类定义者 |

---

## 4. 具体例子：Profound 怎么做 Shopping Visibility（基线可见度）

> 场景：你是跑鞋 DTC 品牌 "StriDe"，想知道消费者在 ChatGPT 问买跑鞋时，ChatGPT 推不推你、排第几。
> （数字为举例说明，非真实数据。）

1. **确定购物触发型提问集**：从 Prompt Volumes 真实对话数据里挑出品类里会触发购物界面、且有量的 prompt，几十到几百条。如 "best running shoes for marathon training"、"affordable running shoes under $120"。
2. **大规模重复实测**：拿这批 prompt 反复问 ChatGPT 购物模式（同条多次、跨地区/语言），因为 LLM 输出有随机性，要取分布——这是"实测"而非"审计网页"的关键。
3. **解析每次回答出现了谁**：本次出现哪些品牌/产品、你排第几（第 1 卡 vs 第 5 卡）、同时出现的竞品（HOKA/Brooks/On）。
4. **算出基线可见度**（举例）：
   - 80 条 prompt × 每条问 20 次 = 1600 次回答
   - StriDe 被推 320 次 → Shopping Visibility = 320/1600 = **20%**
   - Share of Voice：所有品牌露出里 StriDe 占 **12%**（HOKA 31%、Brooks 24%）
   - prompt 级：under-$120 类出现率 45%（强），marathon 类仅 6%（弱）
   - SKU 级：几乎都推 StriDe Cloud，旗舰 StriDe Pro 几乎不出现
   → 这个 20% / 12% SoV / 各 prompt 分布就是基线，以后每周重测看涨跌。
5. **诊断为什么低 → 连到动作**：
   - Attribute Accuracy：ChatGPT 把 StriDe Pro 描述成"休闲鞋"而非"竞速鞋" → marathon 类不被选 → 根因是 Feed/结构化数据品类字段标错
   - Shopping Mode Rate：品类只有 40% 查询触发购物界面 → 也得做内容型 AEO
   - Merchant Layer：被推时 70% 结账落第三方零售商，仅 30% 官网直购 → 你对价格/描述失控
   - 动作清单：修 Feed 品类/材质/价格字段、补结构化标记、优化弱势 prompt 描述、补官网直购商户信息

**一句话**：Profound 的 Shopping Visibility = 拿真实购物 prompt 反复实测 ChatGPT → 数出你被推几次、排第几、哪款 SKU → 得出可追踪基线分 → 拆到属性/触发率/商户层找根因 → 给修 Feed 的具体动作。

对照：Vigilath 审计你产品页"够不够 AI 友好"（推断你应该能被引用）；Profound 真去问 ChatGPT，实测你"到底被不被推、排第几"。一个看就绪度，一个看真实战绩——这是 Profound 能卖企业高客单的根本。

---

## 5. 总结判断

- **不完全是正面竞品**。Profound = 测量 + 洞察 + 企业级编排，卖给有预算、要看 ROI 数据的大品牌；Vigilath = 审计 + 自动铺内容，卖给买不起 Profound、想低成本自跑的中小玩家。
- **格局**：Profound = 测量(四端) + 生产(Agents) 双全，企业级；Vigilath = 轻量审计 + 自动生产，中小级。
- **Profound 最难复制的**：Prompt Volumes（真实 AI 提问量）+ 10 万+ 页 citation 基准网络这种"数据飞轮"。
- **Vigilath 的差异化机会**：Profound 强在"看见"但基本不替你"动手生产"；Vigilath 的 8 阶段流水线是"自动产出 + 自动铺"。做扎实就能占位"穷人版 Profound + 自动内容工厂"。
- **Vigilath 最大短板**：没有真实 AI 对话/引用数据。它说"提升可见性"却缺 Profound 那种"实测被引用率从 X 涨到 Y"的硬证据。补这块（哪怕小规模实测抽样）是最该补的可信度缺口。

---

## 6. Profound 四大模块的实现机制（如何实现）

> 关键分野：4 个模块里有 3 个是"主动探测"（自己去问引擎），只有 Agent Analytics 是"被动日志"（读你服务器）；而 Prompt Volumes 是唯一靠"沉淀的对话语料库"。这三条数据线 Vigilath 一条都没有。

### 6.1 Answer Engine Insights —— 主动探测 + 解析
核心叫 **Daily Prompt Execution（每日跑 prompt）**：
- 你配一批要追踪的 prompt（几十到几百条）
- Profound 每天自动拿这批去问 ChatGPT/Perplexity/Gemini/AI Overviews（自动化/无头或可用接口，带不同地区/语言/persona）
- 每次抓回三样：AI 回答正文、citation 信源链接、前端结果（商品卡/卡片）
- 解析：品牌出没出现/排第几 → Visibility Score / Rank / Share of Voice；回答正文做情绪+主题抽取 → Sentiment & Keyword（含幻觉/错误描述检测）；抽 citation URL → 进 Citation 模块
- 因 LLM 输出随机 → "每天 × 多次 × 多地区"取分布

子能力 **Query Fan-Out**（硬核）：答案引擎内部 RAG 把一个 prompt"扇出"成多条子查询去检索。Profound 把子查询逆向出来，给每条一个 share%，并分析：
- Word transformation：AI 改写时加/删/留了哪些词 → 内容该用哪些措辞
- Freshness pressure：有些引擎往子查询注入日期 token 偏好新内容 → 哪些页要勤更新
- 用"三道闸"评内容：Fetchable（爬得到）/ Chosen（被选为信源）/ Extractable（能被抽取）

### 6.2 Prompt Volumes —— 靠对话语料库（护城河）
核心是一个 **13 亿+（1.3B+）真实 AI 对话语料库**。跟 6.1 最大不同：前者"你指定 prompt 我去测"，这个"我本来就有海量真人对话"。
- 在语料上做关键词/话题聚合 → 每个话题在 AI 平台上的"提问量"（如 ChatGPT 2.1m / Perplexity 90k）
- 提取意图信号、趋势，按平台/受众/人群切片
- 驱动 data-driven prompt recommendation 引擎；并给 6.1 的追踪列表做 Prompt Volume Validation
- **诚实点**：这 13 亿对话具体怎么来的（panel/浏览器插件/合作数据源/采样）官网未披露。正是它最值钱、最难抄的地方。

### 6.3 Agent Analytics —— 被动读日志（另一条数据线）
不问引擎，装在你自己网站基础设施上：
- 集成在 **CDN / 服务器日志层，无需埋 JS、无需改代码**
- 点名直连：AWS、Akamai、Cloudflare、Fastly、Google Cloud、Netlify、Vercel、WordPress
- 机制：读服务器/CDN 日志，按 user-agent 识别 AI 爬虫（GPTBot/ClaudeBot/PerplexityBot/Google-Extended…），统计：哪个 bot 何时抓了哪些页/多频繁、有没有报错/响应慢/被 robots.txt 或 CDN 挡、JS 重渲染抓不到；真人流量从 AI 搜索的转化归因
- Benchmarking：跟 Profound Network（所有客户聚合的 10 万+ 页基准网）比 citation，每日刷新
- Submit to AI Search：主动把新内容推给爬虫，缩短"发布→被引用"
- 企业向：SOC 2 Type II / SSO / RBAC，才敢接大客户日志

### 6.4 Shopping —— 主动探测的电商特化版
机制跟 6.1 同源（主动跑 prompt 实测），针对购物特化：
- 跑"购物触发型 prompt"，抓 ChatGPT 商品卡/购物界面
- 解析产品、SKU、排序、描述、结账入口
- 算 Shopping Visibility / Shopping Mode Rate / Attribute Accuracy / SKU-Level / Merchant Layer
- 落地：修产品 Feed 字段 + 结构化标记

### 6.5 实现方式总表
| 模块 | 数据线 | 采集方式 | 核心技术活 |
|------|--------|----------|------------|
| Answer Engine Insights | 主动探测引擎 | 每日跑 prompt，抓回答+引用 | 露出/排名解析、情绪抽取、Query Fan-Out 逆向 |
| Prompt Volumes | 沉淀语料库（13 亿对话） | 持有/采集真实对话 | 大规模关键词聚合、意图/量化（护城河） |
| Agent Analytics | 被动读你的日志 | CDN/服务器日志集成 | bot UA 识别、爬取诊断、流量归因、基准网对比 |
| Shopping | 主动探测 ChatGPT | 跑购物 prompt，抓商品卡 | SKU/属性/商户层解析、Feed 诊断 |

**最值得记**：Profound 的真正壁垒 = "主动探测的工程规模"（每天对几十个引擎 × 海量 prompt × 多地区跑，还要扛 LLM 随机性）+ "13 亿对话语料库"。Vigilath 只做"审计你的网页"，这两条数据线都没有 —— 这就是为什么一个能卖企业高客单、一个只能卖 $9.99。

---

## 7. 对 Zen7 / MoltsPay 的启发（待办）

- 现在在 Moltbook / Dev.to / Farcaster 手动铺内容引流，本质就是人肉版 Vigilath 8 阶段流水线。可考虑用现有工具（video_gen、ddg_search、各平台 API）把"种子词→生成→分发→监测"几步半自动化。
- 可用 Vigilath / Profound 各自标准，实测 moltspay.com、juai8.com 当前 AI 可见性，看自己处在什么位置。
