# GEO 项目 vs 讯灵 流程对比

> 配套阅读:[`competitor-xunling.md`](./competitor-xunling.md)(讯灵自身的产品分析)。
> 本文专注**两边流程的并排对比 + 借鉴点筛选**,不重复讯灵的内部细节。
> 数据基线:本仓库 `backend/geo/` + `frontend/src/` 当前状态;讯灵 = `g3kefubu1` 测试账号实测(2026-05-27)。

---

## 0. 一句话定位差异

| | GEO(本仓库) | 讯灵 |
|---|---|---|
| **形态** | **诊断 + 内容生产**的 SaaS 工具,以 25 维体检 + topic stepper 为核心 | **代运营 + 媒体席位**的服务型 SaaS,以可发条数 + 监测看板为核心 |
| **核心壁垒** | citation match 闭环 + GEO Readiness 25 维方法论 + 舆情 | 5 类媒体发布渠道 + 抖音/字节系老用户数据沉淀 + 12 cell 真浏览器 probe |
| **运营介入度** | 用户自助为主,admin 在 workbench 介入审核 | 重运营,实名认证 + 抖音授权 + 客服对接 |

---

## 1. 两条流程并排图

```
GEO 项目(workbench TopicStepper,6 步)             讯灵(顶部主菜单,6 步)
─────────────────────────────────────────         ─────────────────────────────────
①  画像                                            ①  AI画像
    profile_json(6 模块表单)                          画像主体(实名认证+抖音授权)
    profile_extractor 从上传文件抽 LLM 解析            + 训练词(4 类场景)
                                                      + 图片/视频/资料素材
                ▼                                                ▼
②  诊断与方案预评估                                ② AI备课
    25 categories × 5 clusters                       蒸馏长尾池 → 文章生成(按 5 类媒体分)
    GEO Readiness Score 0~100 + 各维体检               (第三方新闻/商业/自媒体/智能体官网/视频)
                ▼                                                ▼
③  GEO 策略优化方案                                ③ AI授课
    solution_generator 用 LLM 出方案                 真实代发布到媒体(可发条数扣费)
    snapshot selected_queries 入档                    状态:已生成→发布成功
                ▼                                                ▼
④  计划书                                          ④ AI成绩单
    选定 query + plan_items + 排期                   12 cell × Top1/3/5/可见/信源占比
                ▼                                                ▼
⑤  文案                                            ⑤ AI智能体
    content_generator 用 DeepSeek 写文章             智能体名片/官网/管理
    可直接复制到公众号/小红书/抖音/视频号             (独立 AI 数字员工页)
                ▼                                                ▼
⑥  效果查验与更新                                  ⑥ AI蒸馏
    run topic → AI 引擎应答                         蒸馏 + 数据挖掘(再训新 seed)
    citation_match URL↔doc URL                       回到 ①
    matrix / insights / responses 看板
```

---

## 2. 阶段对应表(同样的活儿,不同的实现)

| 业务环节 | GEO 项目实现 | 讯灵实现 | 谁更重 |
|---|---|---|---|
| **品牌主体录入** | `BrandProfile`(profile_json,6 大模块表单,可上传 docx/pdf 让 LLM 抽)| **画像**(brand entity)+ 实名认证 + 抖音/微信授权 | 讯灵(认证 + 授权重) |
| **种子提示词** | `SeedPromptItem`(带 pending/approved/rejected 审核状态机)| 训练词(4 类场景分库,无审核态) | GEO(有审核流) |
| **种子扩展** | `expand_queries_for_topic` 调 telemetry-service `/suggest-queries`,DeepSeek 出 50~200 query | **蒸馏**(LLM 用 ~10 模板词扩长尾,100-150/seed) + **数据挖掘**(从搜索数据反推) | 讯灵(2 种扩展工具) |
| **扩展量级** | 上限 200 候选,用户从中勾 ≤200 selected | 142,458 长尾(2,211 seeds × ~64/seed),全自动入库 | 讯灵(量级大 100×) |
| **诊断体检** | **25 categories × 5 clusters = GEO Readiness Score 0~100** | **无对应** —— 没有公开的"网站体检"功能 | **GEO 独有** |
| **方案文档** | `solution_generator` LLM 生成完整 GEO 优化方案 markdown | 无对应 | **GEO 独有** |
| **内容生产** | `content_generator._generate_one(profile, query)` DeepSeek 800-1500 字纯文本 | AI备课(5 类媒体差异化生成) | 持平,讯灵分媒体类型 |
| **发布** | `publish_targets_json` **运营手工标注**,**不调外部 API** | 真实代发布(可发条数扣费 → sohu.com 等媒体) | **讯灵独有** |
| **probe 执行** | telemetry-service `/run-topic` → 调 Perplexity/OpenRouter API | 真浏览器跑 6 国产引擎 × PC/手机 = 12 cell | 持平,但讯灵区分 PC/手机 |
| **citation 匹配** | `citation_match.py`:AI 答案 citation URL ↔ doc.publish_targets URL | 通过 rankingList 落 cell 字段(domain/title)记录 | 持平,GEO 实现更显式 |
| **结果看板** | brand-growth: sources/engines/competitors/matrix/insights/queries/responses/published | 成绩单 7 tab + 5 核心指标 | 持平,讯灵指标更聚焦 |
| **舆情监测** | `sentiment_pipeline` + `/sentiment` 看板 | **无对应** | **GEO 独有** |
| **AI 数字员工页** | 无对应 | 智能体名片/官网/管理(独立 AI 答疑页) | **讯灵独有** |

