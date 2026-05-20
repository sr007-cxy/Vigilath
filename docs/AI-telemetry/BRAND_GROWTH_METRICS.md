# 品牌增长 — 功能模块 / 指标定义 / 判定口径

> **覆盖范围**:`/brand-growth` 主页 + 7 个子页(信源 / 平台 / 竞品 / 矩阵 / 洞察 / 监测问题 / 原始引用 / 投放战果)。
> **后端代码**:`backend/geo/api/ai_telemetry.py`(REST 接口) + `backend/geo/models/ai_telemetry.py`(ORM + pydantic schema)。
> **前端代码**:`frontend/src/pages/BrandGrowth/`(`index.tsx` / `Sources.tsx` / `Engines.tsx` / `Competitors.tsx` / `Matrix.tsx` / `Insights.tsx` / `Queries.tsx` / `Responses.tsx` / `Published.tsx`)。
> 本文档目标:**每一个数都讲清楚出处 + 计算口径 + 边界条件**,不依赖代码也能复述。

---

## 0. 共同基底(读后面任何一节前先看这里)

### 0.1 数据流总览

```
admin workbench 配置 topic(target + queries[] + engines[])
        │
        ▼
telemetry-service 调度  →  AI 引擎跑批
        │
        ▼
落 AiTelemetryResponseORM        ← 单次 (query × engine) 答复 + LLM 后处理
        │
        ▼
增量更新 AiTelemetryQueryHitORM    ← (query × engine) 维度累计:total_runs / total_hits / first_hit_at
        │
        ▼
ai_telemetry.py 各 GET 端点    ← 按需聚合
        │
        ▼
BrandGrowth React 页面        ← 渲染
```

### 0.2 关键 ORM 字段(每个指标都从这里来)

| 表 | 字段 | 含义 |
|---|---|---|
| `AiTelemetryTopicORM` | `target` | 品牌主词 |
| | `target_aliases_json` | 别名列表(`[str]`),与 `target` 一起组成 `brand_keywords` |
| | `queries_json` | 监测问题列表;每项可以是 `str` 或 `{text, cluster_id}`(picker 端聚类后写入) |
| | `engines_json` | 该 topic 跑哪些引擎(`["deepseek","doubao",...]`) |
| | `industry` | 行业分组键,行业基准用 |
| | `clusters_json` | picker 端聚类后的簇定义 `[{cluster_id, label}]` |
| | `created_at` | topic 创建时间,首次命中天数差用 |
| `AiTelemetryResponseORM` | `engine`, `query`, `answer` | 一次跑批的原文 |
| | `hit: bool` | runner 在 `answer` 里大小写不敏感匹配 `brand_keywords` 任一是否命中 |
| | `brand_rank: int?` | LLM 抽出的「品牌是答复中第几个被提到」(1-based;未提到 = None) |
| | `mention_position: str?` | LLM 抽出的「品牌出现的段落位置」: `lead` / `body` / `tail` / `unknown` |
| | `citations_json` | `[{domain, title, url}]`,LLM 答复给出的引用 URL |
| | `competitors_json` | `[{name, count}]`,LLM 抽出的竞品提及 |
| | `error` | 跑批异常字符串;**所有聚合都 filter `error IS NULL`** |
| | `created_at` | 跑批时间(period 切窗用) |
| `AiTelemetryQueryHitORM` | `query`, `engine` | 唯一键 `(topic_id, query, engine)` |
| | `total_runs` / `total_hits` | 该 cell 累计跑批数 / 累计命中数(全生命周期,不切 period) |
| | `first_hit_at` | 该 cell 首次命中时间;矩阵首命中时间线用 |
| | `first_hit_response_id` | 首次命中的 response 引用 |
| | `last_checked_at` | 最近一次跑批时间 |

### 0.3 全站共用参数

| 参数 | 取值 | 行为 |
|---|---|---|
| `period` | 默认 30,clamp `[1, 90]`(`max(1, min(period, 90))`) | shell 顶栏 picker 控制;**只切 `Response` 的 `created_at` 窗口,不切 `QueryHit`(后者是全生命周期)** |
| `topic_id` | URL `?topic=` | 主题 picker 选定;权限校验 `topic.user_id == current_user.id`,失败 404 |
| `brand_keywords` | `[topic.target] + topic.target_aliases_json` 去空 | runner / 后端聚合 / 前端 owned 判定**三方同源** |
| `MIN_INDUSTRY_BASELINE_SAMPLES` | `3`(`ai_telemetry.py:1898`)| 行业基准至少需要 3 个**同行业 + 期内有 response** 的 topic 才返回 |

---

## 1. 主页(`/brand-growth`)

主页是品牌增长的「门面」,聚合 5 类视觉块。

### 1.1 顶部 3 大数 — `TopMetricsRow`

后端 `GET /topics/{id}/overview` → `OverviewOut`,前端 `index.tsx:55`。

