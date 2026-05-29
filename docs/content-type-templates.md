# 创作方向 × 文案类型 × 模板生成 — 方案文档

> 出发点:用户希望 BrandProfile 暴露 `creation_directions` + `copywriting_types` 多选,
> 选不同组合 → 生成不同风格的文章。
> 参考:讯灵 AI备课 5 种媒体 tab + 画像名 隐式承载文案差异化。

---

## 0. 一句话目标

```
画像里多选「创作方向」+「文案类型」
  ↓
每种组合 = 一个文章模板(prompt 变体)
  ↓
对一条 query,系统按用户选中的所有组合,**一条 query 出 N 篇风格不同的文章**
```

---

## 1. 现状(实地考证)

### BrandProfile schema(`models/ai_telemetry.py:503-507`)

字段都在,但 2026-05-17 起从资料表单移除(注释明确写了):

```python
creation_directions: list[str]   # 创作方向
copywriting_types: list[str]     # 文案类型偏好
target_platforms: list[str]      # 适配平台
content_tones: list[str]         # 内容调性偏好
content_redlines: list[str]      # 内容雷区
```

### 当前实际用到哪里

| 字段 | 用到的地方 | 说明 |
|---|---|---|
| `creation_directions` | **没用** | schema 存了,生成时不读 |
| `copywriting_types` | **没用** | 同上 |
| `target_platforms` | `ContentTemplate.target_platforms_json` 用了,但是模板自己的 | 跟 profile 那个不串 |
| `content_tones` | `content_generator._build_system_prompt:390` 拼了一句"调性:X" | **唯一在跑的** |
| `content_redlines` | 没用 | |

**结论:5 个字段在 schema 里全保留,代码只用了 content_tones 1 个。**

### 当前内容生成主路径

```
admin 在 PublishingPlanEditor 给 plan_item 选 ContentTemplate
  ↓
content_generator._run_per_item 按 template 拼 prompt
  ↓
profile.content_tones 拼成"调性:..."的一句话注入 system prompt
  ↓
DeepSeek 写一篇文章
```

ContentTemplate 是 admin 维护的"模板库",每条带 `target_platforms_json`(适配平台)+ prompt 字段。
但模板本身没有"创作方向" / "文案类型"这两个维度的标签。

---

## 2. 讯灵对应做法(快速回顾)

讯灵 AI备课 页 5 个 tab:
- 第三方新闻媒体训练
- 第三方商业媒体训练
- 自媒体训练
- 智能体官网训练
- 新媒体视频训练

每个 tab 是一种**媒体形态**。同一画像 + 同一训练词,选不同 tab → 生成不同形式的文章。
讯灵**没有独立的"创作方向"和"文案类型"二级多选**,它把整个差异化打包到"5 种媒体类型"里 —— 简化但不灵活。

我们的做法可以更精细:把"创作方向 × 文案类型"做成 2D 矩阵,组合数比讯灵多得多。

---

## 3. 设计 — 三层落点

### 3.1 画像层(BrandProfile)— 默认偏好

资料 tab 加回「内容创作偏好」节,**只暴露 creation_directions + copywriting_types 两个多选**(其余 3 个字段后续再说)。

**creation_directions 候选项**(7 选 N):
| 值 | 中文标签 | 适用场景 |
|---|---|---|
| `industry_insight` | 行业洞察 | 趋势 / 市场分析 |
| `case_story` | 案例分享 | 真实项目复盘 |
| `how_to_guide` | 实操指南 | 步骤 / 流程 |
| `trend_forecast` | 趋势预测 | 行业前瞻 |
| `product_review` | 产品评测 | 横向对比 |
| `customer_story` | 客户故事 | 真人 / 真案例 |
| `faq` | FAQ 答疑 | 常见问题列表 |

**copywriting_types 候选项**(6 选 N):
| 值 | 中文标签 | 长度 / 形式 |
|---|---|---|
| `long_form` | 深度长文 | 1500-2500 字 |
| `medium_post` | 中等图文 | 500-1500 字 |
| `short_social` | 短社媒文案 | < 500 字 |
| `video_script_long` | 长视频脚本 | 3-8 分钟口播稿 |
| `video_script_short` | 短视频文案 | 30-60 秒口播稿 |
| `faq_list` | FAQ 列表 | Q&A 多条 |

