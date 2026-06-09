# GEO 优化 Agent — 最佳方案(权威版)

> v1.0 · 2026-06-04 · 本文为唯一落地基准(已整合并取代此前迭代稿)。

---

## 1. 定位

**一个对话式 GEO/AEO 优化助手**:用户用自然语言(Web + IM)走通「诊断 → 优化 → 发文 → 复测」飞轮,助手主动推进、主动触达。

**实现本质**:在现有 `ai_telemetry_*` 工作流外包一层对话式 Agent(Tool/Method 接口 + 单 agent 循环 + DeepSeek 大脑),**不重写**引擎 / 生成 / 工作流。

---

## 2. 范围(首期完整交付,一步到位)

**做**:单账号一个对话助手;Web + IM 双入口;建主题 → 资料 → 提示词 → 诊断 → 发文模板 → 自动发文;主动触达;基于账号自有资料的问答。

**不做**:多主题(限 1);逐篇审批(模板确认即自动发文);计量计费(一次性线下合同);公共 GEO 知识语料库;新用户体系;MVP 不上多 agent。

---

## 3. 边界

| 维度 | 边界 |
|---|---|
| 隔离单位 | **账号(account)**;`tenant_id` 逻辑隔离,单服务多租户,不起 per-account 进程 |
| 会话单位 | **账号 ↔ 助手**(账号成员共享会话与记忆,不分自然人) |
| 引擎查询 | **平台固定**:引擎选择 / 调度 / 频率 / 账号池 / IP 全由平台控制,**用户只看结果** |
| 发布 | **模板一次性确认后自动发布**,无逐篇审批;模型不能擅自确认/发布 |
| 计费 | **产品内无计量计费**;收费一次性线下合同(系统不处理);仅内部用量护栏 |
| 用户体系 | **沿用现有 membership/auth**,不新建、不改权限 |
| 越权 | `account_id` 由后端注入(非模型可填);模型给的 id 一律服务端校验归属 |

---

## 4. 用户可操作的能力

| 用户**可以** | 用户**不可以** |
|---|---|
| 建主题(限 1) | 建第 2 个主题 |
| 上传资料(文件 / URL) | 选择 / 调度引擎查询、定频率 |
| (确认前)设定 / 扩展种子词与扩展词 | 改**已确认锁定**的提示词 |
| **确认提示词**(确认即锁定) | 逐篇审批(已无审批环节) |
| 确认发文模板(确认后自动发文) | 跨账号读写 |
| 编辑文章(发布前) | 改计费 / 用户体系 |
| 看报告 / 进度 / 发布结果 | 绕过用量护栏 |
| 对话提问(含基于**自己上传资料**的问答) | — |

---

## 5. 用户 / 账号信息(数据归属)

- **用户体系沿用现状**(`users` + `membership`);Agent 不碰。
- **账号级数据**(都挂 `account_id`,账号隔离):主题档案、上传资料、种子/扩展提示词(带锁定状态)、诊断报告、发文模板/计划、文章、发布记录、批次结果、会话与决策链。
- 这些**绝大多数已存在于 `ai_telemetry_*`**(见 §8),Agent 只读写,不新建工作流表。

---

## 6. 用户旅程(端到端)

```
注册(现有体系) → 建主题(限1) → 上传资料(进账号知识库)
 → 共创种子词 → 扩展词 →【确认锁定,不可再改】
 → 诊断报告(带证据根因;引擎/调度平台固定,用户只看结果)
 → 发文计划/模板 → (可选)编辑
 →【一次性确认模板】→ 此后自动产稿 + 自动发布(无逐篇审批)
 → 批次跑完有新数据 → 主动推送给用户
 → 随时问报告/进度/基于自有资料的问答
 → (复测)回到诊断,形成飞轮
```

---

## 7. 架构

```
   Web 聊天面板 ┐                        ┌ 飞书/企微(ISV 第三方应用,自建 bot)
                ├── 双入口(账号↔助手,共享会话/记忆)──┤
                └────────────┬────────────────────────┘
                             ▼  resolve_account(鉴权 + tenant)
              ┌──── 单 agent 循环(OpenAI Agents SDK;DeepSeek tool-use)────┐
              │   规划 → 调 Tool → 回填 → 迭代(读类自由 / 写类过护栏)  │
              └────┬────────────────────────────────┬──────────────┘
                   ▼ 读 / 提议写                       ▼ 确定性副作用(Method,不给模型)
            Tool 层(给模型)              鉴权 / 用量护栏 / 发布 / 引擎调度 / 记忆 / 推送
                   │
                   ▼  复用现有:ai_telemetry_* 工作流 / query_expander / content_generator
                        / analyzers / 引擎账号池(browser-service + API 引擎)
```

