# 系统架构文档 — Vigilath GEO 平台

> 最后更新:2026-06-10 · 对应分支 `feaure/yuqin`
> 本文档描述当前**运行中**的系统架构。CLAUDE.md 里"Python CLI 工具"的描述只对应历史起点;
> 仓库现已演进为一个多服务的 GEO(Generative Engine Optimization)SaaS 平台。

## 1. 总览

Vigilath 是面向中国与海外双市场的 **GEO 检测 + 优化 + 舆情** 平台,核心能力:

- **检测**:给定网站/品牌/实体,评估其在 AI 搜索引擎(ChatGPT、Perplexity、Google AI Overviews、Copilot 等)中的可见度,产出 0–100 分与分类报告。
- **优化**:对话式 Agent 把"建主题 → 诊断 → 扩词 → 生成文章 → 发布 → 看效果"整条工作流自动化。
- **舆情**:Sentinel 微服务多源采集 + LLM 分析品牌舆情。
- **商业化**:会员/配额体系 + 三套支付通道(微信 / Stripe / USDC)。

### 1.1 服务拓扑

```
                          ┌──────────────────────────────┐
                          │  frontend (React 19 + Vite)  │  :3000
                          │  i18n · 支付 · PDF/Word 导出  │
                          └───────────────┬──────────────┘
                                          │  nginx 按路径反代
            ┌─────────────────────────────┼──────────────────────────────┐
            │                             │                              │
   /api/agent/*                        /api/*                      (内部调用)
            │                             │                              │
┌───────────▼──────────┐     ┌────────────▼─────────────┐    ┌───────────▼───────────┐
│  Agent Service       │     │  主后端 backend/geo       │    │  辅助微服务群           │
│  geo.agent.service   │     │  FastAPI  :8070           │    │  services/             │
│  FastAPI  :8010      │     │  18 routers               │    │  · sentinel-service    │
│  独立 venv(pydantic  │     │  api / services / models  │    │    :8090(舆情/yuqin)   │
│  2.11 + pydantic-ai) │     │                           │    │  · openrouter-proxy    │
│  DeepSeek/OpenRouter │     │                           │    │  · ddg-proxy           │
│  飞书/企微/钉钉 接入   │     │                           │    │  · browser-service     │
└──────────┬───────────┘     └────────────┬─────────────┘    │  · telemetry-service   │
           │                              │                  │  · newsnow(:4444)      │
           └───────────┬──────────────────┘                  └───────────┬───────────┘
                       │                                                  │
              ┌────────▼─────────┐                              ┌─────────▼─────────┐
              │  PostgreSQL      │                              │  各源站 / 搜索引擎  │
              │  (共享主库)       │                              │  / AI 引擎 API     │
              └──────────────────┘                              └───────────────────┘
```

- 主后端与 Agent Service **共用同一个 PostgreSQL、SECRET_KEY、OPENROUTER_API_KEY**,但跑在不同进程/venv。
- Sentinel 有**独立数据库 schema**(多租户 `tenant_{N}`),通过 HTTP 被主后端调用。

## 2. 前端 `frontend/`

| 项 | 说明 |
|---|---|
| 框架 | React 19 + Vite + TypeScript |
| 数据 | `@tanstack/react-query`(服务端状态)+ axios |
| 路由 | react-router-dom v7 |
| 国际化 | i18next + 浏览器语言探测(中/英双语) |
| 图表 | recharts |
| 导出 | jspdf / docx / html2canvas(报告导出 PDF/Word/图片) |
| 支付前端 | ethers(USDC 钱包)、qrcode.react(微信扫码) |
| 结构 | `pages/ components/ hooks/ contexts/ services/ i18n/ types/` |

构建产物 `frontend/dist/` 由 nginx 托管。仓库另有 SSR/SSG 方案文档(`docs/SSR_PLAN.md`、`docs/ssg-home-plan.md`)。

## 3. 主后端 `backend/geo/`(FastAPI, :8070)

平台核心。入口 `geo/main.py` 挂载 **18 个 router**,采用经典三层。

### 3.1 路由层 `api/`

