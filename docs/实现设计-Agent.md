# 实现设计:GEO 优化 Agent(Pydantic AI)

> v1.0 · 2026-06-04 · 配合 `最佳方案-GEO优化Agent.md`(产品/架构决策),本文是 **Pydantic AI 落地设计**:流程图 + 边界 + 工具/方法封装 + 调用。
> 框架已定 **Pydantic AI**(理由见最佳方案 §9:校验内核治 DeepSeek tool 漂、deps 注入对齐租户上下文、Python 同栈、不强加状态)。大脑 = DeepSeek。

---

## 1. 整体流程图

```
┌────────────── 入口(双) ──────────────┐
│  Web 聊天面板(SSE)   IM 飞书/企微 ISV bot │
└───────────────┬────────────────────────┘
                ▼
      resolve_account(Method)         ← 鉴权:Web token / IM 发信人 → account_id
                ▼
      build_deps → AgentDeps           ← 注入 account_id / user_id / db / topic_id / 预算
                ▼
   ┌─────────── Pydantic AI Agent.run(msg, deps) ───────────┐
   │  DeepSeek(OpenAIModel, base_url)                        │
   │   规划 → tool_call → 执行 tool(RunContext[Deps]) → 回填  │ ◀─┐
   │   参数不合法 → ModelRetry(回灌错误)→ 重试               │   │ 多步循环
   │   读类自由调 / 写类先过 usage_guardrail                  │   │
   └───────────────┬───────────────────────────────────────┘ ──┘
                   ▼ 工具内部
   现有 service(只读/提议写):geo_checker / ai_telemetry / query_expander
                   / content_generator / analyzers ;引擎走 engine_dispatch
                   ▼
   ┌─ 同步:流式 token + tool 事件 → SSE → Web/IM
   ├─ 异步:跑批/产稿(后台任务)→ 落 ai_telemetry_*
   └─ 主动:中心 cron / 事件 → deliver_notification → Web + IM 主动推

发布:模板确认(confirm_template)后,产稿完成 → publish_execute(Method,事件触发,无逐篇审批)
```

---

## 2. 边界(实现层强约束)

承接最佳方案 §3/§4,落到 Pydantic AI:

- **`account_id` 不是工具参数**:由 `resolve_account` 放进 `AgentDeps`,工具经 `ctx.deps.account_id` 取;模型无从指定别的账号。
- **模型给的资源 id 必须校验归属**:`topic_id`/`draft_id`/`plan_id` 入工具后先 `assert_owns(ctx.deps, ...)`,不符抛错(Pydantic AI 会作为工具错误回灌或终止)。
- **写类先过 `usage_guardrail_check`**;**发布无面向模型的工具**(止于 `confirm_template`/产稿)。
- **引擎集/调度平台固定**:`probe_*`/`run_geo_checks` 不暴露 engine/频率给模型。
- **单服务多租户**:**每次会话/请求新建一个 Agent run + 一份 AgentDeps**(deps 不跨账号共享);Agent 对象本身可复用(无状态、工具注册一次)。

---

## 3. 模块结构

```
backend/geo/agent/
  __init__.py
  deps.py        AgentDeps(=TenantContext) + assert_owns(归属校验)
  model.py       DeepSeek 模型工厂(OpenAIModel + base_url;复用 DEEPSEEK_* 环境)
  agent.py       build_agent():Pydantic AI Agent + system prompt + 注册全部工具
  tools.py       工具实现(@agent.tool,RunContext[AgentDeps];包装现有 service)
  methods.py     Method 层(不暴露给模型):resolve_account / usage_guardrail_check
                 / publish_execute / engine_dispatch / deliver_notification
  api.py         /api/agent/* 路由(chat SSE / diagnose / 异步任务)
```

---

## 4. Agent 与 deps(Pydantic AI 核心)