**核心原则**:**读 + 提议写 = Tool(给模型);鉴权 / 用量护栏 / 发布 / 调度 / 推送 = Method(不给模型)。**

---

## 8. 能力范围(Agent 能做什么 = Tool/Method 清单)

后端工作流**已存在**,Agent 是薄包装。

**Tool(暴露给大模型,严格 JSON Schema;写类先过用量护栏)**

| Tool | 复用 | 说明 |
|---|---|---|
| `create_topic` / `get_topic` / `update_topic` | `ai_telemetry_topics` | 内置 1 主题上限 |
| `ingest_material` / `list_materials` | 上传 + 向量化 | 进账号知识库(=用户资料) |
| `propose_seed_prompts` / `set_seed_prompts` | `seed_prompts_json` | 共创;锁定后拒写 |
| `expand_prompts` | `query_expander.expand_one_scene`(已用 DeepSeek) | 4 维扩展;锁定后拒写 |
| `confirm_prompts` | `queries_json.status` 固化 | 确认 → 锁定 |
| `run_geo_checks` / `probe_*` / `trace_sources` / `analyze_competitor` | `geo_checker` / `modes/*` / `analyzers/*` | 引擎集/调度平台固定 |
| `get_report` / `get_batch_results` | `Solution` / `runs`+`responses` | 只读检索 |
| `draft_content_plan` / `confirm_template` | `ExecutionPlan`(draft→confirmed) | 模板确认 = 自动发文 gate |
| `draft_article` / `edit_article` | `content_generator` / `TopicGeneratedDoc` | 发布前可编辑 |
| `get_content_plan` / `get_publish_status` | `ExecutionPlan` / `TopicGeneratedDoc` | 进度/结果 |
| `ask_knowledge` | 账号知识库 RAG | **基于用户自有资料**问答(非公共语料) |

**Method(不暴露给模型)**:`resolve_account`、`usage_guardrail_check`、`publish_execute`(模板已确认 → 产稿完成触发)、`engine_dispatch`、`memory_read/write`、`deliver_notification`。

**新增表(仅 3)**:`agent_session` / `agent_trace`(会话 + 决策链)、`account_channel_binding`(账号 ↔ 渠道用户)、知识库向量表。

---

## 9. 框架选择(✅ 已落地:Pydantic AI + DeepSeek)

**大脑 = DeepSeek**(OpenAI 兼容 function-calling;成本远低于 Claude、与现有内容生成同栈;测试环境经 OpenRouter,加 `provider:{require_parameters,order:[DeepSeek]}` 修 tool-call 泄漏)。

**框架 = Pydantic AI**(`pydantic-ai-slim[openai]`)—— **已实现并上线 vm02 测试环境**(详见 `实现设计-Agent.md`)。

> 初评时一度倾向 OpenAI Agents SDK(下方矩阵),但**落地选了 Pydantic AI**:头号风险是 DeepSeek 多步 tool-calling 不稳,**Pydantic AI 的结构化校验最对症**;deps 注入也正好对齐租户上下文。实测验证通过(19 工具、多轮记忆、语义检索)。
> 候选只在**确认真实、成熟**的 Python agent 框架里评(此前误纳的 OpenClaw / Hermes / Pi SDK 来自不可靠的联网信息,已剔除)。

**候选对比矩阵**(✅强 / ◯可 / ⚠️弱;粗体为本项目权重高的维度):

| 维度 | OpenAI Agents SDK | **Pydantic AI(选)** | LangGraph |
|---|---|---|---|
| 语言/可嵌入(Python) | ✅ | ✅ | ✅ |
| DeepSeek(OpenAI 兼容,base_url) | ✅ | ✅ | ✅ |
| **成熟 / 出名(你看重)** | ✅ 官方、广用 | ◯ Pydantic 团队背书 | ✅ 生态最大 |
| **好用 / 简单(单 agent 场景)** | ✅ 轻、直接 | ✅ 轻、类型化 | ⚠️ 偏重、学习曲线 |
| DeepSeek tool 稳健性(头号风险) | ◯(严格 schema 可补) | ✅ 结构化校验最强 | ◯ |
| 多 agent(**不确定的未来**) | ✅ handoff | ◯ delegation | ✅ supervisor/swarm |
| 我们用得到的重特性 | 单 agent 循环 + 工具 + 流式即够 | 同 | 持久化/checkpoint/记忆 **我们自建,用不上** |

**决策逻辑(落地结论)**:
1. **多 agent 不确定** → LangGraph 的持久化/记忆/checkpoint 我们全自建,用不上 → **过度工程,出局**(真要复杂多 agent 编排再上)。
2. **头号风险 = DeepSeek 多步 tool-calling 不稳** → **Pydantic AI 的结构化校验最对症**,deps 注入对齐租户上下文;故**最终落地 Pydantic AI**(OpenAI Agents SDK 为同级备选,Tool/Method 与框架解耦,换不动工具层)。
3. **实测**:经 OpenRouter 时 DeepSeek 会把 tool-call 泄漏成文本 → 加 `provider:{require_parameters,order:[DeepSeek]}` 解决,Pydantic AI schema 校验 + 重试兜底。