| 指标 | label | 字段 | 公式 | 取值含义 |
|---|---|---|---|---|
| 推荐总词数 | 「Total Citations」 | `overview.citations.value` | `SUM(len(citations_json))` 跨所有周期内 response | AI 引擎在所有问题里给出的引用 URL 总数(含错误响应也算 0)|
| 权威媒体推荐数 | 「Authoritative Citations」 | `overview.owned_split.owned` | 单条 citation 的 `domain.lower()` 命中 `brand_keywords.lower()` 任一(`k in d`) | 自家域名 / 含品牌字串的 domain 被引次数 |
| 第三方引用总数 | 「Third-party Citations」 | `overview.owned_split.other` | `total_citations - owned`(后端直接相减,不重新走过滤) | 除自家外的所有 citation |
| Δ% 变化 | 红/绿小箭头 | `*.delta_pct` | `(curr - prev) / prev × 100`;`prev=0` 时为 `None`(不渲染) | 与上一窗口(`period_start - period`)同周期对比 |

**点击跳转**:推荐总词数 → `/responses`,权威 → `/sources?filter=owned`,第三方 → `/sources?filter=third_party`。

**口径细节**:
- citations 是「引用条数」,**不是「唯一域名数」**。同一答复出现同一 URL 3 次会算 3
- owned 判定是 `domain.includes(brand_keyword)` 的子串匹配,不是精确等于。例如 `target="Acme"`,domain `"acme.help.zendesk.com"` 算 owned

### 1.2 雷达 5 维 — `RadarBlock`

后端 `GET /topics/{id}/position-breakdown` → `PositionBreakdownOut`,聚合函数 `_aggregate_position_breakdown()`(`ai_telemetry.py:1813`)。**全部基于成功 response(`error IS NULL`)聚合,记为 `total_ok`**。

| 维度 | 字段 | 公式 | 业务含义 |
|---|---|---|---|
| Top1 占比 | `top1_pct` | `COUNT(brand_rank == 1) / total_ok × 100` | 答复里品牌**第一个**被提及的占比 |
| Top3 占比 | `top3_pct` | `COUNT(brand_rank ≤ 3) / total_ok × 100` | 答复里品牌进前 3 的占比 |
| Top5 占比 | `top5_pct` | `COUNT(brand_rank ≤ 5) / total_ok × 100` | 答复里品牌进前 5 的占比 |
| 可见占比 | `visible_pct` | `COUNT(hit == True) / total_ok × 100` | 不管排第几,只要答复里提到就算 |
| 被引用占比 | `source_pct` | `COUNT(DISTINCT query WHERE 任意 hit) / total_queries × 100` | **维度从 response 切到 query**:有多少监测问题至少被命中过一次 |

**`total_queries` 计算**(用于 source_pct 分母):
```
解析 topic.queries_json,对每项:
  - str 且非空 → +1
  - dict 且 text 非空 → +1
```

**行业基准多边形**(`industry_baseline`):
- 取**同 `industry` 字段**的所有 topic
- 各算 `breakdown`,然后**逐维度取 P50**(`_compute_industry_baseline()`,`ai_telemetry.py:1901`)
- 任一档不满足返回 `None`,前端不渲染基准多边形 + 不渲染「行业 P50 X.XX%」小字:
  1. 当前 topic `industry` 为空 → 直接 `None`
  2. 同行业 topic **总数** < 3
  3. 同行业里**期内有 response** 的 topic 数 < 3
- 注意 P50 偶数取「中间两位的均值并保留 2 位小数」

**雷达画法**(`index.tsx:103`):
- 5 个顶点顺序逆时针(从 12 点钟起):Top1 / 可见 / 被引用 / Top5 / Top3
- 蓝色多边形 = 本品(填充 `rgba(59,130,246,0.25)`)
- 灰色多边形 = 行业 P50(填充 `rgba(148,163,184,0.18)`)
- 顶点 label 可点击,跳到 `/matrix?layer=<dim>` 滤镜模式

### 1.3 功能入口 6 卡 — `EntryCardGrid`

纯导航卡,sub 文案动态拼:

| 卡 | 标题 | sub 文案来源 |
|---|---|---|
| 信源分析 | 「Sources」 | `overview.top_domains[0].domain`(无数据时显示「暂无信源数据」)|
| 平台分析 | 「Engines」 | `overview.engines_covered.value / overview.engines_total` |
| 竞品分析 | 「Competitors」 | 静态 「看竞品 + 替代证据 →」 |
| 问题命中矩阵 | 「Query Matrix」 | 静态 「查询 × 引擎 命中详情 →」 |
| 智能洞察 | 「Insights」 | 静态 「周报 + 优化建议」 |
| 监测问题 | 「Tracked Queries」 | 静态 「只读 · 在 admin 工作台编辑」 |

### 1.4 核心指标 4 卡 — `CoreMetricsPanel`

