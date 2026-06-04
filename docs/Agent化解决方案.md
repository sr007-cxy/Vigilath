# Vigilath Agent 化解决方案

> 状态:设计草案 · 2026-06-02
> 范围:把现有「一次性 LLM 查询 + 人工串联」的 GEO 产品,升级为「自治 Agent 闭环」
> 产出约定:本文是架构方案,不含代码改动;落地路线见 §7。

---

## 0. TL;DR

现状:Vigilath 的所有 LLM 调用都是**一次性(one-shot)**的——固定 prompt → 调一次引擎 → 解析 → 打分。25 个检查项、6 个高级模式、内容生成、情感分析彼此独立,**没有 agent loop、没有工具调用式推理、没有「诊断→修复→复测」的闭环**。

方案:把已经建好的能力(25 检查、browser-service、多引擎查询、内容生成、调度器)**重新封装成 Agent 可调用的 Tool**,在其上构建一个**自治 GEO 优化 Agent**,闭合这条主链路:

```
诊断(checks/visibility) → 归因(为什么这条 query 不被引用)
  → 规划(该改哪些页/产哪些内容) → 生成(content_generator)
  → 审批(人工/规则 gate) → 发布(publisher) → 复测(re-run) → 迭代
```

技术大脑给两条路线对比(§6),建议**主路线 Claude Agent SDK,国产引擎工具仍走现有多引擎层**——即混合架构。

---

## 1. 现状盘点:能力都在,缺的是「编排大脑」

| 现有能力 | 所在模块 | 当前形态 | Agent 化后的角色 |
|---|---|---|---|
| 25 项 GEO 检查 | `backend/geo_checker/checks.py` + `orchestrate.py` | 并行跑、`track_score` 打分 | **诊断 Tool**(只读) |
| 6 高级模式(对比/引用/可见性/实体/竞情) | `backend/geo_checker/modes/*` | 各自固定 query 集 | **专项探针 Tool** |
| 多引擎查询 | `backend/geo_checker/ai.py`(`_query_*`) | 同步、一次性 | Agent 的**底层 LLM 网关** |
| 国产引擎浏览器自动化 | `services/browser-service/` + `backend/browser_engine/client.py` | Playwright 抓引用 | **真实引擎观测 Tool** |
| 引用溯源/来源分析 | `backend/geo_checker/analyzers/*` | 聚合去重 | **归因 Tool** |
| 内容生成 | `backend/geo/services/content_generator.py` | DeepSeek 一次性产文 | **执行 Tool**(写) |
| 内容/情感定时任务 | `content_scheduler.py` / `sentiment_scheduler.py` | APScheduler cron | **触发器**(让 Agent 周期性自启) |
| 修复建议 | `backend/geo/services/fix_package.py` / `solution_generator.py` | 静态规则模板 | 被 Agent 的**规划步**取代/增强 |
| 发布 | `mediumsly_publisher.py` 等 | 单平台 | **执行 Tool**(写,需审批) |

**结论**:不需要重写引擎。Agent 化 = 在现有能力外面包一层 **Tool 接口 + 推理循环 + 编排/记忆**。这是低风险、高杠杆的改造。

---

## 2. 目标 Agent 形态

定义三个 Agent,职责单一、可独立上线:

### A1. 诊断归因 Agent(只读,先上)
- **输入**:一个 URL / 品牌词 + 一组关注 query。
- **循环**:调诊断 Tool → 看哪些 query 在真实引擎里不被引用 → 自主决定再调哪个探针(引用检查?来源分析?竞情?)→ 直到能给出**带证据的根因**。
- **产出**:结构化诊断报告(哪条 query、在哪个引擎、输给了谁、缺什么信号)。
- **价值**:把今天「跑完 25 项给个分」升级成「告诉你为什么 + 该先做什么」。

### A2. 内容优化 Agent(写,需审批 gate)
- 接 A1 的根因 → 规划内容/页面改动 → 调 `content_generator` 产稿 → 进**审批队列**(复用 `admin_review`)→ 审批通过后调 publisher。
- 关键:**人在环(human-in-the-loop)**,Agent 只提议,不直接发布。

