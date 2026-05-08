# 舆情系统架构 v2(重构目标 · Tier 2 / 精简版)

> 状态:方向已确认,细节待重构期落地
> 创建日期:2026-05-08
> 上一版:[`sentiment-architecture.md`](./sentiment-architecture.md)(描述重构前现状,**不要原地覆盖**)
> 配套文档:[`../../docs/sentiment-gap-analysis-vs-wisersone.md`](../../docs/sentiment-gap-analysis-vs-wisersone.md)(对标慧科,本次重构暂不并入)
>
> **本方案是 Tier 2(精简版)**:DAG runner 跑在 backend 进程内,sentinel-service 完全不动。预计工作量 5-8 工作日,单 PR 上线,可整体回滚。
> **Tier 3(完整方案)**——把 runner 下放到 sentinel、把 14 crawler 拆成独立 DAG 节点、HTTP API 收窄、4 phase 灰度迁移——保留为后续可选演进,不在本轮范围。

---

## 0. 重构动机

把当前散落在 `sentiment_pipeline.py` 200 行 if-else 里的**隐式 pipeline**,显式化成**类 ETL 的 DAG 工作流**。

**一句话**:重构不是推倒重来,是把"一串 HTTP 调用顺序"换成"一个可被 runner 解释执行的 DAG 对象",**只动 backend 编排层,sentinel 不动**。

要解决的具体痛点:

| 痛点 | v1 现状 | 锚点 | Tier 2 是否覆盖 |
|---|---|---|---|
| 隐式 DAG | 14 crawler + 4 主 RPC 顺序、并行、容错策略,全靠 200 行 if-else 表达 | `sentiment_pipeline.run_pipeline_for_account()` | ✓ 显式声明 |
| stage 级失败粒度缺失 | 14 crawler 失败被吞进 `crawlers_stats[name].error`,无独立 retry / 跳过 / 补跑能力 | `sentiment_pipeline._run_crawlers()` | ✓ stage 级 retry + 留痕 |
| 前端无中段进度 | UI 只能拿到 `last_run_status` 顶层四态,看不到"卡在哪一步" | `Sentiment.tsx` / `StatusBanner` | ✓ stage 级进度 |
| 三层防重入语义重复 | scheduler `max_instances=1` + 主库 `last_run_status='running'` + `sentinel_cleanup` 60 分钟僵尸回收 | `sentiment_scheduler.py` + pipeline + cleanup job | ✓ 收敛为 scheduler + 唯一索引 |
| HTTP 长连接 + 业务级超时 | `run_analyze=1800s`、`run_brief=1200s`、`run_monitor=1200s`,任一卡住整个 pipeline 卡住 | `backend/geo/services/sentinel_client.py` | ✗ 仍在,但被 stage retry 间接缓解 |

**为什么不覆盖最后一条(HTTP 超时)**:

- v1 实测 analyze 20–30 分钟,1800s 够用;这是个"心理负担 > 实际 bug"的项
- 真要去掉,得让 sentinel 端支持 async-submit + poll,那就成了 Tier 3
- Tier 2 里每个 RPC = 一个 stage,失败可独立 retry,**不会"整 pipeline 因一次 HTTP 抖动废掉"** — 痛点被间接缓解到可接受

**非目标**(明确不在本次重构范围):

- sentinel-service 进程内部不动(`service.py`、19 RPC、stages 拆分、`runner.db`、HTTP API 收窄,全部 Tier 3 才做)
- monkey-patch `connect()` 多租户隔离逻辑(`service.py:35-127`)
- 14 crawler 拆成独立 DAG 节点(Tier 2 仍在 backend 用 ThreadPoolExecutor 并行,但**收进一个 `crawl_fanout` stage 内部**)
- 现有 5 张业务表 schema(已是 ETL-friendly,不动)
- LLM provider / 搜索引擎 / 爬虫源切换
- 引入 Redis / Celery / Airflow / Prefect / Dagster 等新基础设施

---

## 1. 一句话定位

**Sentinel v2 (Tier 2) = backend 内的 DIY DAG runner,把 plan / search / crawl / analyze / brief / notify 声明成显式 DAG;每个 stage 内部仍调原有 sentinel RPC;sentinel-service 完全不动。**

---

## 2. 物理拓扑