复用 §1.2 的 `position-breakdown`,把 4 个维度(Top1 / 可见 / Top5 / 被引用)**单独成卡**:
- 主数 = 该维度 % 值
- 卡底小字 = `行业 P50 X.XX%`,无行业基准时显示「行业基准样本不足」
- 点卡片跳 `/matrix?layer=<dim>`

(Top3 没有单独成卡,是有意的 — 雷达里 Top1 / Top3 / Top5 三层并存,卡片层去掉 Top3 是 PM 觉得「Top3 信息量被 Top1+Top5 夹击稀释」)

### 1.5 报告明细 + 投放战果 — 主页两块卡

| 块 | 数据源 | 显示 |
|---|---|---|
| 报告明细 | `GET /topics/{id}/briefings?limit=10` | 最近 6 条周报标题(`period_start → period_end`) + 生成时间 |
| 投放战果 | `contentApi.listDocs(topic_id, {status:'published'})` | 最近 6 条已发布稿件标题 + `publish_targets[0].platform · media` |

「查看全部 →」分别跳 `/insights` 和 `/published`。

---

## 2. 信源分析(`/brand-growth/sources`)

`Sources.tsx` + `overview.top_domains` / `overview.owned_split`。

### 2.1 顶部 3 stat tile

| 指标 | 取值 | 含义 |
|---|---|---|
| 总引用 | `overview.citations.value` | 同主页指标(§1.1) |
| 唯一域名数 | `overview.top_domains.length` | **注意是 ≤ 10**(后端排序后取 Top 10)— 真实唯一 domain 可能更多,但前端只能拿到 Top 10 这个口径 |
| 自有占比 | `overview.owned_split.owned_pct` | `owned / total_citations × 100`,1 位小数;`total=0` 时 `0.0` |

### 2.2 自有 vs 第三方 donut

```
slices = [
  { label: "自有 / 权威", value: owned_split.owned, color: #10b981 绿 },
  { label: "第三方",      value: owned_split.other, color: #94a3b8 灰 },
]
centerText = owned_pct.toFixed(1) + "%"
```

### 2.3 Top 域名构成 donut

```
top 7 from overview.top_domains
+ 其它 = sum(top_domains[7:].count)
```

### 2.4 Top 引用域名横条图

```
domains 列表过滤:
  filter=all          → overview.top_domains 全列
  filter=owned        → top_domains.filter(d => isOwned(d.domain))
  filter=third_party  → top_domains.filter(d => !isOwned(d.domain))
isOwned(d) = brandKeys.some(k => d.toLowerCase().includes(k.toLowerCase()))
```

每行显示 `domain · count · pct%`,**右侧有「看样本 →」**触发抽屉。

### 2.5 抽屉 — 单 domain 引用样本

```
GET /topics/{id}/responses?domain=<d>&period=&limit=
```

抓最近若干条 hit 引用了该 domain 的 response,显示 query + answer 截段 + 跳引擎源 URL。期内无样本时显示「该域名近 N 天无引用样本」。

---

## 3. 平台分析(`/brand-growth/engines`)

`Engines.tsx`,数据复用 `overview` 接口,前端二次切片。

| 指标 | 计算 | 含义 |
|---|---|---|
| 引擎覆盖 | `engines_covered.value / engines_total` | 本期跑出 ≥1 成功 response 的引擎 / topic 配置的总引擎数 |
| 各引擎引用次数(条形图) | 把 `trend[]` 横切到 engine 维度:`engine_total[e] = SUM_d(bucket[d][e])` | 各日各引擎的 citation 数累加 |
| 引擎 × 域名热力图 | `engine_domain_matrix[engine][domain]` | 仅保留全局 Top 10 domain;颜色深浅按 cell count 归一化 |

**`engine_domain_matrix` 构造**(`_domain_stats`,`ai_telemetry.py:1319`):
```
对每条 response(error IS NULL):
  对其 citations_json 里每条 citation:
    domain = c.domain.lower().strip(),非空才计
    engine_domain[response.engine][domain] += 1
最后只保留 domain ∈ Top 10 的列
```

> 热力图深色 = 该引擎严重依赖该 domain;浅色 = 引用过但不多;空白 = 0 次。

---

## 4. 竞品分析(`/brand-growth/competitors`)

`Competitors.tsx`,两个端点合起来:
- `GET /topics/{id}/share-of-voice` → 卡 1 + 卡 2
- `GET /topics/{id}/competitor-substitutions` → 卡 3

### 4.1 SAIV 卡(声量份额)

| 指标 | 字段 | 公式 |
|---|---|---|
| 品牌提及次数 | `brand_count` | `COUNT(Response WHERE hit=True AND error IS NULL)` 近 period |
| 竞品提及次数总和 | `competitors_count_total` | `SUM_{r}(SUM_{c ∈ r.competitors_json}(c.count))` 跨所有期内 response |
| **SAIV %** | `saiv_pct` | `brand_count / (brand_count + competitors_count_total) × 100`,1 位小数 |
| donut 主体 | 两片 | 「本品(target)」 vs 「竞品总和」 |

