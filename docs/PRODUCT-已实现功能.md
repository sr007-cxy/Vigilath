# Vigilath 产品文档 —— 当前已实现功能

> 整理日期：2026-06-17
> 依据：**项目实际代码**（`backend/`、`services/`、`frontend/`、`scripts/`），非设计文档
> 口径：只列代码里有实质逻辑的功能；"部分实现/未实现"单独标注，不混入已实现清单
> 图例：✅ 完整实现　🟡 部分实现　⚠️ 仅框架/受限

---

## 0. 一句话定位

Vigilath 是一套 **GEO/AEO 全栈平台**：从「网站 AI 就绪度审计」→「多引擎真实露出实测」→「内容生产与铺设」→「AI 引用追踪 + 舆情监测」→「闭环优化」，并配套对话式 Agent、会员计费与 IM 推送。自助低门槛，向上够企业托管。

**已落地的五大产品线：**
1. **GEO 检查与优化**（网站审计 + 修复建议）
2. **AI 可见性 / 引用追踪**（10+ 引擎实测 + 品牌增长看板）
3. **舆情监测**（多源 UGC/财经爬虫 + LLM 分析）
4. **内容生产与发布**（扩词→生成→排期→发布→战报）
5. **对话式 GEO/舆情 Agent**（IM 集成 + 推送）

---

## 1. GEO 检查与优化（审计入口）

活跃代码：`backend/geo_checker/`（重构后的 package，`checks.py` 2958 行 + `modes/`）。

### 1.1 站内审计（免费）✅
- **25 类审计**，0–100 AI Visibility Score + 字母等级 + 分项细分。覆盖：
  - Crawl access：HTTPS、robots.txt（含 AI bot 白名单）、llms.txt、`/.well-known/`、sitemap.xml
  - 可抽取性：内容可提取度、DOM 渲染、技术爬虫性（重定向链、JS 阻塞）
  - Meta & 结构化数据：title/description/canonical、OG/Twitter、JSON-LD
  - 权威与信任：域名年龄、SSL 历史、外链质量、品牌知识图谱信号、隐私/合规标记
  - 答案格式就绪度：FAQ、对比表、Pro/Cons、分步、TL;DR、可引用统计
  - 其余：移动端/页面体积、URL 规范化、出站链接/媒体、多语言深度、跨平台分布、多页采样
- 每项独立评分（2–8 分制），全部已实现。

### 1.2 AEO 审计（免费）✅
`modes/aeo.py`：检查页面对 AI 摘要抽取的友好度（FAQ schema、问句标题、直接答案片段、表格、TL;DR），8 层递进评分 + 多页采样。

### 1.3 权威信号审计（免费）✅
`modes/authority_audit.py`：站外权威——评论平台（Trustpilot/G2/Capterra/Product Hunt）、奖项徽章、Wikipedia/Wikidata、GitHub/npm/PyPI/Crunchbase/LinkedIn/HackerNews 存在与提及。

### 1.4 多 URL 对标（免费）✅
`modes/compare.py`：并行分析多个网站，输出分类对比矩阵 + 优势排名。

### 1.5 AI 爬虫诊断 ✅/🟡
- `modes/crawl_check.py`：解析 nginx/Apache 日志，统计 AI 爬虫（GPTBot/ClaudeBot/PerplexityBot 等）抓取活动 ✅（核心实现，高阶时序聚合待补 🟡）
- `modes/crawl_test.py`：无需日志，模拟 AI bot UA 探测 robots.txt 放行 ✅（WAF/Common Crawl 细节待补 🟡）

### 1.6 实测类审计（付费，需 API Key）✅
- `modes/citation.py`：经 Perplexity 发多条品牌查询，统计引用率 ✅
- `modes/visibility.py`：多引擎可见性审计，统计品牌被提及/排名 ✅
- `modes/competitive_intel.py`：多引擎并行查询，源追踪 + 竞品发现 + 品牌排位 ✅
- `modes/entity.py`：实体 GEO 审计——知识库维度（Wikipedia/Wikidata/百度百科）已实现 ✅，其余维度部分 🟡