```
┌────────────────────────────┐    HTTPS     ┌──────────────────────────────────────┐
│  Frontend (React/Vite)     │  ─────────▶  │  GEO backend (FastAPI, 8070)         │
│  Dashboard/sentiment/      │              │  geo/api/sentiment.py(+1 endpoint)   │
│   ├─ TodayTab              │              │  geo/services/                       │
│   ├─ ArticlesTab           │              │   ├─ sentiment_scheduler.py 不动     │
│   ├─ BriefsTab             │              │   ├─ sentiment_pipeline.py    ★ 瘦身 │
│   └─ StatusBanner(stage 级)│              │   │   223 行 → ~30 行                │
└────────────────────────────┘              │   ├─ dag_runner.py            ★ 新增 │
                                            │   │   ~300 行,DAG 解释执行          │
                                            │   ├─ sentiment_dag.py         ★ 新增 │
                                            │   │   ~80 行,DAG 静态声明           │
                                            │   └─ sentinel_client.py 不动         │
                                            │  Postgres/SQLite 主库:               │
                                            │   sentiment_accounts                  │
                                            │   sentiment_knowledge                 │
                                            │   sentiment_run_logs(复用为 run)    │
                                            │   pipeline_stage_runs        ★ 新增  │
                                            └────────────────┬─────────────────────┘
                                                             │
                            原有 18 个写 RPC(4 主 + 14 crawler)+ 6 个读端点 全部保留
                                                             │
                                                             ▼
                                            ┌──────────────────────────────────────┐
                                            │  sentinel-service (FastAPI, 8090)    │
                                            │                                      │
                                            │   完全不动                           │
                                            │                                      │
                                            └──────────────────────────────────────┘
```

**职责切分**:

- **Frontend**:不变,但 StatusBanner 可消费 stage 级进度(见 §8)
- **GEO backend**:新增 DAG runner,`sentiment_pipeline.py` 大幅瘦身;`sentinel_client.py` 不动
- **sentinel-service**:**零改动**

---

## 3. 端到端数据流

### 3.1 DAG 形态(在 backend 进程内)

```
[Run] ── stage: monitor ──┐
        (1 次 sentinel    │
         run-monitor 调用,│
         内部 plan + 3 引擎)│
                          ├──▶ posts(写入 sentinel per-account SQLite)
        stage: crawl_fanout─┘
        (1 个 stage,内部用 ThreadPoolExecutor 并行调 14 个 crawl_* RPC,
         单点失败吞进 stage_summary 但不传染 — 与 v1 行为一致)
                                                            │
                                                            ▼
                                                   stage: analyze
                                                   (1 次 sentinel run-analyze,
                                                    内部仍是 LLM_ANALYZE_CONCURRENCY=5)
                                                            │
                                                            ▼
                                                   stage: brief (reduce)
                                                   (1 次 sentinel run-brief)
                                                            │
                                                            ▼
                                                   stage: notify
                                                   (邮件推送,backend 内执行,不调 sentinel)

draft 是独立子 DAG,前端按需触发,不在 hourly run 路径
```

**节点级注**:

- **monitor 是一个节点,不拆 plan / search.cnbing / search.baidu / search.ddg**:Tier 2 不动 sentinel,sentinel 端 `/run-monitor` 一次调用内部完成 plan + 3 引擎 SERP,backend runner 看不到内部细节,只能整体当一个 stage。要拆是 Tier 3 的事
- monitor 与 crawl_fanout 互不依赖,DAG 上是 sibling,可并行
- 14 crawler 是**一个 stage 内 fanout**,不是 14 个独立节点(这是 Tier 2 vs Tier 3 的关键缩减)
- analyze 是 stage 内 fanout(沿用 sentinel 内的 `LLM_ANALYZE_CONCURRENCY=5`)
- brief 是 reduce stage

**实际 DAG 节点数**:`(monitor ∥ crawl_fanout) → analyze → brief → notify` = **5 个节点**

### 3.2 触发与执行