注意:
- 一条 response 内**多次提到竞品**走 `competitors_json[i].count` 累加,所以同 response 既能贡献 brand_count(=1)也能贡献多次竞品 count
- LLM 抽出失败 / `competitors_json IS NULL` 不计

### 4.2 竞品排行 Top 10 + 命中位置分布

**Top 10 横条**:`competitors[]` 按 `count` 降序前 10,每条 `{name, count, pct = count / total × 100}`(total = brand + 所有竞品)。下方 chip 行可单选 → 4.3 表筛选。

**命中位置分布**(`position_dist`):仅 hit=True 的 response,按 `mention_position` group by:

| 字段 | 含义 | 业务解读 |
|---|---|---|
| `lead` | 答复**首段**就提到品牌 | 强曝光;LLM 首推 |
| `body` | 中段提到 | 中等曝光 |
| `tail` | 末段才提 | 弱曝光;可能只在「另外」「也可以考虑」里 |
| `unknown` | 无法定位段落(后处理失败) | 数据缺失 |

mini-bar 按比例填色:`lead=绿 / body=蓝 / tail=橙 / unknown=灰`。

### 4.3 被替代证据(`competitor-substitutions`)

**口径**:「**提了竞品但没提我**」的 query 列表。
```
近 period 天 + hit=False(没命中本品) + error IS NULL + competitors_json IS NOT NULL
按 (query, competitor.name) 聚合 count,保留第一条 answer 的 snippet 作为证据
按 competitor_count 降序排,默认 limit 50
```

每行:`query · 竞品 · 次数 · 证据 snippet · 看矩阵 →`(跳 `/matrix?q=<query>` 看该 query 的所有引擎答复)。

**(可选)单竞品过滤**:在 4.2 点 chip 触发,前端 `subs.items.filter(i => i.competitor_name === selectedComp)`,**不是再调一次接口**。

### 4.4 优选率 `optimal_rate_pct`(隐藏字段,目前页面没直接展示)

```
optimal_rate_pct = SUM(QueryHit.total_hits) / SUM(QueryHit.total_runs) × 100
```
**注意**:这个走 `AiTelemetryQueryHitORM` 的全生命周期累加,**不切 period**。和 `visible_pct` 长得像但口径不同 — 前者是 cell 累计,后者是 period 切片 per-response。

---

## 5. 问题命中矩阵(`/brand-growth/matrix`)

`Matrix.tsx`,后端 `GET /topics/{id}/tracking-matrix` → `TrackingMatrixOut`。

### 5.1 矩阵构造

```
queries = topic.queries_json 抽 text(str 或 dict 都吃)
engines = topic.engines_json
cell_rows = AiTelemetryQueryHitORM WHERE topic_id=<id>(全生命周期)

填充 queries × engines 笛卡尔积:
  - 库里有 → 用 ORM 行
  - 库里没 → 内存填 status='pending', total_runs=0, total_hits=0(不入库)
```

### 5.2 单元格颜色档位(`Matrix.tsx:54 cellColor`)

颜色由「该 cell 历史 hit response 里**最佳 brand_rank**」决定。前端先扫一遍 `responses[]` 算 `cellRank.get(query|engine)`:

| brand_rank | 颜色 | hex |
|---|---|---|
| 1 | 深绿 | `#15803d` |
| 2-3 | 中绿 | `#22c55e` |
| 4-5 | 浅绿 | `#86efac` |
| 命中但 rank 未抽出 | 极浅绿 | `#bbf7d0` |
| `total_hits = 0` 且 `status ∈ {pending, running}` | bg-input(占位)| — |
| `total_hits = 0` 且 status=done | 灰 | `#94a3b8` |

**Layer 滤镜**(`?layer=top1/top3/top5/visible/source`):不匹配的命中格降为半透明灰 `rgba(148,163,184,0.2)`,匹配格保持原色。`all` 显示所有原色。

### 5.3 命中率 KPI

```
total_cells = len(queries) × len(engines)
hit_cells   = COUNT(cell WHERE total_hits >= 1)
hit_pct     = hit_cells / total_cells × 100
```
显示在矩阵右上:「命中率 X.X% · hit_cells / total_cells」。

### 5.4 首次命中时间线(`timeline`)

每个 engine 在所有 cell 里取 `MIN(first_hit_at)`:
```
days_after_start = (first_hit_at - topic.created_at).days,负数 clamp 到 0
```
显示「<engine> 上线后第 X 天首命中(query=<x>)」;无 cell 命中时 `first_hit_at=None`。

### 5.5 点击 cell → 抽屉

```
GET /topics/{id}/responses?query=<q>&engine=<e>&limit=20
```
显示该 cell 历次答复时间线;每条:`时间 · ✓Top<n> 或 ✕未命中 · hit_excerpt(若有)或 answer.slice(0,240)`。

