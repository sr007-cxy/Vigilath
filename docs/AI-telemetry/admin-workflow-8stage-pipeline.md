# Admin 工作流 ↔ 8-Stage GEO Automation Pipeline

> 文档目的:把当前 admin 后台串起来的页面 / 操作,逐一对应到 8-stage GEO automation pipeline 上,标清楚每一步走哪个文件、调哪个接口、对应 pipeline 的哪个环节,以及哪些环节当前没有 UI、由后台服务接管。

---

## 0. 8-Stage Pipeline 全景

```
Seed Prompt  →  Intent Expansion  →  Prompt Clustering  →  Content Generation
                                                                    │
                                                                    ▼
Reinforcement Loop  ←  Telemetry Collection  ←  AI Crawling/Retrieval  ←  Media Distribution
```

8 个阶段的职责:

| Stage | 名称 | 职责 |
|------:|------|------|
| 1 | Seed Prompt | 收集品牌信息 + 种子查询词 |
| 2 | Intent Expansion | 把种子词扩展成完整监控查询集 |
| 3 | Prompt Clustering | 聚类 + 输出关键词体系 / 七步模型 / 严重度分层 |
| 4 | Content Generation | 按聚类结果生成 25+ 篇初稿(每查询 1 篇) |
| 5 | Media Distribution | 选平台 / 媒介,发布初稿 |
| 6 | AI Crawling / Retrieval | 各引擎跑监控查询,看 AI 是否引用 |
| 7 | Telemetry Collection | 命中 / 排名 / 立场 / 事实性 数据回流 |
| 8 | Reinforcement Loop | 监测报告反哺,回去调种子词 / 重发初稿 |

---

## 1. 当前 admin 流程 → Pipeline 对应图

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Admin 工作流(当前实现)                                │
└─────────────────────────────────────────────────────────────────────────────────┘

  ① 账户管理                                          ┐
  /workbench/accounts                                 │   [Pre-Pipeline · 准备]
  AdminAccounts.tsx                                   │   开账户 = 创建租户
  · 列出所有用户 / 显示主题数                          │   不进入 pipeline
  · 「配置主题」按钮                                   ┘
            │
            ▼
  ② 主题配置 / 新增主题                                ┐
  /workbench/accounts/:userId/topics                  │   ╔═══════════════════╗
  AdminAccountTopics.tsx                              │   ║ Stage 1           ║
  · 填品牌信息 + 种子查询 + 监控查询                   │──▶║ Seed Prompt       ║
  · POST /api/ai-telemetry/topics/admin               │   ║ (品牌 + 种子词)    ║
  · admin 创建直接 approved,绕过审核                  ┘   ╚═══════════════════╝
            │
            ▼
  ③ 审核 / 展示「审核通过」                             ┐  ╔═══════════════════╗
  /workbench/review · Review.tsx                      │  ║ Stage 2           ║
  · listTopicReviews / patchTopic                    │──▶║ Intent Expansion  ║
  · 编辑 brand profile / seeds / queries              │  ║ (扩展监控查询)     ║
  · approveTopic → 初稿方案                      ┘  ╚═══════════════════╝
            │
            ▼
  ④ 初稿方案 ★                       ┐  ╔═══════════════════╗
  /workbench/topics/:topicId/solution                 │  ║ Stage 3           ║
  Solution.tsx                                        │──▶║ Prompt Clustering ║
  · 输入品牌官网 → 一键诊断                            │  ║ (聚类 + 关键词     ║
  · POST .../solution/generate                        │  ║  体系 + 七步模型)  ║
  · 输出:诊断分 / 严重度簇 / 执行分层 / 关键词        ┘  ╚═══════════════════╝
  · 「下一步」→ 生成执行计划书
            │
            ▼
  ⑤ 执行计划书                                         ┐  ╔═══════════════════╗
  /workbench/topics/:topicId/execution-plan          │  ║ Stage 4           ║
  ExecutionPlan.tsx                                   │──▶║ Content Generation║
  · GET .../execution-plan                            │  ║ (25+ 文档 / 30 天)║
  · 发文计划表:每个查询 → 一篇初稿                    │  ║ priority / 平台建议║
  · 显示 query coverage % / priority / 建议平台       ┘  ╚═══════════════════╝
            │
            ▼
  ⑥ 内容审核                                           ┐  ╔═══════════════════╗
  /workbench/content-review?topic=:topicId            │  ║ Stage 5           ║
  ContentReview.tsx                                   │──▶║ Media Distribution║
  · selectForReview / approveDoc / rejectDoc          │  ║ (按平台 / 媒介分发)║
  · publishDoc { platform, media }                    │  ║                   ║
  · 状态:draft → pending → approved → published     ┘  ╚═══════════════════╝

═════════════════════════════════════════════════════════════════════════════════
                         以下进入「后台自动管线」(无 admin 操作页)