### 1.7 修复建议 ✅
`services/fix_package.py`（1045 行）：为每项失败检查生成可操作修复建议，支持 i18n。付费档解锁。

---

## 2. AI 可见性 / 引用追踪

让 AI 引擎真的回答品牌相关问题，抓回答 + 引用，做露出/排名/竞品分析。

### 2.1 多引擎浏览器自动化集群 ✅
服务：`services/browser-service/`（FastAPI + Playwright）。通过 Web UI 提交查询→抓答案+结构化引用，含反检测、会话持久化、二维码登录、定时刷新。

**已实现 10 个引擎浏览器**：ChatGPT、Claude、Gemini、Grok、Copilot、DeepSeek、通义千问、豆包、文心一言、元宝（文件 13–65 KB，含实际逻辑，非占位）。
**另有 API 引擎适配器**（`backend/api_engine/`）：Qwen、Perplexity、Kimi、Zhipu。

### 2.2 AI 引擎查询函数 ✅
`backend/geo_checker/ai.py`：`_query_perplexity / _query_openai / _query_anthropic / _query_deepseek / _query_doubao / _query_qwen`（经 OpenRouter 或直连）+ 分析器 `_check_brand_in_result / _extract_competitors / _classify_framing`（品牌露出、竞品抽取、正/中/负 framing）。

### 2.3 遥测追踪服务 ✅
服务：`services/telemetry-service/`。收集 browser-service 的查询+引用结果，按引擎/源域/查询维度聚合；含商业 API 网关（Bearer 鉴权 + 多租户 + 日配额计费）、引用匹配/聚类、后台调度。支持引擎：deepseek/qwen/wenxin/yuanbao/doubao/chatgpt/claude/gemini/copilot/grok。

### 2.4 品牌增长看板（前端）✅
前端 `/brand-growth`（全屏）9 个子页：
- 综合看板（KPI + 各引擎排名雷达图 + 追踪矩阵）
- 引用来源分析（按域聚合）、引擎对标、竞品对标、追踪矩阵
- AI 洞察（LLM 战略建议）、推荐词库、AI 回复草稿、发布战报

### 2.5 数据模型 ✅
`backend/geo/models/ai_telemetry.py`：Topic（品牌主题/种子词/监测 query/引擎选择/审核状态机/发布计划）、Run、Response、QueryHit（命中矩阵）、CellInsight、GeneratedDoc、Briefing。

---

## 3. 舆情监测

服务：`services/sentinel-service/`（多租户，PG schema `tenant_{N}` 隔离）。流程：监测计划生成 → 多源抓取 → LLM 分析 → 简报 → 回复草稿。

### 3.1 数据源爬虫 ✅
已实现爬虫（各含 HTTP 异常/重试逻辑）：雪球、东财（股吧/公告/研报/新闻）、新浪财经、百度贴吧、微博、知乎、财联社、格隆汇、36氪、华尔街见闻、微信公众号、老虎证券等。
搜索补盲：百度、微博、知乎、DuckDuckGo（经 `ddg-proxy`）、SearXNG，多源汇聚去重 + 时间排序。

### 3.2 LLM 分析管线 ✅
`analyzer/`：对帖子做情感 / 主题 / 风险 / 事实性结构化评分（JSON mode）。情感模型 13 维（`backend/geo/models/sentiment.py`：sentiment_label/score、emotions、topics、entities、stance、intent、factuality、risk_level、risk_signals、influence_potential、hidden_meaning 等）。

### 3.3 简报与回复 ✅
- `brief/`：生成执行摘要（Top posts + 情感聚合 + 风险概览）
- `response/`：LLM 自动生成回复草稿
- 调度：`sentiment_scheduler.py`（APScheduler 定时跑）

### 3.4 舆情面板（前端）✅
`/sentiment`：Today（热门话题/情感分布/风险告警）、Articles（内容库，多平台帖子+过滤）、Briefs（简报库）、Settings（监测词/排除词/平台/高级过滤/知识库/引导向导）。

---

## 4. 内容生产与发布

把"种子词→生成→发布→追踪"打成流水线。