(更深的「LLM 单格诊断」按钮走 `POST /topics/{id}/cells/insight`,由 telemetry-service 同步生成 3-8s,产出 `CellInsightOut`:`verdict` / `summary` / `competitors_top3` / `recommendations[]`,可对生成结果 👍/👎/wrong 反馈。)

---

## 6. 智能洞察(`/brand-growth/insights`)

`Insights.tsx`,后端两端:
- `GET /topics/{id}/briefings?limit=30` 列表
- `POST /topics/{id}/briefings/generate` 触发生成(转发给 telemetry-service,180s 超时)

### 6.1 周报字段(`BriefingOut`)

| 字段 | 含义 |
|---|---|
| `id` | 周报主键,URL `?briefing=<id>` 可深链 |
| `period_start` / `period_end` | 周报覆盖的窗口(默认上一自然周)|
| `body_md` | LLM 生成的 markdown 正文。包含:本期 KPI 摘要 + 新命中 cell + 流失 cell + 趋势观察 |
| `kpi_snapshot` | 生成时刻的 KPI 快照 dict(`citations / visibility / saiv_pct / top1_pct`),用于报告里的「对比上一周」 |
| `top_actions[]` | `[{priority: 'P0'|'P1'|'P2', title, why, how}]` 优化建议列表(右侧渲染) |
| `delivered_email_at` | 邮件投递时间,空 = 未投递(默认未开启邮件) |
| `feedback_score` | 1-5 星,通过 `POST /briefings/{id}/feedback` 写入 |
| `llm_model` / `prompt_version` | 生成元数据,便于追溯版本 |
| `generated_at` | 生成时间 |

### 6.2 列表 + 详情布局

- 左列(1/3 宽):周报列表,选中态背景高亮。顶部有「新生成」按钮,期间显示「生成中…」并禁用
- 右列(2/3 宽):active 周报的 body_md + Recommendations。未选时显示「选择左侧周报查看,或点新生成」

> **生成的 LLM 配置**:`backend/services/telemetry-service` 的 `.env` 要配 `LLM_PROVIDER=glm`(或 `deepseek` / `qwen` / `openai`)+ 对应 API key。**未配时返回 stub**(body_md 是占位)。

---

## 7. 监测问题(`/brand-growth/queries`)

`Queries.tsx`,前端聚合 `tracking-matrix.cells` + 后端 `intent-breakdown`。只读 — 编辑入口在 admin workbench。

### 7.1 问题列表表

前端在矩阵 cells 上按 query 维度 reduce:

| 列 | 计算 |
|---|---|
| 问题 | `cell.query` |
| 命中率 | `total_hits / total_runs`(0 时记 0)|
| 跑批数 | `SUM(cell.total_runs WHERE cell.query == q)` 跨所有 engine |
| 命中数 | `SUM(cell.total_hits WHERE cell.query == q)` 跨所有 engine |
| 首次命中引擎 | 该 query 下 `first_hit_at` 最小的 cell.engine;无则「尚未命中」 |
| 操作 | 「查矩阵 →」跳 `/matrix?q=<q>` |

### 7.2 问题主题分布(`intent-breakdown`)

按 picker 端(`admin workbench`)聚出的 `cluster_id` 把本期 response 分组(`ai_telemetry.py:1401`)。

**簇定义**:`topic.clusters_json = [{cluster_id, label}]`,query 上的 `cluster_id` 来自 `queries_json[i].cluster_id`。

每簇返回 `ClusterBreakdownItem`:

| 字段 | 含义 |
|---|---|
| `cluster_id` / `label` | 簇 id 和人类可读标签(例如「价格类」「对比类」)|
| `query_count` | 该簇里的 query 数(topic 维度,不切 period)|
| `response_count` | 本期成功 response 数(query × engine,`error IS NULL`)|
| `mention_count` | response 里 `hit=True` 的数量 |
| `mention_rate` | `mention_count / response_count`(0 时 0.0,3 位小数) |
| `citation_count` | 该簇 response 的 `citations_json` 累加 |

**簇颜色档位**(前端):
- `mention_rate >= 0.5` → 蓝(健康)
- `0.25 ≤ rate < 0.5` → 橙(普通)
- `< 0.25` → 红(弱)

**uncategorized 兜底桶**:
- 老话题 `queries_json` 没 `cluster_id` 字段 → 全部累到这里(`cid = -1`)
- `clusters_json` 里没列出但 response 里出现过的 cluster_id → 也合并到这里

---

## 8. 原始引用(`/brand-growth/responses`)

`Responses.tsx`,后端 `GET /topics/{id}/responses?engine=&query=&period=&domain=&limit=`,返回 `ResponseOut[]`。