---

## 3. 数据模型差异(核心 4 张表)

### 主体

| 维度 | GEO | 讯灵 |
|---|---|---|
| 容器名 | `Topic`(`AiTelemetryTopicORM`) | 画像(brand entity) |
| 字段载体 | `profile_json`(`BrandProfile`,21+ 字段)| API 不公开,UI 表单含品牌全称+主营业务+地域 |
| 关联约束 | User 1 : N Topic;Topic 内 1 个 profile | User 1 : N 画像;画像 1 : N seed |
| 认证 | 邮箱即用 | 实名 + 抖音/微信授权 |

### 种子 + 扩展

| 维度 | GEO | 讯灵 |
|---|---|---|
| seed 字段 | `seed_prompts_json` → `SeedPromptItem.text`(带 status) | API 字段 `models[].name`,挂 type=1/2/3/4 |
| 扩展存储 | `queries_json` → `QueryItem`(含 cluster_id/selected/seed 回指) | API 字段 `models[].content[]`,128 条/seed |
| 扩展量级 | 候选 ≤ 200,选定 ≤ 200 | 无上限(实测 142K) |
| 录入方式 | 单输入框 + 用户勾选 | 3 种:批量添加/拓词/数据挖掘 |
| 场景分类 | 无(所有 query 一锅) | **4 类(搜索/问答/意图/品牌)独立 seed 库** |

### probe 与结果

| 维度 | GEO | 讯灵 |
|---|---|---|
| probe 实体 | `AiTelemetryQueryHitORM` cell | rankingList records(rid 主键) |
| probe 来源 | selected_queries(用户从扩展池里勾的) | **推荐词**(从长尾池里挑 + 加地域/关联实体微变形)|
| 引擎数 | 取决于配置(Perplexity/OpenRouter 等通用 API) | **固定 12 cell**(6 国产 × PC/手机) |
| 命中信号 | citation URL 字符串匹配 doc URL | Top1/3/5/可见/信源 5 维 |
| 真浏览器 | `browser_engine/` 已有,主要给舆情用 | **核心 probe 通道** |

### 内容文档

| 维度 | GEO | 讯灵 |
|---|---|---|
| doc 实体 | `TopicGeneratedDoc` | API 未单列(嵌在 publish 流) |
| 字段 | title / body(markdown) / summary / publish_targets_json / 审核态 | title / 媒体类型 / 发布状态 / 媒体 URL |
| 发布动作 | **标注式**(`publish_targets_json` 注释明确:不调外部 API) | 真发(对接媒体 OpenAPI / 内部发稿管线) |
| 媒体类型 | 公众号 / 小红书 / 抖音 / 视频号(自由文本) | 5 固定:第三方新闻/第三方商业/自媒体/智能体官网/新媒体视频 |

---

## 4. 关键 LLM 调用对比

| 调用点 | GEO 用法 | 讯灵用法 |
|---|---|---|
| 资料抽取 | `profile_extractor.extract_profile_from_text` 从 docx/pdf 抽 21 字段 | 用户表单手填(API 未暴露 LLM 抽取) |
| 种子扩展 | `/suggest-queries`:seed + target + aliases + industry + service_geo + profile_cases → ≤200 query | 蒸馏:seed × ~10 模板词 → 128 长尾 |
| 方案生成 | `solution_generator._build_system_prompt`:diagnosis + profile + url → markdown 方案 | 无对应 |
| 文章生成 | `content_generator._generate_one`:profile + query → title + body(800-1500 字纯文本) | 按 5 种媒体类型差异化生成(API 未公开) |
| probe 推荐词加工 | 直接用 selected_queries 跑 | 长尾 → 加地域前缀("顺德/中山")+ 关联实体 → 推荐词 |
| 答案分类 | `_classify_framing(answer, brand)` 分类 AI 情感 | rankingList 字段无情感维度,但拆 queries/title/domain |