### A3. 可见性监控 Agent(周期自治)
- 由调度器周期唤醒 → 复测关注 query 的真实引擎可见性 → 与上次快照对比(`crawl_snapshot` 思路)→ 有显著下滑/竞品反超就告警 + 自动触发 A1 重新归因。
- **闭环点**:A3 → A1 → A2 → 发布 → A3,形成持续优化飞轮。

---

## 3. Tool 层设计(核心)

Agent 的能力边界 = Tool 的集合。把现有函数包成**幂等、可观测、带 schema** 的 Tool。建议放 `backend/geo/agent/tools/`。

| Tool 名 | 读/写 | 底层复用 | 入参 | 出参要点 |
|---|---|---|---|---|
| `run_geo_checks` | 读 | `orchestrate.generate_score` | url, categories[] | 分项得分 + 失分原因 |
| `probe_citation` | 读 | `modes/citation.py` | url, queries[] | 各 query 引用率、被引域名 |
| `probe_visibility` | 读 | `modes/visibility.py` | url/brand, queries[], engines[] | 多引擎可见性、竞品位次、framing |
| `observe_engine` | 读 | `browser_engine/client.py` | engine, query | 真实答案 + 引用列表(国产引擎走 Playwright) |
| `trace_sources` | 读 | `analyzers/source_trace.py` | citations[] | 去重溯源、来源类型分布 |
| `analyze_competitor` | 读 | `modes/competitive_intel.py` | brand, query | 竞品被引偏好、内容缺口 |
| `draft_content` | 写* | `content_generator.py` | topic, scene_type, brief | 草稿(进队列,不直发) |
| `submit_for_review` | 写* | `admin_review.py` | draft | 审批单 id |
| `publish_content` | 写* | `mediumsly_publisher.py` 等 | approved_doc_id | 发布回执 |
| `snapshot_visibility` | 写 | `crawl_snapshot.py` | url, queries[] | 时间序列存档 |

\* 写类 Tool 一律**经审批 gate / 配额 gate**,不允许 Agent 绕过 `membership_service` / `quota_service`。

**设计要点**
1. **Schema 优先**:每个 Tool 有严格 JSON Schema 入参/出参,让大脑用「结构化工具调用」而非自由文本解析。
2. **复用并发与缓存**:Tool 内部仍走现有 `ThreadPoolExecutor` + Redis/DB 缓存,不重复造轮子。
3. **线程安全**:`geo_checker` 用模块级全局状态(`state.py` 的 `_scores`),已有 `_geo_checker_lock`。Tool 必须沿用此锁,避免 Agent 并发调用串扰。
4. **成本护栏**:每个 Tool 调用记 token/费用(复用 `ai_telemetry` / `request_log`),Agent 单次任务有总预算上限,超额即停。

---

## 4. Agent 循环与编排

```
┌─────────────────────────────────────────────┐
│  触发: API 请求 / 调度器 cron / 监控告警        │
└───────────────┬─────────────────────────────┘
                ▼
        ┌───────────────┐   规划下一步该调哪个 Tool
        │   大脑 (LLM)   │◄──────────────┐
        └───────┬───────┘                │
                ▼  tool_call             │ 观测结果回填
        ┌───────────────┐                │
        │   Tool 执行    │────────────────┘
        └───────┬───────┘
                ▼  达到「证据充分」/ 预算耗尽 / 步数上限
        ┌───────────────┐
        │  结构化产出     │ → 落库 + (写类)进审批队列
        └───────────────┘
```

- **停止条件**:三选一——(a) 大脑判定证据充分给出结论;(b) token/费用预算耗尽;(c) 步数硬上限(防失控)。
- **记忆/上下文**:任务级用对话上下文;跨任务(同一品牌的历史诊断、已发内容、上次快照)落 DB,作为下次 Agent 的检索上下文,避免重复诊断。
- **可观测**:每步 tool_call + 推理摘要写 `request_log.py` 风格的 JSONL,前端 Workbench 可回放 Agent 决策链(对 SaaS 信任度很关键)。
- **审批闭环**:写类动作产出「提议」进 `admin_review` 队列;前端已有审批/重新生成 UI(见近期 commit `f24d229`),可直接复用。