```
[backend] sentiment_scheduler.run_hourly_job()
       │  遍历 active accounts
       ▼
[backend] sentiment_pipeline.run_pipeline_for_account(account_id, trigger)
       │  ① 在主库写一行 sentiment_run_logs(status=running),id 即为 run_id
       │
       ├─▶ ② dag_runner.execute(SENTIMENT_DAG, ctx={account_id, run_id, ...})
       │       runner 拓扑排序 → 逐 stage 执行:
       │         - stage 入库 pipeline_stage_runs(run_id, stage_id, attempt, ...)
       │         - stage body = 一次 sentinel HTTP RPC 调用(老的 sentinel_client 方法)
       │         - 失败按 retry_policy 重试,attempt 用尽则按 on_failure 决定下游
       │         - stage 状态实时回写 pipeline_stage_runs
       │
       ├─▶ ③ runner 完成 → 聚合 stage 结果决定 run 整体 status
       │
       └─▶ ④ 更新 sentiment_run_logs.status / ended_at / stats_json;
              notify_emails 非空时推送邮件
```

**关键差异 vs v1**:

- pipeline.py 不再写"if-else 调 RPC"流水账,改为"提交 run + 调 dag_runner + 写 log"骨架
- 单 stage 失败由 runner 的 retry_policy 决定,不再靠 backend `try/except` 散落 14 次
- stage 状态实时落库,FE 可中段查询

**vs Tier 3 的差异**:

- runner 跑在 backend 进程,**仍走 HTTP 调 sentinel**(每 stage 一次 RPC)
- sentinel 端没有任何 DAG 概念,继续吃老 RPC
- 不引入 sentinel 侧的 `runner.db` / lease / reaper / cancel 端点

---

## 4. 模块清单

### 4.1 GEO backend(本次主战场)

| 模块 | 关键文件 | 入口 | v1→v2 |
|---|---|---|---|
| DAG runner ★ | `geo/services/dag_runner.py` | `execute(dag, ctx) -> RunResult` | **新增 ~300 行**:节点遍历 + retry + stage 状态写入 |
| DAG 声明 ★ | `geo/services/sentiment_dag.py` | `SENTIMENT_DAG: DAG = ...` | **新增 ~80 行**:静态 DAG + 每节点 retry_policy |
| Pipeline 编排 | `geo/services/sentiment_pipeline.py` | `run_pipeline_for_account()` | **瘦身 223 → ~30**:删 if-else,只剩"提交 + 跑 runner + 写 log" |
| API endpoint | `geo/api/sentiment.py` | `GET /api/sentiment/{id}/runs/latest` | **新增 ~30 行** endpoint |
| sentinel HTTP 客户端 | `geo/services/sentinel_client.py` | 18 写方法 + 6 读方法 | **不动** |
| 调度器 | `geo/services/sentiment_scheduler.py` | cron + leader | **不动**(`max_instances=1` 保留) |

### 4.2 sentinel-service

**全部不动**。`service.py` 779 行 + 18 写 RPC(4 主 + 14 crawler)+ 6 读端点 + analyzer/brief/response 业务逻辑,一行不改。

### 4.3 Frontend

| 模块 | v1→v2 |
|---|---|
| `StatusBanner.tsx` | 74 → ~120,加 stage 级进度展示;`failed` 仍静默 |
| API client | 加 `getLatestRun(accountId)` 方法,~10 行 |

---

## 5. 数据模型

### 5.1 主库(GEO,Postgres/SQLite)

**复用** `sentiment_run_logs`:`id` 作为 `run_id`,无需加 `run_id` 列。原有 `started_at` / `ended_at` / `status` / `stats_json` 字段足够表达 run 级状态。

**新增** 1 张表:

| 表 | 主键 | 用途 | 关键字段 |
|---|---|---|---|
| `pipeline_stage_runs` | `(run_id, stage_id, attempt)` | 单 stage 单次执行 | `run_id` (FK→`sentiment_run_logs.id`), `stage_id`, `attempt`, `status`, `started_at`, `ended_at`, `error`, `output_summary` (json) |

**唯一约束**(收敛 v1 三层防重入到一处):

```sql
CREATE UNIQUE INDEX idx_run_active
  ON sentiment_run_logs(account_id)
  WHERE status IN ('pending', 'running');
```

同一 `account_id` 只能有一个未完成 run,scheduler / 手动 / 用户点击重复入队都会因约束冲突直接拒绝。这一条直接取代主库 `sentiment_accounts.last_run_status='running'` 的语义。

**migration**:1 个 alembic(新增 1 表 + 1 索引,sentinel_accounts.last_run_status 字段保留作展示)。

### 5.2 业务库(per-account SQLite)