**低风险**:W1 地基与框架无关;三个候选都支持「将来要多 agent 也能长」。**现状:Pydantic AI 已上线、19 工具全工作流验证通过。**

---

## 10. 知识库 / RAG(= 用户自有资料)

- **知识库 = 账号自己上传的资料(用户信息)**,**不建公共 GEO/AEO 语料库**。
- `ingest_material` → 解析 / 切块 / 向量化入账号知识库;用于**诊断/产稿的事实支撑**与**基于自有资料的问答(`ask_knowledge`)**。
- 通用 GEO 问题走模型常识即可,不单独维护语料。
- **栈**:**pgvector(挂现有 appdb)** + **中文 embedding(通义 DashScope 或 BGE-m3)**——**DeepSeek 无 embedding,必须独立选**;DIY 薄封装,账号隔离。

---

## 11. 渠道(双入口,IM = ISV)

- **会话单位 = 账号 ↔ 助手**。
- **Web**:Workbench 内嵌一等聊天面板(`/api/agent/chat` SSE 流式),与富文本编辑 / 报告图表 / 模板表同页联动。
- **IM = 飞书 / 企微 ISV 第三方应用**:Vigilath 做成第三方应用,**各客户企业授权安装**,触达其成员;自建 bot(webhook / 事件 / 卡片 / OAuth);入站按发信人 → 账号会话,1对1。

### 11.1 各入口用户接入流程(已落地,2026-06-08)

四种入口,对应 `docs/对外开放设计-Agent小龙虾.md`。渲染:网页 react-markdown 全 GFM;飞书真表格卡片(2.0 table);企微/钉钉 markdown(表格转列表行)。

**A. 网页端(最省事,零配置)**
登录 Vigilath → 右下角浮窗直接聊;可让 agent 带跳转「信源分析」等页(`open page` 关键词匹配)。

**B. 自建应用 IM(飞书 / 企业微信 / 钉钉)** — 一应用 = 一账号,团队共用
1. 控制台 → 对接集成 → 选平台 → 复制**回调地址**。
2. 去自己 IM 开放平台建企业自建应用/机器人,拿凭证、配回调、订阅「接收消息」、开消息读写权限(飞书还要**发布版本**)。
3. 回控制台填凭证 → 保存并连接。
4. 团队 @机器人 对话。
   - 飞书坑:事件 `im.message.receive_v1` + `im:message`(单聊要 `im:message.p2p_msg:readonly`)+ **必须发布版本**;可用范围含本人。详见 [[reference_feishu_im_bot_setup]]。
   - 后端:`backend/geo/agent/embed/im_{feishu,wecom,dingtalk}.py`。

**C. 飞书应用商店(ISV)** — 一键装,每用户各自绑
1.(前提:Vigilath ISV 应用已上架飞书市场)
2. 客户管理员市场搜 Vigilath → 一键安装 + 授权(无需建应用/配事件/发布)。
3. 每个使用者:控制台 → 对接集成 → **生成飞书绑定码** → 飞书发给机器人 → 绑定成功。
4. 之后直接对话,数据按各人绑定账号隔离。后端 `feishu_isv.py`(app_ticket 三段式鉴权)。

**D. 小龙虾对接(skill / API)** — 一 token = 一账号
1. 控制台 → 对接集成 → **生成 1 年期 token** → 复制一行安装命令。
2. 小龙虾机器跑 `curl -fsSL <域名>/skill/install.sh | bash -s -- <token>`(skill 包 `skills/vigilath-geo/`)。
3. 或直接调 `POST /api/agent/v1/chat`、`GET /api/agent/v1/data/*`(Bearer token)。

> 共性:account_id 后端注入、不给模型;**真实发布对外永不开放**(平台护栏);写操作需明确意图。

---

## 12. 主动触达(周期轮询 + 事件,中心 cron)

- **事件**:批次/复测跑完有新数据、可见性下滑 → `deliver_notification` 主动推(例:「复测跑完了,X 条 query 有新结果」)。
- **周期轮询**:中心 cron(prod 无 scheduler leader,`crawl_snapshot` 先例)周期扫账号,发现下滑/反超 → 拉起 Agent 主动开口。
- **克制**:推送去重 / 限频 / 免打扰自建;按 `account_channel_binding` 投递。

---

## 13. 部署与影响面