---

## 5. 与现有 FastAPI / 前端的接入

- **后端**:新增 `backend/geo/agent/`(loop、tools、prompts),`backend/geo/api/agent.py` 暴露:
  - `POST /api/agent/diagnose`(同步或 SSE 流式返回推理过程)
  - `POST /api/agent/optimize`(异步,返回任务 id;复用现有 BackgroundTasks + 任务表)
  - `GET /api/agent/run/{id}`(查进度/回放)
- **流式**:仓库已有 SSE 基础(`test-sse.js`、`docs/SSR_*`)。Agent 推理过程用 SSE 推前端,体验远好于「转圈等结果」。
- **前端**:Workbench 下加「Agent 运行」面板——展示决策链、Tool 调用、待审批提议。复用现有 `BrandGrowth` / `ContentReview` 组件。
- **配额/分层**:Agent 是高成本功能,挂在 Pro/Enterprise 层;`membership_service` + `quota_service` 直接复用,Agent 每步消耗计入月度配额。

---

## 6. 技术路线对比(大脑选型)

| 维度 | 路线 A:Claude Agent SDK | 路线 B:复用现有 OpenRouter 多引擎自建 loop |
|---|---|---|
| 成熟度 | 高,原生 tool use / 多步推理 / 上下文管理 | 中,需自己实现 loop、工具解析、重试 |
| 工具调用可靠性 | 强(结构化 tool use,模型原生训练) | 取决于自实现的解析鲁棒性 |
| 多步规划质量 | Opus/Sonnet 规划能力强 | 受所选模型能力限制 |
| 与现有架构契合 | 需新增 SDK 依赖,大脑统一走 Claude | 零新依赖,延续现有 `_query_*` 风格 |
| 国产引擎 | SDK 不直接覆盖 → 仍需 browser-service 作为 Tool | 同样仍需 browser-service |
| 成本/可控性 | 大脑集中,token 可控;按 Anthropic 计费 | 可混用便宜模型(DeepSeek)做大脑,省钱但规划弱 |
| 落地速度 | 快(SDK 兜底循环逻辑) | 慢(自己写 loop + 边界 case) |
| 厂商绑定 | 偏向 Anthropic | 中立 |

**建议:混合架构**
- **大脑 = Claude Agent SDK**(Opus 规划 / Sonnet 执行),负责 loop、tool 调用、推理。落地快、规划强、工具调用稳。
- **观测 Tool 仍是全多引擎**:`probe_visibility` / `observe_engine` 内部继续调 Perplexity/ChatGPT/DeepSeek/Doubao/Qwen + Playwright——因为产品价值恰恰是「在各家真实引擎里的可见性」,这层必须保留多引擎,与大脑选型无关。
- **降本开关**:对低价值的批量子步骤(如标题清洗、来源分类),Tool 内部可继续用 DeepSeek 等便宜模型,只把「规划/归因」留给 Claude。

> 一句话:**用 Claude 当编排大脑,用现有多引擎层当 Agent 的眼睛和手。** 两者不冲突。

---

## 6b. 增补:路线 C「一人一只 OpenClaw」与底座选型(2026-06-04)

§6 的路线 A/B 都是**多租户单服务**思路。补一条路线 C:用开源 Agent runtime **OpenClaw**(原 Clawdbot/Moltbot,自托管 daemon,自带网关编排 + ReAct 循环 + 工具层 + 持久记忆 + 心跳调度,模型无关),**给每个客户开一只独立实例**,隔离靠实例边界而非代码。

> OpenClaw 本质是「路线 B 的成熟版」——把本来要自建的 loop / 记忆 / 调度都做成了现成的;代价是引入一个年轻(2025-11 起步)的外部 daemon 依赖,且它原生定位是「个人助理 + 多消息平台」,我们只能当 runtime 用。

把底座选型收敛成两个方案对比:

- **方案甲**:Claude Agent SDK 多租户**单服务**(= §6 路线 A)。一个服务扛所有客户,多租户 / 记忆 / 循环编排全自建,大脑走 Claude SDK。
- **方案乙**:**一人一只 OpenClaw** + 中心共享基础设施。每客户一只龙虾当大脑 + 记忆 + 日程;引擎观测 / 配额 / 审批 / 发布留中心共享。

| 维度 | 方案甲:Claude SDK 单服务 | 方案乙:一人一只 OpenClaw |
|---|---|---|
| 多租户隔离 | 代码层按 tenant 分区,易串扰、要小心 | **实例边界天然隔离,最干净** |
| 大脑 / 循环上手 | loop 要自己写 glue(SDK 兜底);记忆 / 调度自建 | **loop / 记忆 / 心跳现成** |
| 成本曲线 | **无状态,idle 不花钱**,密度高 | 常驻 daemon,idle 烧内存,**必须休眠唤醒才经济** |
| 运维 | **单服务,升级一次到位** | N 实例 provision / 升级 / 监控,复杂 |
| 安全 blast radius | 同进程,一处漏洞全租户暴露 | **炸只炸一只** |
| 跨任务记忆 | 自己设计落 DB | 自带(但要验能否挂外部 PG) |
| 依赖风险 | 绑 Anthropic SDK,成熟 | 绑年轻外部 daemon |
| 配额 / 审批 / 引擎观测层 | 都得中心自建 | **同样都得中心自建——两方案一样** |

**关键判断**:真正的分歧只在「大脑 + 记忆 + 循环放哪、怎么隔离、idle 成本曲线」。**配额、审批、引擎观测层(账号池 / 代理 / key)在两边都是中心共享、都得自己做**,不是区分点 → 这块该先做、与选型解耦。

决定因素只有两个:
1. **客户量级**:几百 → 乙的常驻没压力;几千万 → 甲的无状态密度赢。
2. **OpenClaw 状态能否外置 PG + 干净休眠唤醒**(未验证项,见 §10)。

**方案乙的隔离边界(若选乙必须守住)**:龙虾只隔离**大脑 + 记忆 + 该客户日程**;下面这些必须留中心、所有龙虾共享——
- 引擎观测层(browser-service 账号池、ARK/DashScope/千帆 key、出口 IP 代理池)= Agent 的眼睛和手,稀缺资源只能中心调度;
- 配额 / 计费 gate(`quota_service` / `membership`)卡在 MCP 工具入口前——**一人一只 ≠ 自动限额**,失控的龙虾照样烧穿 token;
- 审批 / 发布进中心 `admin_review`,龙虾只提议。
- 周期监控由**中心 cron**(prod 走 ubuntu crontab,已是先例)唤醒龙虾,而非靠每只龙虾常驻心跳——休眠场景下这是必须。

### 落地规划:地基先行 + spike 定大脑,不赌单边

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 共享地基**(两方案都要,零后悔) | §3 只读 Tool 包成 **MCP server**,沿用现有锁 + 缓存;中心 gate 层落地(`quota_service`/`membership` 卡 MCP 入口、写类进 `admin_review`、引擎观测走账号池) | 每个 MCP Tool 可独立调用、出参合 schema;任何大脑接上来都能用 |
| **P1 spike**(1 周,并行两线) | 甲线:Claude Agent SDK 接 MCP 跑 A1 只读诊断循环;乙线:拉 OpenClaw 验「状态挂外部 PG」「休眠/唤醒」两点,起一只龙虾接同一套 MCP 跑 A1;同时把客户量级拍下来 | 两边都能对真实站点跑出「带证据的根因」,产出一页 spike 结论 |
| **P2 决策 gate** | 量级大 / 要运维简单 → 甲;量级中低 + OpenClaw 状态可外置可休眠 + 看重「每品牌持久记忆飞轮」→ 乙。§8「换大脑只改 loop 适配层,Tool 不动」由 P0 做实,P2 才敢晚决定 | 选定底座,理由可追溯 |
| **P3+** | 在选定运行时上做 A1→A2→A3,沿用 §7 的 P2–P5,只是底座换成 P2 选定的那个 | 同 §7 |