| Router | 前缀 | 职责 |
|---|---|---|
| `geo` | `/api` | 检测主入口:`/check/anonymous`、`/check`、`/geo`、`/geo/stream`(SSE)、`/check/fix-package` |
| `advanced` | `/api` | 付费检测模式:`compare` / `crawl-test` / `authority` / `citation` / `visibility` / `entity` / `aeo` / `competitive-intel`,引擎状态与快照 |
| `auth` / `oauth` / `account` | `/api/auth` 等 | 登录注册、第三方 OAuth、账户管理 |
| `membership` | `/api` | 会员等级与权益 |
| `payment` / `moltspay_payment` / `wechat_payment` | `/api/payment` | Stripe / USDC(x402) / 微信支付 三通道 |
| `sentiment` | `/api` | 舆情(转调 sentinel-service) |
| `content` / `content_templates` | `/api` | 内容生成与模板 |
| `ai_telemetry` | `/api` | AI 引擎遥测 |
| `engine_sessions` | `/api` | 引擎会话 |
| `admin_review` / `admin_content_review` / `admin_crawl` | `/api` | 后台人工审核 |
| `contact` | `/api` | 联系/销售线索 |

### 3.2 业务层 `services/`(~30 模块)

- **检测**:`geo_checker`(封装 `backend/geo_checker/` 引擎)、`advanced_runners`、`citation_match`/`citation_title`、`crawl_snapshot`、`detection_service`、`profile_extractor`、`query_expander`/`topic_expand`、`embedding`。
- **会员/配额**:`membership_service`、`quota_service`、`price_override`、`user_service`。
- **支付**:`stripe_service`、`wechat_pay_service`。
- **内容/方案**:`content_generator` + `content_scheduler`、`solution_generator`、`fix_package`、`mediumsly_publisher`(对接 Mediumsly 发布)。
- **舆情**:`sentiment_pipeline` + `sentiment_scheduler`、`sentinel_client`(HTTP 调舆情微服务)。
- **基础设施**:`cache_service`、`email_service`(Resend)、`backfill_cited_by`。

### 3.3 数据层 `models/`

SQLAlchemy ORM 与 Pydantic schema 混置。主要域:`user`、`membership`、`payment`、`geo`、`detection`、`sentiment`、`ai_telemetry`、`agent`、`engine_sessions`、`advanced`。

- **数据库**:PostgreSQL(`postgresql+psycopg://…/appdb`),`alembic` 管迁移(`backend/alembic/`、`backend/migrations/`)。
- **连接池**:`DB_POOL_SIZE` / `DB_MAX_OVERFLOW` env 可配(为多 worker 准备)。
- 注意:`database.Settings` 默认值写的是 sqlite,但生产 `.env` 用 PG —— 以 `.env` 为准。

### 3.4 横切关注点(`main.py`)

- **CORS**:显式枚举 dev origins + 正则兜底 LAN IP;**禁止** `allow_origins=["*"]` 与 `allow_credentials=True` 同用(starlette 会静默丢 header)。
- **全局异常**:`AppException` / `RequestValidationError` / `JWTError` 显式注册到 `ExceptionMiddleware`,避免业务异常(429 配额、402 会员门槛)被记成 ERROR 栈。
- **请求日志**:`configure_request_log()` 写 `backend/logs/requests.jsonl`;中间件给 `/api/check*` 打 `X-Process-Time`。
- **后台调度**:启动时拉起 2 个 APScheduler(舆情 / 内容生成),靠 env `GEO_SCHEDULER_LEADER=1` 保证多 worker 下仅 leader 进程执行定时任务;并清理上次中断遗留的 `generating` 僵尸行。

## 4. 检测引擎 `backend/geo_checker/`

平台 GEO 算法核心,被 `services/geo_checker.py` 薄封装调用。

> ⚠️ **三份同源代码,只有一份活跃**(见 CLAUDE.md):

| 位置 | 状态 | 是否可改 |
|---|---|---|
| `backend/geo_checker/`(package, 12 文件,`checks.py` 2958 行) | **活跃** | ✅ 所有改动进这里 |
| `geo_checker.py`(根,8065 行单体) | 冻结(= pre-refactor tag,上游对照基准) | ❌ |
| `archive/geo_checker_v1_baseline.py` | 冻结(字节级历史归档) | ❌ |