### 4.1 查询扩展 ✅
`services/query_expander.py`（470 行）：4 维（Search/QA/Intent/Brand）并行调 DeepSeek 扩展监测词，含超时控制 + OpenRouter fallback。

### 4.2 文案生成 ✅
`services/content_generator.py`（1131 行）：对每条已审核 query 生成标题/正文/摘要，直连 + fallback，失败不阻塞。

### 4.3 战略方案 ✅
`services/solution_generator.py`（689 行）：先跑 25 类诊断，再 LLM 汇总成 5 大短板簇 + 7 步改进 + keyword tiers。

### 4.4 品牌资料提取 ✅
`services/profile_extractor.py`（404 行）：从上传的 PDF/Word/txt 提取品牌资料（含 OCR fallback）。

### 4.5 发布与排期 ✅
- `services/content_scheduler.py`：定时推送已审批文章
- `services/mediumsly_publisher.py`：发布到 Medium / 公众号等
- 发布战报：与 telemetry 联动追踪发文后的 AI 引用变化
- 跨平台分布：Mediumsly adapter 已实现，其他平台 🟡

---

## 5. 对话式 Agent

代码：`backend/geo/agent/`（Pydantic AI + DeepSeek）。✅ 完整实现。

- **24 个工具**（`tools.py`）：
  - GEO 线：create_topic / run_geo_checks / set_seed_prompts / expand_prompts / set_selected_queries / trigger_diagnosis / draft_articles / confirm_template / publish_drafts …
  - 舆情线：get_sentiment_today / get_sentiment_history / get_hot_topics / configure_sentiment …
  - 知识库：ingest_material / ask_knowledge …
- **业务线隔离**：问舆情只答舆情，问 GEO 只答 GEO，防混污。
- **付费门禁**：写类工具按会员档位门控。
- 前端浮窗助手 `AgentChat/AgentChatWidget.tsx`，支持实时对话。

---

## 6. 商业化（会员 / 支付）

### 6.1 会员体系 ✅
`backend/geo/services/membership_service.py` + `models/membership.py`，5 档：

| 档位 | slug | 价格 | 月检测次数 | 类型 |
|------|------|------|-----------|------|
| 注册会员 | `free` | $0 | 3 | SaaS |
| 检测会员 | `pro` | $9.99 | 20 | SaaS |
| Starter | `starter` | $999 | 无限 | SaaS |
| Growth | `growth` | $2,500 | 无限 | SaaS |
| Scale | `scale` | 定制 | 无限 | Service |

- **配额强制**：`quota_service.py` 月度配额逐请求递减，超额返回 429
- **按档限检测类别**：`allowed_check_categories`（NULL=全部）
- **数据**：`UserMembershipORM`（付费才有记录）+ `UserCheckUsageORM`（年月用量）

### 6.2 支付 ✅/🟡
- **Stripe** ✅：结账 Session + Webhook 验签自动升级 + 轮询补偿 + 防重扣 + 多币种
- **MoltsPay（Base 链 USDC）** 🟡：创建会话 + 链上 Transfer 验证 + fulfill 激活（仅 pro/starter/growth，`moltspay-server/index.mjs`）
- **微信支付** 🟡：Native 扫码 + 异步回调 + 轮询补偿（仅 CNY）
- **自动续费** 🟡：未接 Stripe Billing，仅手动重新下单

---

## 7. IM 集成与推送 ✅

代码：`backend/geo/agent/`（`alerts.py` + `embed/im_feishu.py` / `im_wecom.py`）+ 前端 `AgentIntegrationTab.tsx`。

- **平台**：飞书 ✅、企业微信 ✅、钉钉 ⚠️（仅被动回复，不支持主动推送）
- **推送目标管理**：webhook（可多个 + 加签）+ bot 群（可多个），**每个目标独立开关**，简报/告警按类型群发
- **舆情告警**：定时扫高风险帖→去重→群发→Web 端通知（`AgentNotificationORM`）
- **GEO Agent IM 入口**：快捷按钮 / 菜单映射 / token 管理，IM 用户戳按钮即问询

---

## 8. 后台工作台（Admin）✅