═════════════════════════════════════════════════════════════════════════════════

  ⑦ AI 抓取 / 召回                                     ┐  ╔═══════════════════╗
  services/browser-service/                           │  ║ Stage 6           ║
  ├ doubao_browser.py  ├ deepseek_browser.py          │──▶║ AI Crawling /     ║
  ├ qwen_browser.py    ├ wenxin_browser.py            │  ║ Retrieval          ║
  └ yuanbao_browser.py                                │  ║ (各引擎跑监控查询) ║
  · 跑发布后的查询,看 AI 是否引用                     ┘  ╚═══════════════════╝
            │
            ▼
  ⑧ 遥测采集                                           ┐  ╔═══════════════════╗
  services/telemetry-service/                         │  ║ Stage 7           ║
  + sentinel-service/                                 │──▶║ Telemetry         ║
  · BrandGrowth 看板:Queries / Matrix / Responses    │  ║ Collection         ║
  · /backfill_topic_api.py 回填                       │  ║ (命中 / 排名 / 立场)║
  · sentiment / stance / factuality                   ┘  ╚═══════════════════╝
            │
            ▼
  ⑨ 反哺回环                                           ┐  ╔═══════════════════╗
  /brand-growth/insights · /sources · /competitors    │  ║ Stage 8           ║
  · 监测报告(命中筛选 / 顶部 chip)                  │──▶║ Reinforcement Loop║
  · 异常 → 回 ③ 调 seed / queries 或 ④ 重发初稿       │  ║ (诊断 → 调种子词)  ║
  ◀━━━━━━━━━━━━ 反馈回到 ③ / ④ ━━━━━━━━━━━━━━━━━━━━━┘  ╚═══════════════════╝
```

---

## 2. 步骤映射速查表

| # | Admin 步骤 | 路由 / 文件 | 主要接口 | 对应 Pipeline Stage |
|---|---|---|---|---|
| ① | 账户管理 | `/workbench/accounts` · `AdminAccounts.tsx` | `GET /api/ai-telemetry/accounts` | Pre — 租户准备 |
| ② | 新增主题 | `/workbench/accounts/:userId/topics` · `AdminAccountTopics.tsx` | `POST /api/ai-telemetry/topics/admin` | **1. Seed Prompt** |
| ③ | 审核通过 | `/workbench/review` · `Review.tsx` | `listTopicReviews` / `patchTopic` / `approveTopic` | **2. Intent Expansion** |
| ④ | 初稿发难(原"战略方案") | `/workbench/topics/:topicId/solution` · `Solution.tsx` | `POST .../solution/generate` | **3. Prompt Clustering** |
| ⑤ | 执行计划书 | `/workbench/topics/:topicId/execution-plan` · `ExecutionPlan.tsx` | `GET .../execution-plan` | **4. Content Generation** |
| ⑥ | 内容审核 / 发布 | `/workbench/content-review?topic=:topicId` · `ContentReview.tsx` | `selectForReview` / `approveDoc` / `publishDoc` | **5. Media Distribution** |
| ⑦ | 引擎抓取 | `services/browser-service/*_browser.py` | 内部调度 | **6. AI Crawling / Retrieval** |
| ⑧ | 数据回流 | `services/telemetry-service` + `sentinel-service` | `backfill_topic_api.py` 等 | **7. Telemetry Collection** |
| ⑨ | 监测报告反哺 | `/brand-growth/*` · `Insights.tsx` / `Sources.tsx` / `Competitors.tsx` | BrandGrowth API | **8. Reinforcement Loop** |

---

## 3. 术语对齐

- **战略方案 → 初稿发难**:本次重命名,指 Solution.tsx 输出的"诊断报告 + 关键词体系 + 七步模型"文档,定位为整个 pipeline 的"起手稿"
- **执行计划书**:Stage 4 的发文排期表(25+ 篇 / 30 天),含 query coverage %、priority、建议平台
- **初稿(draft docs)**:每个监控查询自动生成的单篇内容,生命周期 `draft → pending_review → approved → published`

---

## 4. 当前断点 / 待补

| 位置 | 现状 | 影响 |
|---|---|---|
| ④ → ⑤ | 手动「下一步」按钮 | 不阻塞,但需 admin 点击触发 |
| ⑥ → ⑦ → ⑧ | 后台任务,无中间态 UI | admin 看不到"在跑哪条查询""为何卡住" |
| ⑨ → ③ / ④ | 无自动闭环 | 只能人工读报告 → 回 Review 改 seed 或重发初稿 |

后续如果要把 pipeline "拉直",优先补 ⑥ → ⑧ 的可视化进度条,以及 ⑨ 异常 → 一键回 ③ 的快捷入口。

---

## 5. 后端服务责任划分

| 服务 | 负责阶段 |
|---|---|
| `browser-service` | Stage 6(抓取)|
| `telemetry-service` | Stage 1 主题录入、Stage 2 扩展、Stage 4 生成、Stage 7 采集 |
| `sentinel-service` | Stage 8(监测 / 反哺信号)|
| 前端 admin | Stage 1–5 的人工审核 / 触发 |