| 列 / chip | 取值 |
|---|---|
| 命中 chip | `hit=True` → 绿「命中」;否则灰「未命中」 |
| Top 标签 | `brand_rank` 非空时显示 `Top<n>` |
| 引擎 + 时间 | `engine + created_at` |
| 答案正文 | `answer`,默认折叠 240 字,「展开」全文 |
| `mention_position` | 在 chip 旁显示「开头 / 中段 / 末尾 / 未知」|
| 引用清单 | `citations` 展开;每条 `[domain] title (url)` |
| 跳源链接 | `source_url`(LLM 给的页面 URL,无则不显示)|
| 视频 | `video_url`(豆包等录制了浏览过程时有)|
| 过滤摘要 | 「引擎: X · 问题: Y · 近 N 天共 M 条」|

---

## 9. 投放战果(`/brand-growth/published`)

`Published.tsx`,数据源:`contentApi.listDocs(topic_id, {status:'published'})` + 反查 `Response.citations_json` 判定 AI 引用。

### 9.1 单稿展示字段

| 字段 | 含义 |
|---|---|
| `title` | 稿件标题 |
| `source` | `ai`(模板生成)/ `user`(人工提交);页面 chip 区分 |
| `publish_targets[]` | `[{platform, media, url}]`,平台 chip 跳外站(`target='_blank'`)|
| `publishedAt` | 发布时间 |
| 关联问题 | 稿件元数据里的 `related_query`,带「→ 看 AI 现在怎么答」链接跳 `/matrix?q=...` |

### 9.2 「AI 引用」绿章判定

```
该 publish_target.url 出现在任一 Response.citations_json[*].url(近 period)中
→ 显示 "Cited by <engine>"
```
反向追溯:点绿章可跳到 `/responses?engine=<x>&q=<related_query>` 看原文上下文。

### 9.3 筛选

- 顶栏 tab:全部 / 已被 AI 引用 / 未被引用
- 二级 chip:平台过滤(根据所有稿件的 `publish_targets[].platform` 去重生成)

---

## 附录 A — 后端常量速查

| 常量 | 值 | 位置 | 说明 |
|---|---|---|---|
| `MIN_INDUSTRY_BASELINE_SAMPLES` | 3 | `ai_telemetry.py:1898` | 行业基准最少需要 3 个同行业 + 期内有 response 的 topic |
| `period` clamp | `[1, 90]` | 各 endpoint 共用 `max(1, min(period, 90))` | 控制 `created_at >=` 切窗 |
| Top domains 截断 | 10 | `topic_overview()` 排序后 `[:10]` | 影响「唯一域名数」「热力图列」 |
| Briefing 列表上限 | 52 | `list_briefings`(`limit min(limit, 52)`)| 默认 30 |
| substitution 默认 limit | 50 | `get_competitor_substitutions(limit=50)` | 默认按 competitor_count 降序前 50 |
| cell drawer 历次答复条数 | 20 | 前端 `CellDrawer` 调 `listTopicResponses(..., limit:20)` | — |
| Matrix 页 responses 拉取上限 | 500 | `Matrix.tsx` `listTopicResponses(..., limit:500)` | 影响 cellRank 准确度,周期内 response > 500 时**部分 cell 颜色档可能不准** |

## 附录 B — 关键端点速查

| 路径 | Schema | 用途 |
|---|---|---|
| `GET /topics/{id}/overview` | `OverviewOut` | 主页 3 大数 + 信源 + 平台所有聚合(KPI + trend + top_domains + owned_split + engine_domain_matrix)|
| `GET /topics/{id}/position-breakdown` | `PositionBreakdownOut` | 雷达 5 维 + 核心 4 卡 + 行业基准 |
| `GET /benchmarks/industry?industry=` | `IndustryBenchmarkOut` | 行业基准(可独立查询,展示样本数)|
| `GET /topics/{id}/intent-breakdown` | `IntentBreakdownOut` | 问题主题聚类分布(queries 页底部)|
| `GET /topics/{id}/tracking-matrix` | `TrackingMatrixOut` | 命中矩阵 cells + 首命中 timeline(matrix / queries 页)|
| `GET /topics/{id}/share-of-voice` | `ShareOfVoiceOut` | SAIV + 竞品 Top 10 + 位置分布 + 优选率 |
| `GET /topics/{id}/competitor-substitutions` | `CompetitorSubstitutionOut` | 被替代证据列表 |
| `GET /topics/{id}/responses` | `list[ResponseOut]` | 原始 response 流(responses 页 / 信源抽屉 / matrix drawer / 绿章反查 通用 list) |
| `GET /topics/{id}/cells/drawer` | `CellDrawerOut` | matrix cell 抽屉的历次答复 + 已生成的 LLM 诊断 |
| `POST /topics/{id}/cells/insight` | `CellInsightOut` | matrix cell 触发 LLM 同步生成单格诊断(3-8s)|
| `GET /topics/{id}/briefings` | `list[BriefingOut]` | 周报列表 |
| `POST /topics/{id}/briefings/generate` | `BriefingOut` | 手动触发生成上一周周报 |

## 附录 C — Hover Hint 文案速查(`lang.ts`)