**为什么这两个先做,其他不做:** target_platforms 已经在 ContentTemplate 上有了,content_tones 已经在跑,content_redlines 选填可后置。

### 3.2 计划层(PublishingPlanEditor)— 应用选中组合

每个 plan_item 加 2 个多选(可选):
- `creation_directions_override`(默认空 → 用 profile 的)
- `copywriting_types_override`(默认空 → 用 profile 的)

存在 `publishing_plan_json[].overrides.{creation_directions, copywriting_types}`。

### 3.3 生成层(content_generator)— 多变体出稿

**核心改动:** 当前一条 query → 一篇文章。改成 **一条 query × N combos → N 篇文章**。

```python
combos = []
for d in plan_item.creation_directions or profile.creation_directions:
    for t in plan_item.copywriting_types or profile.copywriting_types:
        combos.append((d, t))
# 没选任何 combo → fallback 用 profile.content_tones 走老路径(1 篇)
if not combos:
    combos = [(None, None)]

for d, t in combos:
    doc = TopicGeneratedDoc(
        topic_id=topic_id, plan_id=plan_id, plan_item_id=item_id,
        source_query_text=query,
        creation_direction=d, copywriting_type=t,    # 新字段
        ...
    )
    title, body = generate_with_combo(profile, query, d, t)
```

**TopicGeneratedDoc 加 2 字段:** `creation_direction`, `copywriting_type` — 同一 query 多份稿件按 combo 区分。

---

## 4. 模板矩阵(prompt 变体)

每个 combo 用同一个 base prompt + 套不同的「形式约束段」:

```python
DIRECTION_HINTS: dict[str, str] = {
    "industry_insight":  "以行业趋势 / 市场数据 / 头部玩家动向切入,产出有信息密度的分析。",
    "case_story":        "围绕一个真实案例展开:背景 → 挑战 → 我方解法 → 结果 → 启发。",
    "how_to_guide":      "用步骤化叙述:Step 1 / Step 2 ...,每步可执行,有判断节点。",
    "trend_forecast":    "用前瞻视角:近 12-36 个月可能发生的变化 + 应对建议。",
    "product_review":    "对比 2-3 个同类产品 / 方案,列优劣矩阵,给推荐场景。",
    "customer_story":    "第一人称引述客户原话 + 真实姓名 + 行业 + 量化成果。",
    "faq":               "8-12 条常见问题 + 每题 100-200 字回答,问题先于回答。",
}

TYPE_HINTS: dict[str, str] = {
    "long_form":         "正文 1500-2500 字,3-5 个二级小节,每节 300-500 字,带过渡句。",
    "medium_post":       "正文 500-1500 字,2-3 个二级小节,适合公众号 / 知乎中等阅读量帖子。",
    "short_social":      "正文 ≤500 字,无小节,1-3 个 emoji,1 个 CTA,适合小红书 / 微博。",
    "video_script_long": "5-8 分钟口播稿,标记[镜头]/[口播]/[屏幕字],带钩子前 10 秒。",
    "video_script_short":"30-60 秒口播稿,前 3 秒钩子 + 痛点共鸣 + 解决方案 + CTA。",
    "faq_list":          "纯 Q&A 列表,8-12 条,每条问题独占一行,回答 100-200 字。",
}

def build_combo_prompt(profile, query, direction, type_):
    direction_hint = DIRECTION_HINTS.get(direction, "")
    type_hint = TYPE_HINTS.get(type_, "")
    return f"""...(原 system prompt)...

本次稿件的形式要求:
  - 创作方向:{direction_hint}
  - 文案类型:{type_hint}
  - 调性:{', '.join(profile.content_tones) or '中性专业'}
  - 雷区(必避):{', '.join(profile.content_redlines) or '无'}
"""
```

---

## 5. 数据库 / 类型变更

### 后端

```python
# models/ai_telemetry.py — TopicGeneratedDocORM 加 2 字段(SQLite ALTER 安全)
class TopicGeneratedDocORM(Base):
    ...
    creation_direction = Column(String(64), nullable=True)
    copywriting_type   = Column(String(64), nullable=True)

# Pydantic GeneratedDocOut 同步加
class GeneratedDocOut(BaseModel):
    creation_direction: Optional[str] = None
    copywriting_type:   Optional[str] = None
```