> **倾向**:P0 立刻可动手、风险为零;大脑别现在赌。spike 前若硬押——**乙更契合「每品牌持续优化飞轮 + 持久记忆」**,但前提是客户量级不爆、且 OpenClaw 能外置状态;这俩任一不满足就回甲。

---

## 7. 分阶段落地路线(每阶段可独立交付)

| 阶段 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| **P0 Tool 化** | 把 §3 的只读 Tool(`run_geo_checks`/`probe_citation`/`probe_visibility`/`observe_engine`/`trace_sources`)加 schema 封装,沿用现有锁与缓存 | 无新功能,纯封装 | 每个 Tool 可被单测独立调用,出参符合 schema |
| **P1 诊断 Agent(A1)** | 接 Claude Agent SDK,只读循环,SSE 流式返回推理链 | P0 | 对一个真实站点跑出「带证据的根因」,优于现有静态 fix |
| **P2 写类 Tool + 审批闭环** | `draft_content`/`submit_for_review` 封装,接入现有审批队列 | P1 | Agent 提议进队列,人工可审/可重新生成 |
| **P3 优化 Agent(A2)** | 根因→规划→产稿→审批,异步任务 + 配额 gate | P2 | 端到端跑通诊断到「待发布提议」,无越权 |
| **P4 监控 Agent(A3)** | 调度器周期复测 + 快照对比 + 告警触发 A1 | P3 + `crawl_snapshot` | 可见性下滑能自动触发重新归因,形成飞轮 |
| **P5 决策回放 + 治理** | 前端 Workbench Agent 面板、决策链回放、成本看板 | P3 | 每次 Agent 运行可审计、可复盘、成本可见 |

**先做 P0+P1**:风险最低(全只读)、最快出可演示价值、为后续写类闭环铺好 Tool 地基。

---

## 8. 风险与护栏

| 风险 | 护栏 |
|---|---|
| Agent 失控烧 token | 单任务总预算 + 步数硬上限 + 每步成本计量(`ai_telemetry`) |
| 写类动作越权/乱发布 | 写 Tool 一律经审批 gate,Agent 只提议;配额经 `quota_service` |
| 并发串扰全局状态 | 沿用 `_geo_checker_lock`,Tool 内部不暴露可变全局 |
| 国产引擎 Playwright 不稳 | `observe_engine` 失败降级为「best-effort 空引用 + error」,不阻断整个循环(延续现有错误策略) |
| 推理不可解释,SaaS 信任低 | 全程决策链落 JSONL + 前端可回放 |
| 大脑厂商绑定 | Tool 层与大脑解耦,换大脑只改 loop 适配层,Tool 不动 |

---

## 9. 与现有 ENHANCEMENT 的衔接

- 未完成项 #4(主题权威/内容簇)、#16(页级实体密度)都需要「更深的爬取 + 规划」——正是 A2 优化 Agent 的天然落点:Agent 可自主决定爬多深、补哪些内容簇。
- #22(关键词→实体模式升级)可作为 A1 的入口分支:输入不是 URL 而是品牌词时,Agent 直接走实体可见性归因。

---

## 10. 开放问题(待拍板)

1. 大脑是否锁定 Claude Agent SDK?(本文建议是;若要厂商中立则走路线 B)
2. 写类闭环的审批严格度:全人工审批,还是「低风险动作规则自动放行 + 高风险人工」?
3. A3 监控的唤醒频率与配额归属(按品牌?按 query 数计费?)
4. 是否需要把 6 个高级模式全部 Tool 化,还是先只上诊断必需的 3~4 个?
5. **底座选甲还是乙(见 §6b)**:客户量级是几百还是几千万?这直接定「无状态单服务」还是「一人一只常驻 + 休眠唤醒」。
6. **OpenClaw 可行性(P1 乙线)**:状态能否挂外部 PG 当 memory、能否干净 dump/restore 休眠唤醒?能否多实例共享同一套中心 MCP 与账号池?——这三点不过,方案乙不成立。