> 每个 InfoHint `?` 的悬浮文案。zh / en 双套(用户语言切换通过 `i18n.language` 判定)。**新人接手时,把这份表过一遍就能把品牌增长每个指标的口径背下来**。

### 主页(`/brand-growth`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintTopMetrics` | 推荐总词数 卡 + Sources 顶 stat | 本期 AI 引擎在所有问题里提到品牌的累计引用次数(SUM 答复里 citations 数组长度)。一条答复里同一 URL 出现多次会算多次 |
| `hintOwnedCitations` | 权威媒体推荐数 卡 | 上述总数中,被引域名包含品牌关键词(target + 别名,大小写不敏感子串匹配)的部分。例如品牌为 Acme 时 acme.help.zendesk.com 也算自有 |
| `hintOtherCitations` | 第三方引用总数 卡 | 总引用数 − 权威媒体引用数。能反映品牌在外部 / 第三方域名上的曝光面 |
| `hintRadar` | 雷达 5 维卡 | 5 个维度叠在一张图上看品牌健康度:Top1/Top3/Top5 是品牌在 AI 答复中是第几位提到,可见 = 任意提到,被引用 = 不同监测问题里至少被命中一次 |
| `hintEntries` | 6 入口块 | 点任意卡片进对应子页查看明细 |
| `hintCoreMetrics` | 核心指标 4 卡 | 基于 LLM 抽取的 brand_rank(品牌在答复里第几个被提到)算占比。行业 P50 来自同行业 ≥3 个租户的中位数,样本不足时不展示 |
| `hintBriefings` | 报告明细块 | LLM 每周自动总结的周报,含本期 KPI 摘要 + 新命中 / 流失 cell + 行动建议。点条目看详情 |
| `hintPublished` | 投放战果块 + Published 页 | 已发布的稿件清单。点 chip 跳外站;有"AI 引用"绿标表示该投放 URL 出现在 AI 答复的引用清单里 |

### 信源分析(`/sources`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintTopMetrics` | 顶部「总引用」stat | (同上,复用)|
| `hintSourcesUnique` | 顶部「唯一域名数」stat | 本期出现过的不同引用域名数。上限是后端返回的 Top 10 — 实际可能更多但只算 Top 10 内 |
| `hintSourcesOwnedPct` | 顶部「自有占比」stat | 权威媒体引用 / 总引用 × 100。比例越高说明 AI 答复越倾向引用品牌自家域名 |
| `hintSourcesFilter` | filter tab 旁 | 切换看全部 / 仅自有权威域名 / 仅第三方域名。自有判定 = 域名子串包含品牌关键词 |
| `hintSources` | 「Top 域名构成」donut 卡 | AI 答复里出现过的 citation domain 排序。Top 7 单独成色块,其余合并到「其它」桶 |
| `hintSourcesDomainEngine` | 「域名 × 引擎 引用拆分」卡 | 同一域名在不同 AI 引擎间的引用拆分。条形越长 = 该引擎引用该域名越多。点行查看引用样本 |

### 平台分析(`/engines`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintEngines` | 「各引擎引用次数」条形图卡 | 不同 AI 引擎对品牌相关 query 的引用频次(SUM 该引擎所有 response 的 citations 长度)|
| `hintEnginesOverview` | 「引擎概览」卡 | 每个引擎在本期跑批的引用总数。点卡片跳到该引擎的原始答复列表 |
| `hintEnginesHeatmap` | 「引擎 × 域名 热力图」卡 | 行 = 引擎,列 = Top 12 域名。颜色越深表示该引擎越依赖该 domain(归一化到该 domain 全局总数)。空白 = 0 次 |

### 竞品分析(`/competitors`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintCompetitors` | SAIV 卡 | SAIV = 品牌提及次数 / (品牌 + 全部竞品提及总次数) × 100。竞品提及来自 LLM 抽出的 competitors_json |
| `hintCompetitorsPosition` | SAIV 卡内「命中位置分布」 mini-bar | 命中 hit=True 的答复里,品牌出现的段落位置:lead=首段,body=中段,tail=末段,unknown=后处理未定位 |
| `hintCompetitorsTop` | 「竞品排行 Top 10」卡 | 本期被 LLM 抽出最多的竞品 Top 10,按提及次数降序。点 chip 可过滤下方「被替代证据」表 |
| `hintCompetitorsSubs` | 「被替代证据」卡 | "提了竞品但没提你"的 query 清单:hit=False 但 competitors_json 非空的答复,按 (query × 竞品) 聚合次数,展示证据 snippet |

### 问题命中矩阵(`/matrix`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintMatrix` | layer 切换栏旁 | 每个 (问题 × 引擎) cell 的累计命中状态。深绿 = Top1,中绿 = Top3,浅绿 = Top5,极浅绿 = 命中但 rank 未抽出,灰 = 未命中,占位灰底 = 还没跑过。点 cell 看历次答复 |
| `hintMatrixTimeline` | (后端 timeline 字段,目前 UI 未展示;预留) | 每个引擎在所有 query 里最早一次首命中的日期,显示「上线后第 X 天」。X = (first_hit_at − topic.created_at).days |