`posts` / `analyses` / `briefs` / `drafts` / `query_runs` 5 张表 schema **完全不动**,数据仍由 sentinel 写入。

### 5.3 多租户隔离

不动。sentinel 端的 monkey-patch `connect()` 完全不在重构范围。

---

## 6. 任务编排与状态机

### 6.1 状态机

`sentiment_run_logs.status`:
```
pending ─▶ running ─┬─▶ success
                    ├─▶ failed
                    └─▶ cancelled (Tier 2 不实现,Tier 3 再加)
```

`pipeline_stage_runs.status`:
```
pending ─▶ running ─┬─▶ success
                    ├─▶ failed (attempt < max → 自动回 pending 重试)
                    └─▶ skipped (上游失败 + on_failure=skip_downstream)
```

### 6.2 重入控制

| v1 守卫 | v2 (Tier 2) |
|---|---|
| scheduler `max_instances=1` | 保留 |
| pipeline `last_run_status='running'` 主库标记 | **改为** `sentiment_run_logs` 上的 unique index |
| `sentinel_cleanup` 60 分钟僵尸回收 | 保留(仍 cron),scope 改为扫 `sentiment_run_logs.status='running' AND started_at < now-60min` |

### 6.3 stage 失败策略

每个 stage 在 DAG 声明里带 `retry_policy`:

- `max_attempts`(默认 1;crawler fanout 不重试整个 stage,内部失败靠 sentinel 自身;LLM 调用 stage 设 2)
- `backoff`(指数,封顶 60s)
- `on_failure`:
  - `fail_run`:整个 run 失败(plan / monitor / analyze / brief 用)
  - `skip_downstream`:下游标 skipped,run 状态 = success_with_warnings
  - `continue`:run 继续,失败留痕(notify 用)

**14 crawler 的失败处理**:仍由 `crawl_fanout` stage 内部用 ThreadPoolExecutor 并行 + 老的 `try/except` 兜底。`stage_summary` 字段记录"哪几个 crawler 挂了",FE 可展示但**不算 stage 失败**。这与 v1 行为完全一致,只是从"散在 pipeline.py 里"搬到"集中在 crawl_fanout stage 里"。

### 6.4 单 PR 上线 + 整体回滚

Tier 2 不分 phase。一个 PR 包含:dag_runner + sentiment_dag + sentiment_pipeline 重写 + 主库 migration + FE StatusBanner 改造。

**回滚策略**:revert PR + 回滚 migration(drop `pipeline_stage_runs` 表 + drop unique index)。新表无业务数据依赖,丢弃只损失观测能力。

---

## 7. LLM 用法

**完全不变**。模型 / 并发 / 超时 / prompt 全部由 sentinel 端管,backend runner 看不到 LLM。

| 阶段 | 在哪 | 备注 |
|---|---|---|
| plan | sentinel `search/plan.py` | runner 通过 `monitor` stage 调用 |
| analyze | sentinel `analyzer/pipeline.py` | runner 通过 `analyze` stage 调用,LLM 内部并发不变 |
| brief | sentinel `brief/generate.py` | runner 通过 `brief` stage 调用 |
| draft | sentinel `response/draft.py` | 不在主 DAG,前端按需触发 |

---

## 8. 前端切片

| 入口 | v1→v2 (Tier 2) |
|---|---|
| `Sentiment.tsx` / `StatusBanner` | 加 stage 级进度展示;`failed` 仍静默(尊重 `feedback_sentiment_failed_silent.md`),`running` 时显示 "执行中:analyze (3/5 stages 已完成)" |
| `TodayTab` / `ArticlesTab` / `BriefsTab` | **不动**(数据源仍是 per-account SQLite,展示格式不变,见 `feedback_sentiment_display_format.md`) |
| `OnboardingWizard` / `SettingsPage` | **不动** |

新 endpoint:`GET /api/sentiment/{id}/runs/latest`,返回:

```json
{
  "run_id": 1234,
  "status": "running",
  "started_at": "2026-05-08T10:05:00Z",
  "stages": [
    {"stage_id": "monitor",      "status": "success", "started_at": "...", "ended_at": "..."},
    {"stage_id": "crawl_fanout", "status": "success", "started_at": "...", "ended_at": "...", "summary": {"failed_sources": ["xueqiu", "tieba"]}},
    {"stage_id": "analyze",      "status": "running", "started_at": "...", "ended_at": null}
  ]
}
```