- Agent 嵌入现有 FastAPI(`backend/geo/agent/` + `/api/agent/*`),单服务多 worker,每会话实例化 Agent + 账号记忆,按需 load。
- 自建 IM bot(可灰度);主动触达走中心 cron;数据共用 PG appdb(账号隔离)+ pgvector。
- **纯增量,不碰现状**:现有 `/api/check/anonymous`、舆情、browser-service、内容生成、`ai_telemetry` 工作流原样不动。
- **唯一侵入改造**:`geo_checker` 模块级全局态(`_scores`/`_geo_checker_lock`)→ 会话作用域化(多账号并发跑 `run_geo_checks` 时必须;单账号可延后);回归兜底 = 根文件 vs package 分数对齐。

---

## 14. 可扩展性(路线图,现在留口子)

| 方向 | 留口子 |
|---|---|
| 更多工具/技能 | 统一 Tool 契约 + 注册表;能力分组 + 分阶段暴露(防 DeepSeek 工具膨胀);加 = drop-in |
| 多 agent(不确定) | OpenAI Agents SDK 的 handoff(需要时);Tool 按未来 subagent 职责分组;真要复杂编排再评 LangGraph |
| 多主题/品牌 | 记忆/会话 key 按 `account × topic`(MVP 限 1 仅上限校验,不写死 account 级) |
| 多渠道/引擎 | channel adapter(`account_channel_binding` 泛化)+ engine adapter 统一 `observe` |

---

## 15. 一步到位落地(完整交付,不分阶段)

一次性交付完整产品 = 下列**全部模块**,按依赖并行推进,最终以**整条用户旅程(§6)端到端跑通**为唯一验收。

| # | 工作流 / 模块 | 内容 | 依赖 |
|---|---|---|---|
| W1 | **地基层** | Tool/Method 统一契约 + 注册表;租户上下文注入 + 服务端归属校验;用量护栏;**`geo_checker` 全局态(`_scores`/`_geo_checker_lock`)会话作用域化** | — |
| W2 | **工具封装** | 把 `geo_checker` / `ai_telemetry_*` / `query_expander` / `content_generator` / `analyzers` 全部包成 §8 的 Tool(诊断/提示词/内容/查询/知识),严格 schema | W1 |
| W3 | **Agent 循环** | 接 OpenAI Agents SDK + DeepSeek tool-use;账号级会话/记忆(`account × topic`);严格 schema 校验 + 重试;决策链落 `agent_trace` | W1 |
| W4 | **工作流闭环** | 建主题 → 资料 → 提示词(确认锁定)→ 诊断报告 → 发文模板 →**模板确认 → 自动发文**(复用 `auto_generate_*`,无审批) | W2,W3 |
| W5 | **知识库/RAG** | pgvector(appdb)+ 中文 embedding;`ingest_material` 向量化入账号知识库;`ask_knowledge` 基于自有资料问答 | W1 |
| W6 | **双入口渠道** | Web 聊天面板(`/api/agent/chat` SSE)+ 同页联动编辑/报告/模板;IM 飞书/企微 **ISV 第三方应用** bot;`account_channel_binding` | W3 |
| W7 | **主动触达** | 中心 cron + 事件(批次完成/下滑)+ 周期轮询;`deliver_notification` 去重/限频/免打扰 | W3,W6 |
| W8 | **前端** | Workbench 聊天面板 + 报告/可见性图表 + 发文模板表 + 决策链回放 | W6 |

**构建依赖(关键路径)**:`W1 → W2/W3 → W4/W5/W6 → W7/W8`;W1 的 `geo_checker` 会话化是多账号并发的硬前置,**一步到位场景下直接做掉**(不延后)。

**唯一验收**:对一个真实账号,从注册 → 建主题 → 资料 → 提示词锁定 → 诊断报告 → 模板确认 → 自动发文 → 主动推送 → 自有资料问答,**Web 与 IM 双入口端到端全程跑通、无越权、决策链可回放**。

---

## 16. 待确认(剩余开放项)

1. **模板确认主体**:用户自助确认 vs 平台专员一次性把关(已不在逐篇层,影响小)。
2. **每账号月运行次数预期**:用于我们成本测算 + 用量护栏定档(非客户计费)。

---

## 17. 关键风险

| 风险 | 应对 |
|---|---|
| DeepSeek 多步 tool-calling 不稳 | 严格 schema + 校验重试;单轮少工具;步数上限;关键步升级模型 |
| `geo_checker` 全局态并发串扰 | 会话作用域化(唯一侵入改造),回归基准兜底 |
| 框架 / DeepSeek tool 稳健不足 | 严格 schema + 校验重试;W1 地基与框架解耦;必要时切 Pydantic AI(结构化校验最强),不动工具层 |
| 主动触达刷屏 | 去重 / 限频 / 免打扰 + 单一中心 cron |
| 多租户越权 | 租户上下文注入 + 服务端归属校验 |
