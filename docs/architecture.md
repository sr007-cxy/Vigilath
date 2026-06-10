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

## 9. 关键架构张力与注意点

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
