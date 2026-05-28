# 种子提示词 4 场景分类 —— 是否采纳决策文档

> 调研对象:讯灵 GEO 的「搜索词 / 问答词 / 意图 / 品牌」4 类场景
> 评估目标:本仓库是否要在 seed prompt + query expansion 这两层引入场景分类
> 文档目的:**给出明确判断,而不是只罗列 pros/cons**
> 关联:`docs/competitor-xunling.md`(竞品完整产品流程分析)

---

## TL;DR(结论先行)

**判断:不采纳完整 4 场景分类,但抄一样东西 —— 模板词典作为 expansion prompt 的隐式增强。**

| 子项 | 是否做 | 理由 |
|---|---|---|
| 加 `scene` 字段到 `SeedPromptItem` | ❌ 不做 | 当前业务每 topic 仅 5-50 个 seed,字段维度收益 < 字段维护成本 |
| 4 套 LLM prompt 切分 | ❌ 不做 | 我们已经把 target/aliases/industry/service_geo/profile_cases 全注入,LLM 拿到的上下文比讯灵厚 |
| 前端 Queries 页加 4-Tab | ❌ 不做 | 用户每个 topic 只填几个 seed,Tab 反而增加操作步骤;咨询师在 admin 侧人工分类即可 |
| **抄讯灵的模板词典(怎么样/介绍/详细介绍/客户评价/...)做 prompt 提示** | ✅ 做 | 几乎零成本,直接提升 expansion 质量,不改数据结构、不改 UI |
| 5 个核心指标(Top1/3/5/可见/信源)固化展示 | ✅ 做(单独的事) | 这是另一个优先级更高的事,与 scene 无关,见 `competitor-xunling.md` § 7 |

---

## 1. 对比表 —— AS-IS vs 加 4 场景

### 1.1 数据模型

| 维度 | 现状(本仓库) | 加 4 场景后 |
|---|---|---|
| 种子结构 | `{text, status, submitted_at, ...}` | + `scene: Literal["search","qa","intent","brand"]` |
| Topic 内 seed 数量(典型) | 5-50 条/topic | 5-50 条/topic(不变) |
| LLM 上下文 | target / aliases / industry / service_geo / **case_stories** / **core_credentials** | 同上 + scene + template_hints |
| seed → query 追溯 | `QueryItem.seed: str` 已有 | scene 通过 `seed.scene` 反查,无新字段 |
| DB migration | — | 不需要(JSON 列默认值兜底) |

### 1.2 LLM 调用

| 维度 | 现状 | 加 4 场景后 |
|---|---|---|
| Prompt 数量 | 1 套 GEO-aware system prompt | 4 套(按 scene 切) |
| count 默认 | 50,可配 ≤200 | 按 scene:search 80 / qa 30 / intent 20 / brand 50 |
| 入参字段数 | 7 | 9 |
| 成本(单次) | DeepSeek ~¥0.01-0.05 | 同上,差 < ¥0.001 |

**关键观察:** LLM 成本省不下来。当前 seed 平均长度 + topic profile 注入已经给 LLM 提供了远比讯灵详尽的上下文,4 场景切 prompt 的收益不在成本侧。

### 1.3 用户体验

| 维度 | 现状 | 加 4 场景后 |
|---|---|---|
| Queries 页录入 | 单 textarea + 提交 | 4 个 Tab + 场景说明文案 + textarea |
| 用户操作步数 | 1(粘贴 → 提交) | 2-3(选 Tab → 看 hint → 粘贴 → 提交) |
| 用户认知负担 | 低 | 中(需要理解 4 场景语义差异) |
| 扩展结果展示 | 一锅 50 条 query 列表 | 按 scene 分 4 组(每组 8-20 条) |

### 1.4 下游

| 维度 | 现状 | 加 4 场景后 |
|---|---|---|
| selected_queries 走向 | 既 run 又 generate | 同上(不动) |
| matrix N×M | brand × engine | 同上(不动,future 可加 scene 维) |
| citation_match | URL 级匹配 | 不变 |
| Insights 看板 | 7 个聚合分 | 不变 |

---

## 2. 业务线匹配度分析(关键判断点)

讯灵之所以**必须**做 4 场景,是它的业务模型逼的。我们的业务模型不一样:

| 维度 | 讯灵 | 本仓库 GEO |
|---|---|---|
| 客户类型 | **量大客单价低**(代运营 SaaS) | **客单价高频次低**(咨询 + 工具) |
| 单客户 seed 量级 | **千-万级**(测试账号 2,211 种子) | **5-50 级**(每 topic 几个种子) |
| 谁来分类 seed | 客户自助分(必须的,否则一锅扩词没法控) | 咨询师/admin 审核(已有 workbench review 流程) |
| 客户领域知识 | 浅(让他们点 Tab 都嫌多) | 深(自己就是品牌方营销负责人) |
| LLM 上下文丰富度 | 弱(只有 brand 名 + 业务关键词) | 强(25+ 字段 profile + case_stories + core_credentials) |
| 流程定位 | 自助批量化 | 咨询 + 内容生产 |

**核心矛盾:**

讯灵的 4 场景是**用结构化补偿上下文不足** —— 它的 LLM 只知道"南方网通+抖音推广",不知道公司故事/案例/资质,所以必须靠 scene 模板锁定扩展方向。

我们的 expansion 已经塞了:

```python
# backend/geo/api/ai_telemetry.py:1267
"target": target, "aliases": aliases, "industry": industry,
"service_geo": service_geo, "profile_cases": profile_cases,
# profile_cases 来自 profile.case_stories + profile.core_credentials,各最多 40 条
```