---

## 9. 关键设计决策

1. **runner 跑在 backend,不下放到 sentinel**
   - **决策**:sentinel 完全不动,runner 在 backend 进程内
   - **理由**:本轮目标是"显式化 DAG + stage 状态可见 + stage 级 retry",这三件事不需要跨进程;下放 runner 收益是"HTTP 长连接消失",但 v1 实测这不是真正的痛点
   - **取舍**:HTTP 1200/1800s 超时仍在;sentinel API 没瘦身。未来若需要可升 Tier 3

2. **DIY runner,不引入 framework**
   - **决策**:写 ~300 行 `dag_runner.py`,不用 Prefect / Dagster / Airflow / Celery
   - **理由**:量级不匹配(每小时 N 账号);framework 概念会反客为主
   - **取舍**:未来若需 asset lineage,迁 Dagster

3. **14 crawler 不拆 DAG 节点,留在一个 stage 内 fanout**
   - **决策**:`crawl_fanout` stage 内部用 ThreadPoolExecutor + 14 次 `crawl_*` RPC
   - **理由**:与 v1 行为一致,迁移风险最小;14 节点拆出来的收益("贴吧爬虫挂了 3 次"这种粒度)在 stage_summary 里也能给到
   - **取舍**:单个 crawler 不能独立 retry / 补跑(只能整个 fanout 重跑)

4. **主库复用 `sentiment_run_logs.id` 作为 run_id**
   - **决策**:不开新表 `pipeline_runs`,沿用现有 `sentiment_run_logs`
   - **理由**:字段已经够用(status / started_at / ended_at / error / stats_json);新增表 = 多一份 schema 同步成本
   - **取舍**:如果未来要支持"一个 account 同时跑两个不同 kind 的 run",得加列

5. **唯一索引取代 `last_run_status='running'`**
   - **决策**:partial unique index on `sentiment_run_logs(account_id) WHERE status IN (pending, running)`
   - **理由**:数据库层面的强约束,任何来源(scheduler / 手动 / API)重复入队都会被拒绝
   - **取舍**:Postgres 支持 partial index,SQLite 也支持(3.8+)。若部署到不支持的库需要改用应用层校验

6. **DAG 是静态 Python 对象,声明式**
   - **决策**:`sentiment_dag.py:SENTIMENT_DAG = DAG([...])`,import 时确定
   - **理由**:可读、可 diff、IDE 友好;不需要"按账号配置生成不同 DAG"的灵活性

---

## 10. 与 v1 的差异(逐点)

| v1 节 | v1 现状 | v2 (Tier 2) |
|---|---|---|
| §2 物理拓扑 | sentinel = 14 crawler + 4 主 RPC server,backend 编排 | sentinel **完全不动**;backend 编排层换为 DAG runner |
| §3 数据流 | 隐式 DAG 写在 backend pipeline.py | 显式 DAG 在 backend `sentiment_dag.py`,runner 解释执行 |
| §4 模块清单 | 6 sentinel 子模块 | 6 sentinel 子模块不动;backend 加 `dag_runner.py` + `sentiment_dag.py` |
| §5 backend 编排 | 调用 18 RPC + try/except 14 次 | 调用 18 RPC(不变)+ runner 统一管 retry / 状态 |
| §6.1 主库 | `sentiment_run_logs` 等不变 | 加 `pipeline_stage_runs` 表 + unique index |
| §6.2 业务库 | 5 表 | 5 表(不动) |
| §6.3 多租户隔离 | monkey-patch connect | 不动(sentinel 全不动) |
| §7 前端 | StatusBanner 顶层 4 态 | StatusBanner + stage 级进度 |
| §8.6 状态机三层守卫 | scheduler / pipeline / cleanup | scheduler + unique index + cleanup(改 scope) |

---

## 11. 上线路径

### 单 PR(预计 5-8 工作日)

PR 内容(从下往上):

1. 主库 migration:加 `pipeline_stage_runs` 表 + `sentiment_run_logs` partial unique index
2. backend `dag_runner.py` ~300 行 + 单元测试
3. backend `sentiment_dag.py` ~80 行(静态 DAG 声明)
4. backend `sentiment_pipeline.py` 瘦身 223 → ~30 行
5. backend `geo/api/sentiment.py` 加 `GET /runs/latest` endpoint
6. frontend `StatusBanner.tsx` 改造,加 stage 进度