---

## 5. 各自独有 vs 共有

```
                        GEO 独有                共有                  讯灵独有
                    ─────────────         ──────────              ─────────────
内容生产             ✓                    内容生成 LLM             ✓ (5 媒体类型)
                    25 维体检              文章纯文本               真实发布渠道
                    舆情监测                                       智能体官网/名片
                    审核态机             ──────────                数据挖掘工具
诊断 / 看板          GEO Readiness                                  Top1/3/5/可见/信源
                    Score 0~100         种子扩展                   12 cell × PC/手机
                    25 categories       推 / 拉 query              6 国产 AI 引擎
                    cluster 分组          ─                          PC vs 手机区分
                    competitive
                    comparison
                    
分发                 标注式 publish_targets   probe 闭环             真席位 + 配额
                    workbench admin      citation 匹配              抖音老平台数据
                    
入口                 网站 URL 自助检测     扩展 LLM (DeepSeek 主)    实名 + 授权门槛
                    /advanced/{mode}     content gen 同 LLM
                    /entity              
                    /sentiment           
```

---

## 6. 可借鉴点(按可落地性排序)

### 高(短期能做,工程量小)

1. **场景分类的 seed 库** — 把现有单一 `seed_prompts_json` 拆成 4 个场景(搜索词 / 问答词 / 意图 / 品牌),每类用不同 LLM 提示词模板。**讯灵的 4 类是真的有效的产品语言**,客户能看明白"我要做哪类 SEO"。改动:`SeedPromptItem` 加 `scene_type` 字段;`expand_queries_for_topic` 按 scene 走不同 system prompt。

2. **扩展词二次包装成推荐词** — 当前 `selected_queries` 直接喂 probe,可加一层"问题化"加工:加地域前缀(从 `profile.service_geo` 取)、加关联实体(从 `competitors` 表)。**显著提升 probe 真实度**(讯灵 93% 字符串相似的"加顺德前缀"是高 ROI 的小动作)。

3. **probe 维度加 PC / 手机区分** — 现在 `AiTelemetryQueryHitORM` cell 只按 engine,加 device 维度(PC/mobile),用 `browser_engine` 跑两套 user agent。讯灵 Top1/3/5 是 PC 和手机分开算的,这才是真实 GEO 监测。

4. **probe 信号拆开 5 维** — 当前 citation_match 是 0/1 boolean,加上 **Top1占比 / Top3占比 / Top5占比 / 可见占比 / 信源占比**(从答案文本和 citation 列表里解析位置)。这是讯灵客户每周看的指标,客户语言一致性强。

### 中(中期能做,需要前后端协作)

5. **媒体类型分类** — `TopicGeneratedDoc` 加 `media_type` 枚举(第三方新闻/商业/自媒体/智能体官网/新媒体视频),`content_generator` 按媒体类型用不同 prompt(新闻稿严谨 vs 自媒体口语化)。当前 `publish_targets_json` 是自由文本,改成枚举。

6. **"AI 智能体官网"产品形态** — 独立路由 `/agent/{brand_slug}`,渲染:品牌 FAQ + 客服 AI 对话框 + 业务卡片。这相当于一个**给品牌托管的 AI 答疑页**,本身是 SEO 流量入口(被 AI 引擎抓收),也是变现入口。GEO 项目已经有 `Topic.profile` 数据底子,加一个对外渲染页即可。

7. **可发条数配额制** — 当前 membership 是按 topic / usage 算,讯灵按"可发条数 × 媒体类型"卖。如果未来引入代发布,这是天然的计费单元。

### 低(长期或战略级)

8. **真实代发布渠道** — 讯灵的核心壁垒。GEO 项目目前是标注式,要切真发需对接公众号 OpenAPI / 小红书机构号 / 搜狐号代发等渠道,渠道资源 > 工程量。

9. **抖音/字节系数据反哺** — 讯灵的 uid 是从 2020 年抖音SEO 平台沉淀来的,客户复用账号 + 字典 + 订阅。GEO 没有这种数据底子,从零起步。

---

## 7. 不能直接抄的部分(为什么)