配套引擎目录:

- `backend/browser_engine/`:Playwright 真实浏览器抓取/渲染。
- `backend/api_engine/`:调真实 AI 引擎(OpenAI / Perplexity / Anthropic / OpenRouter)。

## 5. GEO 优化 Agent `backend/geo/agent/`(独立 service, :8010)

**为版本冲突而物理隔离**:pydantic-ai 需 pydantic≥2.11,而主后端钉死 fastapi 0.104.1 + pydantic 2.5.0(实测升 pydantic 会让主后端路由解析崩)。因此 Agent:

- 跑**独立 venv**(`agent-venv/`,依赖见 `backend/requirements-agent.txt`:fastapi≥0.115 + pydantic≥2.11 + pydantic-ai-slim)。
- 跑**独立进程**(`uvicorn geo.agent.service:app --port 8010`),nginx 把 `/api/agent/*` 单独反代到它。
- **共用主库与密钥**,数据互通。

### 5.1 组成

| 文件 | 职责 |
|---|---|
| `service.py` | 独立 FastAPI app,挂载 agent / embed / 各 IM router |
| `agent.py` / `model.py` / `deps.py` | Pydantic AI agent 定义,大脑走 DeepSeek/OpenRouter(OpenAI 兼容) |
| `tools.py` | 给 LLM 暴露 ~30 个 typed 工具,见下 |
| `methods.py` / `service.py` / `tools.py` | 工具背后的业务实现 |
| `auth.py` | 登录 JWT 校验 |
| `alerts.py` | 告警 |
| `embed/` | 对外接入:`api`(embed token)、`mint`/`tokens`(token 签发)、`im_feishu`/`feishu_isv`/`im_wecom`/`im_dingtalk`(飞书自建/ISV、企微、钉钉 IM 回调) |

### 5.2 工具链(`tools.py`,工作流即工具)

把整条 GEO 优化流程封装成 LLM 可调动作:

```
create_topic / get_topic            建主题(品牌+URL+行业)
run_geo_checks                      跑 GEO 检测
set_seed_prompts / get_prompts      种子提示词
expand_prompts                      扩词(每场景 N 条)
set_selected_queries                选定查询
trigger_diagnosis                   触发诊断
draft_articles                      生成文章草稿
confirm_template / publish_drafts   确认模板 / 发布
list/get/approve/reject_article     文章审核
get_report / get_batch_results      取报告
get_growth_summary / get_today_effect / get_query_coverage / list_unhit_queries   效果与覆盖
ingest_material / ask_knowledge     知识库摄取与问答
get_sentiment_today / configure_sentiment   舆情
```

工具入参严格 typed,非法时 `raise ModelRetry(原因)` 让模型纠错重试(治 tool 漂)。

## 6. 微服务群 `services/`

| 服务 | 端口 | 职责 |
|---|---|---|
| **sentinel-service**(yuqin) | :8090 | 舆情核心。多租户用 PG schema(`tenant_{N}` + `SET search_path`,contextvar `current_account` 路由)。client 用 `X-OpenAI-Key` header 注入 key。 |
| openrouter-proxy | — | OpenRouter 代理(单文件 `main.py`) |
| ddg-proxy | — | DuckDuckGo 搜索代理 |
| browser-service | — | 浏览器服务(VNC/manual login 抓取) |
| telemetry-service | — | 遥测采集 |
| newsnow(第三方镜像) | :4444 | 42 站热榜聚合,自托管绕 Cloudflare,部分源需 cookie |

### 6.1 sentinel-service 内部结构

- `crawler/`:财经源采集 — 东方财富(研报/公告)、雪球、36kr、华尔街见闻、新浪财经,以及 `newsnow_hub`(调 newsnow 容器)。
- `search/`:多引擎召回 — 百度 / 搜狗 / 必应 / DDG / SearXNG / 智谱,`plan.py` + `pipeline.py` 编排。
- `analyzer/`:LLM 情感分析 `pipeline.py` + `prompts.py`。
- `storage/`:PG 多租户存储层。
- `knowledge/`:`response_playbook.md` / `legal_redlines.md` / `brand_voice.md`(应对话术 / 法律红线 / 品牌调性)。
- `brief/` / `response/`:简报与应对生成。