- **deps**:`AgentDeps`(`account_id, user_id, db, topic_id, budget`)——通过 `Agent(deps_type=AgentDeps)` 声明,`Agent.run(msg, deps=...)` 注入,工具内 `ctx.deps` 取。
- **模型**:`OpenAIModel("deepseek-chat", provider=OpenAIProvider(base_url="https://api.deepseek.com", api_key=DEEPSEEK_API_KEY))`。
- **system prompt**:定位 + 边界(只提议不发布、引擎平台固定、确认即锁定)+ 当前账号/主题上下文(动态注入近期诊断/计划摘要)。
- **DeepSeek 健壮性**:工具入参用 Pydantic 模型严格定义;参数非法时工具内 `raise ModelRetry("...具体错误...")` 让模型纠正重试;`Agent` 设 `retries`/步数上限。
- **结构化产出**:需要结构化结论的场景用 `output_type=<PydanticModel>`(如诊断根因);纯对话用文本。
- **流式**:`agent.run_stream(...)` → 边出边推 SSE(token + tool 调用事件)。

---

## 5. 工具封装(给模型,@agent.tool)

每个工具:`async def x(ctx: RunContext[AgentDeps], <业务参数>) -> <typed 出参>`;读类直接调,写类先 `usage_guardrail_check`;接收资源 id 的先 `assert_owns`。

| 工具 | 读/写 | 入参(模型可填) | 包装 |
|---|---|---|---|
| `create_topic` | 写 | name, brand, urls[] | `ai_telemetry_topics`(1 主题上限) |
| `ingest_material` | 写 | files[]/urls[] | 解析 + 向量化入账号知识库 |
| `propose_seed_prompts` / `set_seed_prompts` | 读/写 | (prompts[]) | `seed_prompts_json` |
| `expand_prompts` | 写 | seed_ids[] | `query_expander.expand_one_scene`(DeepSeek 4 维) |
| `confirm_prompts` | 写 | — | `queries_json.status` 固化(锁定) |
| `run_geo_checks` | 读 | (categories[]) | `geo_checker.orchestrate.generate_score`(★全局态会话化前置) |
| `probe_visibility/citation` / `trace_sources` / `analyze_competitor` | 读 | — | `modes/*` / `analyzers/*`(引擎平台固定) |
| `get_report` / `get_batch_results` / `get_publish_status` | 读 | (batch_id?) | 查 `Solution`/`runs`/`TopicGeneratedDoc` |
| `draft_content_plan` / `confirm_template` | 写 | — | `ExecutionPlan`(draft→confirmed=自动发文 gate) |
| `draft_article` / `edit_article` | 写 | (idx/content) | `content_generator` / `TopicGeneratedDoc` |
| `ask_knowledge` | 读 | question | 账号自有资料 RAG(pgvector) |

---

## 6. 方法层(不给模型,普通函数)

| Method | 职责 | 触发 |
|---|---|---|
| `resolve_account` | Web token / IM 发信人 → account_id（沿用 `get_current_user`) | 入口 |
| `usage_guardrail_check` | token/步数/引擎资源上限(非计费) | 写/观测工具内前置 |
| `publish_execute` | 模板已确认 → 调 publisher 发布 + 回填 | 产稿完成事件 |
| `engine_dispatch` | 账号池/IP 熔断/代理 | 观测工具内 |
| `deliver_notification` | Web+IM 主动推(去重/限频) | cron / 事件 |

---

## 7. 调用流程(三条)

1. **对话(同步/流式)**:`POST /api/agent/chat` → resolve_account → build deps → `agent.run_stream(msg, deps)` → SSE 推 token + tool 事件。
2. **诊断(可异步)**:对话里模型自行调 `run_geo_checks`/`probe_*`;长批次走后台任务,完成落 `ai_telemetry_*` + 触发主动推。
3. **主动触达**:中心 cron / 批次完成事件 → 选受影响账号 → `deliver_notification`(必要时拉一次 `agent.run` 生成措辞)→ 推 Web+IM。

---