### 上线观察(2 周)

- 指标对比:run 失败率 / 单 run 总耗时 / per-stage 耗时分布
- 期望:run 失败率与 v1 一致;stage 级耗时与 v1 内部行为一致;stage_runs 表无意外增长

### 回滚

- revert PR
- 回滚 alembic(drop table + drop index);丢弃 `pipeline_stage_runs` 数据(只是观测,无业务依赖)

---

## 12. 风险与未决项

- [ ] **runner 自身的 bug 影响所有账号**:v1 是 200 行 imperative 代码,v2 是新写 300 行 runner。**缓解**:充分单测(retry / 拓扑 / 状态写入);本周代码冻结期不上
- [ ] **`pipeline_stage_runs` 增长率**:每 run × 5-7 stage × 平均 1.x attempt ≈ 10 行/run,每小时 N account → 每天 ~240N 行。N=100 时 24k/day,需要保留策略(建议 30 天后归档或删除)
- [ ] **partial unique index 的兼容性**:Postgres OK;SQLite 3.8+ OK;如果部署目标包含老 SQLite 需降级方案(应用层校验)
- [ ] **stage_id 命名稳定性**:一旦发布,stage_id 不能改名,否则历史 stage_runs 对不上账。**约定**:stage_id 在 sentiment_dag.py 顶部常量化,变更走 deprecate 流程
- [ ] **DAG 学习成本**:开发者从"读代码顺序"变成"读 DAG 拓扑"。**缓解**:`sentiment_dag.py` 顶部画 ASCII DAG 注释 + 维护本文档 §3.1
- [ ] **HTTP 1200/1800s 超时仍在**:Tier 2 不解决。如果未来真的频繁命中(目前几乎从没命中),则升级到 Tier 3
- [ ] **未来升 Tier 3 的口子**:DAG 节点的 stage body 是 `def run(ctx): sentinel_client.xxx(...)` 形态,未来想把 stage body 换成"sentinel 端 worker 处理"只是函数体替换,DAG 结构不变。这是 Tier 2 的故意保留

---

## 13. 关键路径速查

| 想看 / 改 X | 去 Y |
|---|---|
| DAG 长什么样 | `backend/geo/services/sentiment_dag.py` |
| DAG runner 怎么调度 / 重试 | `backend/geo/services/dag_runner.py` |
| 加 / 改一个 stage 的实现 | `sentiment_dag.py` 节点的 stage body(通常调一次 `sentinel_client.xxx`) |
| 加 / 改 stage 的 retry 策略 | `sentiment_dag.py` 节点声明里的 `retry_policy=...` |
| run / stage 当前状态 | 主库 `sentiment_run_logs` + `pipeline_stage_runs` |
| backend 怎么提交 run | `sentiment_pipeline.run_pipeline_for_account()` |
| FE stage 进度从哪来 | `GET /api/sentiment/{id}/runs/latest` |
| 调度入口 | `backend/geo/services/sentiment_scheduler.py`(不动) |
| sentinel 端任何东西 | sentinel-service 一行不改,直接看 v1 文档 |
| LLM prompt | `services/sentinel-service/analyzer/prompts.py`(不动) |
| 业务库 schema | per-account `data/account_{id}/yuqing.db`(不动) |

---

## 14. Tier 2 → Tier 3 演进路径(参考)

如果未来 Tier 2 上线后真的需要更激进的重构,演进路径是:

1. **拆 14 crawler 为独立 DAG 节点**:把 `crawl_fanout` 在 `sentiment_dag.py` 里展开成 14 个 sibling 节点,DAG 形态变化但 stage body 仍调老 RPC。无需 sentinel 改动。
2. **stage body 从"HTTP 调用"换成"sentinel 端 worker 处理"**:在 sentinel 加 `POST /jobs` + `GET /jobs/{id}` 两个端点,stage body 从同步 HTTP 改为提交 + 轮询。HTTP 超时痛点消除。
3. **runner 整体下沉到 sentinel**:DAG 声明、runner、stages 全部搬到 sentinel,backend 只剩"提交 run + 查 status";即原 Tier 3 完整方案。

每一步独立,可分别立项,不强绑定。