### 智能洞察(`/insights`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintInsights` | 左侧「周报列表」标题 | 左侧周报列表,点新生成手动触发上一自然周的周报;每条选中后右侧展示正文 + 建议 |
| `hintInsightsView` | 右侧周报详情顶部 | 周报正文(LLM 生成 markdown) + 优先级标记的建议行动列表。下方可对周报质量打分 1-5 |

### 监测问题(`/queries`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintQueries` | 顶部摘要行 | 所有监测问题的累计命中状况,只读 — 编辑 / 新增问题在 admin 工作台 |
| `hintQueriesTable` | 表头「命中率」列旁 | 每行 = 1 个监测问题。命中率 = total_hits / total_runs(全生命周期累计,不切 period)。点查矩阵看该 query 在各引擎的明细 |
| `hintIntentBreakdown` | 「问题主题分布」块标题 | 把语义相近的问题聚成一组(如"价格类"、"对比类"),看哪类问题品牌曝光最弱。条颜色:≥50% 蓝 / 25-50% 橙 / <25% 红 |

### 原始引用(`/responses`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintResponses` | 过滤摘要旁 | 原始 AI 答复流。chip 含命中状态(Top<n> / 未命中)、段落位置(开头 / 中段 / 末尾)、引用清单条数。展开看全文 + 引用列表 |

### 投放战果(`/published`)

| Hint key | 挂载位置 | zh 文案 |
|---|---|---|
| `hintPublished` | 顶部 filter 行 | (同主页,复用)|
| `hintPublishedCited` | 单稿「AI 引用」chip 行 | 该 publish_targets[].url 出现在 Response.citations_json 中,按引擎分组展示被引擎引用的次数 |

## 附录 D — 数据缺失态汇总

| 现象 | 触发条件 | 表现 |
|---|---|---|
| 雷达基准灰多边形不出现 | 同 `industry` 期内有 response 的 topic 数 < 3,或 `industry` 字段为空 | 雷达只有蓝(本品),底部「行业基准样本不足」|
| 核心指标卡底部小字消失 | 同上 | 显示「行业基准样本不足」替代「行业 P50 X.XX%」|
| 信源页 0 数据 | 期内 0 个成功 response 含 citations | 顶部 3 stat 全 0,donut / 横条空 |
| 平台页 0 数据 | 期内所有引擎都 0 成功 response | 引擎覆盖显示 `0 / N`,热力图空 |
| 竞品页空 | `competitors_json` 全空(LLM 未抽出 / 答复都 hit) | SAIV 显示 100% 本品,Top 10 列空,被替代证据空 |
| 矩阵全 pending | topic 刚建,没跑过任何 run | 矩阵全占位灰底,命中率 `0.0% · 0 / N` |
| 周报空 | topic 还没生成过任何 briefing | 主页周报块「暂无周报,可在智能洞察一键生成」,insights 页右侧「选择左侧周报查看,或点新生成」|

## 附录 E — 测试环境数据排查 SQL(`sqlite3 /opt/geo/backend/data/geo_checker.db`)

```sql
-- 1. 检查 topic 行业字段是否填写
SELECT id, name, target, COALESCE(industry,'<NULL>') AS industry FROM ai_telemetry_topics;

-- 2. 同行业 topic 数 + 期内有 response 的数量(诊断行业基准为什么没出)
SELECT
  COALESCE(t.industry,'<NULL>') AS industry,
  COUNT(DISTINCT t.id) AS topics_total,
  COUNT(DISTINCT CASE WHEN r.id IS NOT NULL THEN t.id END) AS topics_with_recent_data
FROM ai_telemetry_topics t
LEFT JOIN ai_telemetry_responses r
  ON r.topic_id = t.id
 AND r.created_at >= datetime('now','-30 days')
GROUP BY t.industry;

-- 3. 单 topic 期内 response 行数(判断雷达数据来源是否充分)
SELECT engine, COUNT(*) AS resp_count,
       SUM(CASE WHEN hit=1 THEN 1 ELSE 0 END) AS hits,
       SUM(CASE WHEN brand_rank IS NOT NULL THEN 1 ELSE 0 END) AS with_rank
FROM ai_telemetry_responses
WHERE topic_id = ? AND created_at >= datetime('now','-30 days') AND error IS NULL
GROUP BY engine ORDER BY resp_count DESC;

-- 4. 单 topic 的 QueryHit 累计状态(矩阵命中率从哪儿来)
SELECT query, engine, total_runs, total_hits, first_hit_at, last_checked_at
FROM ai_telemetry_query_hits WHERE topic_id = ? ORDER BY total_hits DESC;

-- 5. 周报历史
SELECT id, period_start, period_end, generated_at, llm_model, feedback_score
FROM ai_telemetry_topic_briefings WHERE topic_id = ? ORDER BY period_end DESC;
```