## 8. 落地顺序(一步到位,内部构建依赖)

`deps/model/agent 骨架 → 第一个真实工具 run_geo_checks(+ geo_checker 会话化)→ 批量补齐工具 → api(chat SSE)→ 方法层 publish/guardrail/deliver → RAG/ingest → IM bot`。地基与工具不依赖框架细节,Pydantic AI 仅在 agent.py/tools.py 出现,换框架只动这两处。

---

## 9. 技术栈(已上线)

| 项 | 用的什么 | 备注 |
|---|---|---|
| Agent 框架 | **Pydantic AI**(`pydantic-ai-slim[openai]`) | 独立 service `geo-agent:8010` + 独立 venv;只在 `model.py`/`agent.py`/`tools.py` 出现 |
| 大脑 LLM | **DeepSeek**(OpenAI 兼容 function-calling) | 测试经 OpenRouter,加 `extra_body provider:{require_parameters,order:[DeepSeek]}` 治 tool-call 泄漏;优先 `DEEPSEEK_API_KEY` 直连否则 `OPENROUTER_API_KEY` |
| 后端 | FastAPI(Python),与主后端隔离 | 主后端 fastapi 0.104+pydantic 2.5,与 pydantic-ai 不兼容 → 独立 venv |
| 前端 | React + Vite 悬浮聊天窗(SSE 流式 + 结构化卡片) | `components/AgentChat/`、`services/agentApi.ts` |
| 鉴权 | 复用 `SECRET_KEY` + `user_service`(`geo/agent/auth.py`,不 import geo.api) | 账号级隔离,`account_id` 后端注入、不给模型 |

## 10. 向量语义检索

| 项 | 方案 |
|---|---|
| **向量模型** | **通义 DashScope `text-embedding-v3`(1024 维)** 首选;多供应商可切:OpenAI `text-embedding-3-small`、GLM `embedding-3`;`AGENT_EMBED_PROVIDER` 强制指定 |
| **向量库** | **无专用向量库 / 未装 pgvector**;向量以 JSON 存 `agent_materials.embedding_json`,检索时 **Python 算 cosine**(每账号语料小,够用) |
| 检索策略 | 语义(cosine,阈值 0.2)**优先** → 无 key/调用失败/无命中 **回退中文 bigram**(2-gram,治中文无空格);`embed` 超时 8s 快速回退 |
| 何时升级 | 语料涨到很大时改 **pgvector**(列已是 JSON,迁移即可);embedding 维度/模型由 env 切换 |

## 11. 数据存储(PostgreSQL 共用 appdb)

**复用现有(不新建工作流)**:`ai_telemetry_topics`(主题/种子/扩展词/profile/engines)、`ai_telemetry_runs`/`responses`(跑批+引擎答案+命中)、`ai_telemetry_query_hits`(**命中追踪表,被搜到/累计命中的真相源**)、`AiTelemetryTopicSolutionORM`(诊断)、`...ExecutionPlanORM`(发文计划)、`TopicGeneratedDoc`(文章+发布状态)。

**新增 3 项**:
- `agent_materials`(账号知识库:source/title/text/**embedding_json**)— ingest_material + ask_knowledge
- `agent_conversations`(账号级多轮记忆,存 Pydantic AI 序列化 message_history)
- `agent_materials.embedding_json` 列(向量)

> 「被搜到几个/累计命中」一律读 `ai_telemetry_query_hits`(与品牌增长 dashboard 同源);`get_today_effect` 给「今日新增 + 累计」,`get_query_coverage` 给「累计 query/种子」。

## 12. 部署拓扑(vm02 测试)

`nginx /api/agent/* → 127.0.0.1:8010(geo-agent.service,独立 venv)`;主后端 :8000 不动、不装 pydantic-ai。迁移 3 个(`alembic upgrade head`)。前端 `npm run build`。主动触达走中心 cron(prod 无 scheduler leader)。详见 `backend/deploy/README-agent.md`。