### 前端

```ts
// types/ai-telemetry — TopicGeneratedDoc 类型加 2 字段
// BrandProfileForm 加回「内容创作偏好」节(creation_directions + copywriting_types 多选)
// PublishingPlanEditor 在每个 plan_item 行下加 2 个多选(折叠面板)
```

---

## 6. 实施步骤 + 工作量

| Step | 改动 | 工时 |
|---|---|---|
| 1 | DB migration:`topic_generated_docs` 加 2 列 | 0.3 天 |
| 2 | `TopicGeneratedDocORM` + `GeneratedDocOut` 加字段 | 0.2 天 |
| 3 | `content_generator` 加 `DIRECTION_HINTS / TYPE_HINTS` + `build_combo_prompt` + 多变体生成循环 | 1 天 |
| 4 | `BrandProfileForm` 加回 2 个多选 + i18n 候选项 7 + 6 | 0.5 天 |
| 5 | `PublishingPlanEditor` 加 plan_item 级 override 多选(可选) | 0.5 天 |
| 6 | `AdminContentReview` 列表加 creation_direction / copywriting_type 列 + 按这俩字段筛选 | 0.5 天 |
| **合计** | | **3 天** |

---

## 7. 风险与权衡

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 多 combo 生成 → LLM 成本翻 N 倍 | **高** | (a) Profile 默认偏好限 ≤ 3 direction × ≤ 3 type;(b) plan_item override 默认空(=1 篇);(c) 加 `max_variants_per_query` env 兜底 |
| 同一 query 多稿,审核效率低 | 中 | AdminContentReview 加按 query group 折叠 + 一次性 approve 整组 |
| Profile 默认没填,plan 也没填 → 退化 | 低 | 没填时 = 当前行为(1 篇 / query),向后兼容 |
| 模板矩阵爆炸,prompt 不够区分度 | 中 | hint 文案写细一点,sample 几条人工 review 后调 |

---

## 8. 后续可串联

1. **prompt_extension(已存在)+ 本方案**:admin 在 topic 上配的 prompt_extension 可作为"风格补丁"叠加到每个 combo,做 brand-level fine-tune
2. **content_redlines 落地**:这次只动 directions + types,redlines 下次接(同样塞进 system prompt 的"必避"段)
3. **per-engine 调性差异**:接 telemetry-service 测出"豆包偏好" / "DeepSeek 偏好",自动给不同引擎 probe 配不同 combo

---

## 附录:涉及文件清单

```
# 后端
backend/geo/models/ai_telemetry.py            TopicGeneratedDocORM + GeneratedDocOut 加 2 字段
backend/migrations/<NN>_add_doc_combo.py      (新建)SQLite ALTER 加列
backend/geo/services/content_generator.py     加 DIRECTION_HINTS / TYPE_HINTS + 多变体循环
backend/geo/api/admin_review.py               (可选)content review 列表过滤接 combo 字段

# 前端
frontend/src/components/BrandProfileForm.tsx  加回「内容创作偏好」节(2 个多选)
frontend/src/services/aiTelemetryApi.ts       BrandProfile 类型保留(已有),TopicGeneratedDoc 加 2 字段
frontend/src/pages/Admin/PublishingPlanEditor.tsx  (可选)plan_item override 多选
frontend/src/pages/Admin/ContentReview.tsx    列表加 combo 列 + 按组折叠
frontend/src/i18n/{zh,en}.ts                  creation_directions 7 项 + copywriting_types 6 项 i18n
```

---

## 决策点(等你拍)

1. **多变体生成默认行为?** Profile 选了 3 direction × 3 type = 9 combo —— 默认每条 query 出 9 篇,还是只挑用户在 plan_item 显式勾的那些?(建议: plan_item 显式勾的优先,profile 偏好作为 dropdown 默认候选)
2. **redlines / tones 这次要不要一起重做?**(建议:这次只动 directions + types,redlines / tones 下次,避免 prompt 一次性改太多)
3. **要不要保留 ContentTemplate 体系?** 现有 PublishingPlanEditor 围绕 template 转,如果走 combo 体系,template 会变冗余(combo 自己就是模板)。**建议保留 template 作为高阶 admin 工具**(可以塞极其复杂的 prompt),combo 是低门槛默认路径,两者并行不冲突