## 7. 支付与商业化

| 通道 | 模块 | 场景 |
|---|---|---|
| 微信支付(Native 扫码) | `wechat_payment` + `wechat_pay_service` | 国内 |
| Stripe | `payment` + `stripe_service` | 海外信用卡订阅 |
| MoltsPay(USDC via x402) | `moltspay_payment`,`moltspay-server/` | Web3 钱包(Base 链) |

会员/配额由 `membership_service` + `quota_service` 控制,付费触发 402(会员门槛)/ 429(配额用尽)。

## 8. 部署

- **docker-compose.yml**:串起 `frontend` + `backend` + `sentinel-service` + `newsnow`,容器名内部 DNS 互通(`SENTINEL_SERVICE_URL=http://sentinel-service:8090`,`NEWSNOW_BASE_URL=http://newsnow:4444`)。
- **backend/deploy/**:systemd unit `geo-agent.service`、nginx 反代配置 `nginx-agent.conf` / `nginx-skill.conf`、`README-agent.md`。
- **backend/Dockerfile** + `start.sh` / `start-local.sh`;依赖管理用 **uv**(`backend/.venv` 无 pip)。
- 其它:`dist-harvester/extension`(浏览器扩展)、`skills/vigilath-geo`(Claude skill)。

## 9. 横向扩展(Horizontal Scaling)

> 现状一句话:**为横扩做好了准备的「单机多 worker」**。Web 层是标准的「无状态 + 共享
> PG/Redis + JWT」可横扩形态;但 PG 单主库、进程内 daemon 后台任务、静态 leader 这三点,
> 决定了它离真正的多节点弹性集群还差一个外置任务队列与库层读写分离/分片。
>
> 当前生产是一台 EC2:`geo.service ... uvicorn --workers 4`(`docs/deployment-guide.md`),
> 即单机 4 进程,前面 AWS ALB → nginx 回源 `127.0.0.1:8070`。

### 9.1 Web 层 — 可水平扩(无状态前提已满足)

横扩的根本前提是无状态 worker,本系统满足:

- **会话无状态**:认证走 **JWT**(`jose` 签名,`SECRET_KEY` 所有进程共享,`geo/api/auth.py`),
  无服务端 session → 请求可任意路由到任意进程/节点。
- **多进程并发**:`uvicorn --workers 4` 是单机多进程;扩成多台 EC2 × N worker,**代码层无需改动**。
- **入口已有 LB**:AWS ALB → nginx → uvicorn。加节点理论上只是 ALB target group 多挂机器
  +(目前 nginx/ALB 回源写死单机)把 upstream 改成多 target —— 配置工作,非架构障碍。

### 9.2 共享状态全部外置 — 横扩的核心

worker 间不靠进程内内存协调,而靠三个外部共享后端:

| 共享后端 | 作用 | 横扩含义 |
|---|---|---|
| **PostgreSQL**(单主库) | 业务数据 / 配额 / 支付 / 检测结果 | 所有 worker 连同一库 → 数据天然一致 |
| **Redis**(`db=7`, TTL 24h) | 检测报表缓存(`services/cache_service.py`) | 跨 worker/节点共享,且**降级安全**(Redis 宕只变慢,不打挂业务) |
| **JWT** | 认证态 | 无需共享 session 存储 |

**连接池为多 worker 量身配**(`geo/database.py`):`DB_POOL_SIZE`/`DB_MAX_OVERFLOW` 按 env 配
(注释示例:geo-agent 8 worker × 每 worker 上限 8 = 64,留给 PG `max_connections=100`)。
👉 **PG 连接数是当前横扩的硬上限**:节点 × worker × pool 不能撑爆 100。

### 9.3 定时任务 — env 静态 leader(穷人版选举)

横扩最易踩的坑是定时任务被每个 worker 重复执行。解法:APScheduler 只在
`GEO_SCHEDULER_LEADER=1`(舆情,`sentiment_scheduler.py`)/ `GEO_CONTENT_SCHEDULER_LEADER=1`
(内容,`content_scheduler.py`)的进程启动。部署约定:给**一个**独立 systemd 进程设该 env 当
leader,web worker 都不设 → 流量进程与定时任务进程隔离。

⚠️ 这是**静态约定式 leader**,非动态选举:多机时必须保证全局仅一个进程设 leader env;leader
挂了不自动故障转移(靠 systemd `Restart` 拉起)。要真正 HA,需换成 PG advisory lock / Redis 锁。

### 9.4 Agent / 微服务 — 独立伸缩单元(详细)

主后端、Agent、sentinel 是**三个互不依赖的伸缩单元**:各自有独立进程/端口/部署单元,可以
**分别按各自负载横扩**(Agent 吃 LLM 长连接,sentinel 吃爬虫/搜索 I/O,主后端吃 Web 流量),
互不拖累。下面分别说清楚「现状无状态到什么程度 → 怎么扩 → 扩之前必须先解的卡点」。

#### 9.4.1 Agent service(`:8010`)

**为什么能扩 —— 会话态已落库,进程本身近乎无状态:**

- **多轮对话历史存 DB**,不在内存。`api.py` 每轮 `load_message_history(deps)` 从
  `agent_conversations` 表(按 `account_id` 唯一)读历史,跑完 `save_message_history()` 写回
  (`geo/agent/methods.py`)。→ **会话单位 = 账号**,任意进程/节点都能接续同一账号的对话,
  **无需会话粘性(session affinity)**,普通轮询 LB 即可。
- 进程内仅有的"状态"是 `@lru_cache(maxsize=4)` 缓存的 agent/model 对象(`agent.py`)——
  纯只读、每进程独立重建、代价极小,不影响横扩。
- **并发模型**:每次对话用 `agent.iter()` 以 **async 流式**跑(模型→工具→模型,SSE 边生成边推)。
  这条链路是 **I/O 密集**(大部分时间在等 DeepSeek/OpenRouter),所以**单 worker 的一个事件循环
  就能并发扛很多路 SSE**。

**怎么扩(从便宜到彻底):**

1. **加进程**:`geo-agent.service` 目前 `ExecStart=… uvicorn … --port 8010` **未带 `--workers`**
   (单进程)。第一步直接加 `--workers N` → 单机多进程吃满 CPU。
2. **加实例 + nginx upstream**:起多个 geo-agent(不同端口/机器),nginx 把 `/api/agent/*`
   反代到一个 upstream 池(多 target + 健康检查)。因无会话粘性,轮询即可。
3. **容器化**:Agent 进 docker-compose / k8s,`--scale` 或 replicas 拉副本,共享同一 PG。

**⚠️ 多实例前必须先解的卡点 —— IM 去重是进程内 dict:**

飞书/企微/钉钉的 webhook 去重用的是**进程内全局 dict**(`im_feishu.py` 的 `_seen_events`、
`im_wecom.py` 的 `_seen`,`event_id/msg_id -> ts`,600s 过期)。IM 平台**会重投**事件:

- 单进程:重投命中同一个 dict → 正确去重。
- **多 worker / 多实例:重投可能落到另一个进程 → 各自的 dict 都没见过 → 同一条消息被处理两次
  → 用户收到重复回复。**

所以 **IM 链路在横扩前,必须把 `_seen_events` 外置到 Redis**(`SET event_id … NX EX 600`
做跨进程原子去重)。纯 Web 对话链路没有这个问题(去重不依赖内存,历史在 DB)。

**另一处要注意 —— IM 后台任务不持久:** webhook 收到后立即 ack(返回 `challenge`/`code:0`),
真正跑 agent 放进 FastAPI `BackgroundTasks`(`im_feishu.py:393`)。该后台任务**绑在当前进程**,
进程中途挂掉则这条回复丢失(无durable queue 重试)。要强一致,得把 IM 处理改投**外置任务队列**
(与 §9.5 第 2 点同源)。

#### 9.4.2 sentinel-service(`:8090`)

**为什么能安全多副本 —— 多租户隔离做在连接层,且连接不复用串租户:**

- **PG schema 多租户**:每个 `account_id` 对应一个 schema `tenant_{N}`。请求入口
  `_account_context()` 把 `account_id` 绑到 contextvar `current_account`(`service.py`),
  storage 层 `connect(account_id)` **新开一条 psycopg 连接**并在该连接上
  `CREATE SCHEMA IF NOT EXISTS` + `SET search_path TO tenant_{N}, public`(`storage/db.py:132`)。
- 关键安全性:**connect 是「每次调用新建连接 + 当场绑 search_path」,不是从池里捞复用连接**
  → 即使多副本高并发,也**不会出现连接残留旧 search_path 把 A 租户数据写进 B 租户**的串库问题。
  contextvar 又是 per-async-task 隔离的 → 单 worker 内并发也安全。

**怎么扩:**

- sentinel 是**无状态 HTTP 服务**,backend 经 **service name** 访问
  (`SENTINEL_SERVICE_URL=http://sentinel-service:8090`,docker DNS 轮询)。
  → docker-compose `--scale sentinel-service=N` 或 k8s replicas **直接拉副本**,backend 无需改动。
- 爬虫/搜索是长任务,多副本可把 **采集吞吐**摊开。

**扩 sentinel 的两个真实约束:**

1. **连接无池化**:`connect()` 每请求开新 PG 连接,高并发/多副本下**连接建立开销 + PG
   连接数**会先到顶(呼应 §9.2 的 `max_connections=100` 硬上限)。规模上来要在 sentinel 与 PG
   之间加 **pgbouncer** 或给 storage 层引入连接池。
2. **schema 初始化有全局串行点**:`init_schema()` 用 `pg_advisory_xact_lock(727274)` 事务级
   咨询锁串行化并发建表(避免多爬虫并行 DDL 死锁,`storage/db.py:156`)。这是**全局锁**,
   多副本同时首次触达新租户时会排队——只在租户首次建表时有竞争,日常读写不受影响,可接受。
3. 真正的吞吐天花板往往不在 sentinel 自己,而在**上游源站/搜索引擎的限流**(newsnow、百度、
   微博 cookie 等)——副本加再多,也绕不过源站 rate limit。

### 9.5 当前短板与演进路线

| # | 短板 | 演进方向 |
|---|---|---|
| 1 | **PG 单主库**,无读副本/分片,是唯一有状态瓶颈 | 加读副本(读写分离)→ 按 account 分片 |
| 2 | **进程内 daemon 线程**跑长任务(`solution_generator.py`、`geo_checker.py` 的 `Thread(daemon=True)`),绑定单 worker,重启即丢(靠 `reset_stale_generating` 兜底) | 抽成 Celery/RQ 外置任务队列 |
| 3 | **leader 静态约定**,无自动故障转移 | PG advisory lock / Redis 锁做动态选举 |
| 4 | **nginx/ALB upstream 单机硬编码** | 回源改多 target + 健康检查 |

## 10. 关键架构张力与注意点

1. **pydantic 版本债 —— 最大耦合代价**:主后端锁 pydantic 2.5,导致 Agent 必须拆独立进程/venv。彻底解法是把 FastAPI 升到 ≥0.115 后合并回主后端。
2. **三份同源 geo_checker**:极易改错文件。任何检测改动**只进 `backend/geo_checker/`**,根文件与 archive 永不动。
3. **定时任务的 leader 选举靠 env**:多 worker 部署若漏配 / 多配 `GEO_SCHEDULER_LEADER`,会导致定时任务重复跑或不跑。
4. **DB 默认值与实际不一致**:`Settings` 默认 sqlite,生产实为 PG;读代码时勿被默认值误导。
5. **舆情独立 schema**:sentinel 的数据不在主库表里,排查舆情问题要进对应 `tenant_{N}` schema。

---

### 附:端口速查

| 服务 | 端口 |
|---|---|
| frontend | 3000 |
| 主后端 | 8070 |
| Agent service | 8010 |
| sentinel-service | 8090 |
| newsnow | 4444 |
| moltspay-server | 3010(默认) |