前端 `/workbench`，配套 `backend/geo/api/admin_*`：
- **Cockpit**：所有主题按 6 阶段 pipeline（Submit→Review→Solution→Plan→Content→Insight）显示状态
- **客户管理 / 主题配置向导**（6 步）
- **Worker 集群**（browser-service 引擎在线状态/负载）
- **爬虫运维**（sentinel 任务队列/失败重试）
- **商业 API 网关**（租户配额/API key/webhook 日志）
- **执行历史与详情**（每个 query×engine 任务的日志/引用/错误）
- **内容审核 / 平台规则配置**

---

## 9. 支撑组件

- **dist-harvester** ✅：Chrome/Edge 扩展，访问网站时采集 OG/Schema/SEO 信号回传后端（v1.0.0 / v1.1.0）
- **ddg-proxy** ✅：DuckDuckGo HTTP 反代（北美出口绕地域限制）
- **openrouter-proxy** ✅：OpenRouter HTTPS 透传代理（国内风控绕行，双 token 鉴权）
- **moltspay-server** ✅：MoltsPay 支付 fulfill 服务（Node.js）
- **运维脚本** `scripts/`：引擎健康探针、会话刷新、失败任务重跑等

---

## 10. 平台能力（横切）

- **多引擎覆盖**：10 浏览器引擎 + 4 API 引擎，**含国产豆包/通义/文心/元宝**（差异化优势）
- **多租户**：应用级 user_id 隔离 ✅；舆情 schema 级 `tenant_*` 隔离 ✅；共享表无租户列 🟡（技术债）
- **鉴权**：JWT + OAuth（GitHub/Google/微信）+ API key + 会员门控 ✅
- **缓存**：L1 Redis + L2 DB，支持强制刷新 ✅
- **国际化**：中/英完全对齐（约 1965 keys），检测结果消息双语化 ✅；后端错误消息、Admin 后台、部分高级页 🟡；日/韩/德/法/西仅 nav stub ⚠️
- **i18n 状态详见** `docs/i18n-status.md`

---

## 11. 已知限制 / 技术债

| 项 | 范围 | 说明 |
|----|------|------|
| Entity 8 维 | GEO | 仅知识库维度完整，余 7 维框架 |
| crawl WAF / Common Crawl | GEO | 框架在，细节逻辑待补 |
| 跨平台发布 | 内容 | 仅 Mediumsly adapter，其他平台未接 |
| 自动续费 | 支付 | 未接 Stripe Billing，仅手动重购 |
| MoltsPay 档位 | 支付 | 仅 pro/starter/growth，Scale 不支持 |
| 共享表多租户列缺失 | 隔离 | payment/notifications 等无租户列，需行级过滤 |
| 后端 i18n | i18n | auth 等错误消息仍英文 |
| 钉钉推送 | 集成 | 仅被动回复，无主动推 |

---

## 12. 部署拓扑（参考）

```
Frontend (React)
  ├─ Brand Growth 看板 / Sentiment 面板 / Workbench 后台 / Account
Backend (FastAPI)
  ├─ /api/check*        → geo_checker（25 类审计 + 8 模式）
  ├─ /api/sentiment     → sentinel-service（HTTP）
  ├─ /api/ai-telemetry  → telemetry-service 聚合
  ├─ /api/account|membership|payment
  └─ /api/admin_*       → workbench 后端
Microservices
  ├─ browser-service    → 10 引擎浏览器自动化
  ├─ sentinel-service   → 舆情爬虫 + LLM 分析（多租户 PG schema）
  ├─ telemetry-service  → 引用聚合 + 商业网关
  ├─ ddg-proxy / openrouter-proxy → 代理出口
存储：PostgreSQL（主库）+ Redis（缓存/会话）+ 文件（引擎会话/快照）
支付：Stripe / MoltsPay(USDC·Base) / 微信支付
LLM：OpenRouter（聚合）+ DeepSeek/Qwen/Kimi/Zhipu 直连
```

---

> 本文档以代码实现为准。"✅ 完整实现"指有实质业务逻辑并已接入主流程；标 🟡/⚠️ 的项为部分实现或受限，详见第 11 节。