LLM 拿到一个 seed `防水胶`,同时拿到品牌名 / 别名 / 行业 / 地域 / 历史案例 / 资质 —— 上下文密度远高于讯灵。**再加 scene 是边际收益**。

---

## 3. 模板词典的真实价值(为什么这部分要抄)

讯灵 14 万长尾池里高频模板:

| 模板词 | 长尾池命中数 | 等价于讯灵把它当"模板硬塞" |
|---|---:|---|
| 怎么样 | 4,309 | ✅ |
| 实力 | 6,694 | ✅ |
| 性价比 | 3,661 | ✅ |
| 详细介绍 | 438 | ✅ |
| 基本信息 | 429 | ✅ |
| 客户评价 | 403 | ✅ |
| 公司概况 | 437 | ✅ |
| 团队 | 870 | ✅ |

这 8-10 个模板词是 GEO 行业**实测有效的高频 AI 提问模板**。我们的 LLM 自由发挥时可能漏掉某几个(尤其 "公司概况" / "客户评价" / "团队实力" 这类品牌侧问法)。

**最小改造方案 —— 抄模板,不改结构:**

只动 `expand_queries_for_topic` 一处,在送 telemetry-service 的 body 里加一行 `prompt_extension_hints`:

```python
# backend/geo/api/ai_telemetry.py:expand_queries_for_topic
EXPANSION_HINT_TEMPLATES = [
    "{target}怎么样", "{target}详细介绍", "{target}基本信息",
    "{target}公司概况", "{target}客户评价如何", "{target}性价比怎么样",
    "{target}实力如何", "{target}团队怎么样",
    "{seed}厂家", "{seed}供应商", "推荐{seed}", "{seed}哪家好",
    "{seed}怎么选", "{seed}对比",
]
# 拼接所有模板的填充实例,作为 hint 送 LLM(限制 ≤30 条)
hints = [tmpl.format(target=target, seed=seed) for tmpl in EXPANSION_HINT_TEMPLATES]
body["prompt_extension_hints"] = hints[:30]
```

telemetry-service 端:

```text
[扩词必须覆盖的模板示例]:
{prompt_extension_hints}

生成的 query 中,前 8 条必须严格匹配上面模板的填充结果,后 N-8 条可发散同类长尾。
```

**改动量:** backend 加 ~15 行,telemetry-service 加 ~5 行,**不改 model / 不改 frontend / 不改 DB / 不破坏 legacy**。

---

## 4. 判断总表(为什么这么决定)

| 问题 | 答案 | 依据 |
|---|---|---|
| 我们业务的瓶颈是 expansion 质量不够吗? | **部分是** —— 偶尔漏品牌侧问法(怎么样/团队/口碑) | 客户反馈 + 实抽样 expand 结果 |
| 4 场景能解决这个瓶颈吗? | 能,但**抄模板词典**也能 | 模板词典 = 4 场景产出物的最小公约数 |
| 抄模板的成本 vs 抄整套 4 场景的成本? | **1 : 10**(15 行 vs 5 个文件 + 前端 + i18n + telemetry-service 切分) | 见 § 3 改动量 |
| 客户体验改变? | **抄模板:无感** / 抄 4 场景:多一层 Tab | UX 是高客单价 B 端最敏感的地方 |
| 跟讯灵差异化定位冲突吗? | **抄 4 场景会模糊定位** —— 我们的卖点是咨询深度,不是自助配 seed | TopicStepper 6 步 vs 讯灵直接录词 |

---

## 5. 推荐执行路径

### 本期(本 PR / 1 天内可做完)

✅ 在 `expand_queries_for_topic` 里加 `EXPANSION_HINT_TEMPLATES` 常量 + 拼接逻辑
✅ telemetry-service 端 system prompt 加一段"必须覆盖模板"约束(若不在我们手里,降级把 hints 拼到 seed 字符串里送)
✅ 加一组单测:用同一个 seed `防水胶`(brand=`大有为家居`)对比改造前后 expansion 结果是否覆盖了模板词

**期望效果:** expansion 结果里,品牌+口碑/资质/团队 这类问法的覆盖率从约 20% → 80%+,无前端改动、无 schema 变化。

### 不做(明确划清)

❌ `SeedPromptItem.scene` 字段
❌ `SCENE_TEMPLATES` 多套 prompt 切分
❌ Queries 页 4-Tab UI
❌ 4 场景独立配额机制
❌ legacy seed 归类脚本

### 如果未来要回来做(触发条件)

只有当出现以下任一信号,才回来重新考虑完整 4 场景:

1. 单 topic 的 seed 数量稳定增长到 100+(说明业务转向更量化运营)
2. matrix 维度想加 scene(`brand × engine × scene`)做更细粒度的 Top-K 分析
3. 接代发布渠道(对标讯灵的 5 类媒体),那时 scene 跟媒体类型可联动配额

---

## 6. 决策签字

| 字段 | 决定 |
|---|---|
| 采纳完整 4 场景 | **否** |
| 采纳模板词典(隐式增强 LLM prompt) | **是** |
| 工时 | ~1 天(含 telemetry-service 协同,若不行降级 0.5 天) |
| 影响面 | backend 单文件,15 行 |
| 风险 | 极低(纯增量,不破坏现有调用) |
| 何时复评 | 若 6 个月内出现 § 5 的"触发条件",回来评估完整版 |