| 讯灵做法 | 不直接抄的原因 |
|---|---|
| 实名认证 + 抖音授权 | 我们目标客户(海外 / B2B SaaS)反感强认证,转化漏斗会塌 |
| 12 cell 国产引擎 | 国产 AI 引擎覆盖国内市场,海外目标客户更关心 ChatGPT / Perplexity / Claude / Gemini |
| 6 步教学隐喻命名 | 我们已有 6 步**咨询/诊断**隐喻(画像/诊断/方案/计划/文案/查验),命名风格不同;**改名 vs 改流程**要权衡品牌一致性 |
| 142K 长尾全自动入库 | 当前 ≤200 限制是有理由的:LLM 成本 + 人工审核成本 + 客户决策成本,放开前要算单位经济 |
| 单一 sohu.com 信源 | 讯灵把发布渠道集中在搜狐号,我们如果照抄会导致 citation 集中风险;应多渠道分散 |

---

## 8. 我们独有的优势(讯灵学不来,或学起来贵)

| GEO 独有 | 为什么是壁垒 |
|---|---|
| **GEO Readiness Score 0~100 + 25 categories** | 沉淀了 25 个 check + 5 cluster 的体检方法论,讯灵没有公开 readiness 体检产品 |
| **审核态机**(SeedPromptItem.status,Phase C 改造) | 多人协作的审核流,讯灵无明显多角色审核 |
| **舆情模块** | sentiment_pipeline + 7 平台账号轮询 + 真浏览器抓取,讯灵无对应 |
| **方案文档**(solution_generator) | 完整 markdown 方案输出,讯灵无对应,这是高客单价咨询交付物 |
| **国际化 i18n + Stripe + Moltspay** | 多币种结算 + 多语言 UI,讯灵纯国内人民币 |
| **Workbench admin** | 多账号 / 多 topic / 多审核员管理后台,讯灵无暴露 |
| **citation_match URL 显式匹配** | 后台真把 doc URL 列表跟 AI citation 字符串对齐,信号比讯灵的 domain-level 信源占比更精确 |

---

## 9. 战略建议(基于本次对比)

短期 3 个动作,落地代价小,客户体验提升明显:

1. **种子库分场景**(2-3 天)— 把 `seed_prompts_json` 拆 4 类,扩展提示模板用讯灵的"模板词典"(怎么样/详细介绍/基本信息/客户评价/性价比 等),客户能立刻看到"我做的 SEO 哪一类"。

2. **probe 5 维指标**(1 周)— 在现有 citation_match 之上加 Top1/3/5/可见/信源占比 计算,前端 brand-growth/insights 加 5 个数字卡片。统一客户语言。

3. **AI 智能体官网 MVP**(1-2 周)— 用现有 BrandProfile 数据,加一个 `/agent/{slug}` 公开页,渲染 6 模块表单内容 + FAQ 风格 + sitemap.xml + JSON-LD,本身就是 GEO 优化样板,客户直接 demo "我们给你做的 AI 友好页长这样"。

中期 2 个动作,需要业务侧权衡:

4. **媒体类型枚举化**(2 周)— `publish_targets_json` 改 enum,内容生成按类型走 prompt,为后续真发铺垫。

5. **代发布渠道试水**(1 季度)— 先接 1~2 个渠道(公众号 OpenAPI + 搜狐号),配合 membership 改成"可发条数 × 媒体类型",验证讯灵商业模型在我们客户群是否成立。

不动:

- **不引入实名认证** —— 跟我们的 SaaS 自助形态冲突
- **不全自动入库 14 万长尾** —— 先优化 200 上限的勾选 UX
- **不放弃 25 categories 体检** —— 这是我们最强的差异化资产,讯灵学不来

---

## 附录:本次对比的源数据

- **讯灵实测**:[`competitor-xunling.md`](./competitor-xunling.md),完整 API + 数据样本
- **GEO 项目源**:
  - 流程定义:`frontend/src/components/TopicStepper.tsx`、`backend/geo/api/ai_telemetry.py`
  - 扩展引擎:`backend/geo/api/ai_telemetry.py:1233` `expand_queries_for_topic`
  - 内容生产:`backend/geo/services/content_generator.py:171` `_generate_one`
  - 方案生产:`backend/geo/services/solution_generator.py`
  - citation 匹配:`backend/geo/services/citation_match.py`
  - 25 categories:`backend/geo_checker/checks.py`(2958 行)+ `solution_generator.py:55` `CLUSTER_DEFS`
  - 数据模型:`backend/geo/models/ai_telemetry.py`(`SeedPromptItem` / `QueryItem` / `BrandProfile`)
