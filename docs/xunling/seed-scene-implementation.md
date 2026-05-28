# 种子提示词分场景 — 实施方案

> 出发点:[`geo-vs-xunling.md`](./geo-vs-xunling.md) §6 借鉴点 #1。
> 目标:把单一 `seed_prompts_json` 拆成 4 类场景(搜索词 / 问答词 / 意图 / 品牌),每类用不同 LLM 提示词模板扩词,前端按 tab 组织。
> 预计工作量:**5-7 天**(后端 2 天 + LLM 模板 1-2 天 + 前端 2-3 天),分 4 个 phase 渐进交付。
> 风险:中(动核心数据模型 + LLM prompt 调整 + 前端流程改造,迁移要小心)。

---

## 0. 一句话目标

种子提示词从「一锅 `text + status`」升级为「带场景标签的 4 类 seed 库」,LLM 扩词按场景走差异化模板,**老数据安全迁移、新流程客户能立刻看到差异化效果**。

---

## 1. 现状基线

### 数据模型(`backend/geo/models/ai_telemetry.py`)

```python
class SeedPromptItem(BaseModel):
    text: str
    status: ReviewStatus = "pending"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None

class QueryItem(BaseModel):
    text: str
    cluster_id: Optional[int] = None
    status: ReviewStatus = "approved"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    selected: bool = True
    seed: str = ""    # 用户当时从哪个种子扩展出这条 query
```

**关键存储:** `AiTelemetryTopicORM.seed_prompts_json` / `queries_json` 都是 JSON 字段,Pydantic 序列化进出。**新加字段对老数据 forward-compat 友好**(默认值兜底)。

### 扩展 API(`backend/geo/api/ai_telemetry.py:1233`)

`expand_queries_for_topic` 接 `{seed, count?=50}`,转调 telemetry-service `/suggest-queries`,LLM(默认 DeepSeek)产 ≤200 候选 query。**当前所有 seed 走同一个 prompt 模板**。

### 前端入口

- 用户侧:`pages/Dashboard/TopicProfile.tsx`(还要确认)
- Admin 侧:`pages/Workbench/AdminTopicEdit.tsx`
- 种子录入 UI 当前是单一输入框 + 候选列表 + 勾选

---

## 2. 目标设计

### 4 类场景定义

| scene_type | i18n zh | i18n en | seed 形态 | 扩展模板特征 |
|---|---|---|---|---|
| `search` | 搜索词场景 | Search-Intent | 品类/产品**关键词** | XX厂家 / XX供应商 / XX多少钱 / 推荐XX |
| `qa` | 问答词场景 | Q&A-Recommendation | 推荐/对比**问答短语** | XX哪家好 / XX怎么选 / XX 对比 |
| `intent` | 意图场景 | How-To-Intent | 意图查询(怎么做) | 如何选XX / XX攻略 / XX教程 / XX使用指南 |
| `brand` | 品牌场景 | Brand-Evaluation | 品牌名 / 品牌+业务线 | XX怎么样 / XX详细介绍 / XX客户评价 / XX性价比 |

### 数据模型升级

```python
SceneType = Literal["search", "qa", "intent", "brand"]
DEFAULT_SCENE: SceneType = "search"   # 老数据 fallback

class SeedPromptItem(BaseModel):
    text: str
    scene_type: SceneType = DEFAULT_SCENE      # 新增
    status: ReviewStatus = "pending"
    ...(其余不变)

class QueryItem(BaseModel):
    text: str
    scene_type: SceneType = DEFAULT_SCENE      # 新增,从 seed 继承
    cluster_id: Optional[int] = None
    ...(其余不变)
```

**不动 DB schema** — 因为 `seed_prompts_json` / `queries_json` 是文本字段,Pydantic 序列化负责字段层。**老数据反序列化时自动填默认值 `"search"`**,前端 admin 编辑器再让客户重新分类。

### 跨场景共存

同一 seed 字符串(如 `跨境并购`)**允许在 4 个场景下各存一条独立 SeedPromptItem** —— 这是讯灵的关键 insight(同词 4 倍占位)。约束改成:**`(text, scene_type)` 联合唯一**,不再是 `text` 唯一。

---

## 3. 4 个 Phase 渐进交付

### Phase 1:数据模型 + 老数据迁移(1.5 天)

**改动文件:**
- `backend/geo/models/ai_telemetry.py` — 加 `SceneType` 类型别名,`SeedPromptItem` / `QueryItem` 加 `scene_type` 字段
- `backend/geo/api/ai_telemetry.py` — `submit_seed_prompt` / `_append_seed_prompts` / `_validate_query_diff` 等容纳新字段
- `backend/scripts/backfill_seed_scene.py`(新建)— 一次性脚本,把现有 topic 里所有 seed/query 的 `scene_type` 显式设为 `"search"`(虽然反序列化时也会自动填,但显式落盘后老库结构清晰)

**关键代码(SeedPromptItem):**

```python
from typing import Literal
SceneType = Literal["search", "qa", "intent", "brand"]
DEFAULT_SCENE: SceneType = "search"
ALLOWED_SCENES = ("search", "qa", "intent", "brand")

class SeedPromptItem(BaseModel):
    text: str
    scene_type: SceneType = DEFAULT_SCENE
    status: ReviewStatus = "pending"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
```

**唯一性约束(API 校验,非 DB):**

```python
# _append_seed_prompts 里
existing = {(it.text, it.scene_type) for it in existing_items}
for text in incoming_texts:
    key = (text, payload_scene)
    if key in existing:
        raise HTTPException(409, f"seed already exists in this scene: {text}")
```

**回滚预案:** 这个 phase 的所有改动都是**向后兼容**的(老 JSON 反序列化为默认值 search),如果后续 phase 出问题,回滚只需 revert model + API 代码,数据不动。

### Phase 2:LLM 扩词模板按场景分(2 天)

**改动文件:**
- `backend/geo/api/ai_telemetry.py:1233` `expand_queries_for_topic` — 接 `scene` 参数透传给 telemetry-service
- `telemetry-service`(独立服务,不在本仓库)的 `/suggest-queries` — 4 套 system prompt
- 如果 telemetry-service 不在我们控,改在 `services/` 加新文件 `query_expander.py` 自己实现

**API 改动:**

```python
@router.post("/topics/{topic_id}/expand-queries")
async def expand_queries_for_topic(topic_id: int, payload: dict, ...):
    seed = (payload.get("seed") or "").strip()
    scene = (payload.get("scene") or DEFAULT_SCENE).strip()
    if scene not in ALLOWED_SCENES:
        raise HTTPException(400, f"invalid scene: {scene}")
    ...
    body = {"seed": seed, "count": count, "target": target,
            "scene": scene,
            "aliases": aliases, "industry": industry,
            "service_geo": service_geo, "profile_cases": profile_cases}
    # ↑ 多带一个 scene 字段
```

**4 个 prompt 模板(完整可用,从讯灵实测反推过):**

```python
# backend/geo/services/query_expander.py(新建,或者塞 telemetry-service)

SCENE_PROMPTS: dict[str, str] = {
    "search": """你是 SEO 长尾词扩展专家。
针对种子词「{seed}」扩展 {count} 个**产品搜索意图**的长尾关键词。
扩展模式:{seed}厂家 / {seed}供应商 / {seed}生产商 / 推荐{seed}厂家 /
        {seed}多少钱 / {seed}评测 / {seed}排行 / 性价比{seed} / 优质{seed}供应商 / ...
要求:
1. 每条 ≤ 30 字,不含标点
2. 不要重复
3. 行业上下文:{industry};服务地域:{service_geo}(若有,可适当带地名前缀如「{service_geo}{seed}」)
4. 不要包含明显疑问句(问答类不归这里)
只输出 JSON: {"queries": ["...", "..."]}""",

    "qa": """你是 AI 问答词扩展专家。
针对种子词「{seed}」扩展 {count} 个**Q&A / 推荐 / 对比**类长尾问句。
扩展模式:哪家{seed}好 / {seed}怎么选 / {seed}对比 / {seed}推荐排行 /
        {seed}怎么样 / 求推荐{seed} / {seed}选哪个 / {seed}靠谱吗 / ...
要求:
1. 每条 ≤ 40 字,可含「吗 / 呢 / 哪家 / 怎么」等问句词
2. 不要重复
3. 行业:{industry};地域:{service_geo}
只输出 JSON: {"queries": ["...", "..."]}""",

    "intent": """你是用户意图扩展专家。
针对种子词「{seed}」扩展 {count} 个**意图查询**(用户问怎么做)。
扩展模式:如何选{seed} / {seed}攻略 / {seed}教程 / {seed}使用指南 /
        {seed}怎么用 / {seed}操作步骤 / 新手{seed} / ...
要求:
1. 每条 ≤ 40 字,带「如何 / 怎么 / 攻略 / 指南 / 教程」等意图词
2. 不要重复;不要纯品类词(归 search 场景)
3. 行业:{industry}
只输出 JSON: {"queries": ["...", "..."]}""",

    "brand": """你是品牌评估词扩展专家。
针对品牌名「{seed}」扩展 {count} 个**品牌评估**长尾问句。
品牌全称:{target}(若有别名:{aliases})
扩展模式:{seed}怎么样 / {seed}详细介绍 / {seed}基本信息 /
        {seed}公司概况 / {seed}客户评价如何 / {seed}性价比怎么样 /
        {seed}实力如何 / {seed}靠不靠谱 / {seed}口碑 / ...
要求:
1. 每条 30~40 字,主语必须是品牌名
2. 涵盖正反两面(评价 / 口碑 / 性价比 / 客户 / 实力 / 资质 / 案例)
3. 可适当带地域前缀("{service_geo}{seed}")或业务线("{seed} {industry}")
只输出 JSON: {"queries": ["...", "..."]}""",
}

def render_prompt(scene: str, *, seed: str, count: int, target: str, 
                  aliases: list[str], industry: str, service_geo: str,
                  profile_cases: list[str]) -> str:
    tpl = SCENE_PROMPTS.get(scene) or SCENE_PROMPTS["search"]
    return tpl.format(
        seed=seed, count=count, target=target,
        aliases="、".join(aliases[:5]),
        industry=industry, service_geo=service_geo,
    )
```

**前端传 scene 给 API:**

```ts
// 现状: await api.post(`/topics/${id}/expand-queries`, { seed, count: 50 })
// 改成:
await api.post(`/topics/${id}/expand-queries`, { seed, scene, count: 50 })
```

**回归测试样本:**

| seed | scene | 期望产出形态 |
|---|---|---|
| 污水处理药剂 | search | 污水处理药剂厂家、污水处理药剂供应商、推荐污水处理药剂、... |
| 防水胶 | qa | 哪家防水胶好、防水胶怎么选、防水胶推荐排行、... |
| 跨境并购 | intent | 如何做跨境并购、跨境并购攻略、跨境并购教程、... |
| 南方网通 | brand | 南方网通怎么样、南方网通详细介绍、南方网通客户评价如何、... |

跑通后人工 review 一批样本,确认 LLM 真按场景区分输出。**Phase 2 完成的标志 = 4 个场景手测产出符合预期。**

### Phase 3:前端 UI(2-3 天)

**改动文件:**
- `frontend/src/pages/Dashboard/TopicProfile.tsx` — seed 编辑区改 4 tab(用户侧)
- `frontend/src/pages/Workbench/AdminTopicEdit.tsx` — admin 侧同步
- `frontend/src/components/TopicStepper.tsx` — profile step 内的 seed 输入组件
- `frontend/src/i18n/zh.ts` / `en.ts` — 加 i18n keys

**UI 草图:**

```
┌─ 种子提示词 ─────────────────────────────────────────────────┐
│                                                              │
│  [搜索词场景 32] [问答词场景 12] [意图场景 5] [品牌场景 8]  │
│   ──────────                                                 │
│                                                              │
│  搜索词场景 · 面向「用户搜某类目」的关键词,目标占品类搜索结果  │
│                                                              │
│  ┌────────────────────────────────────┐  [拓 词]            │
│  │ 输入种子词,如「污水处理药剂」      │                     │
│  └────────────────────────────────────┘                     │
│                                                              │
│  已录入:                                                    │
│   • 污水处理药剂 · approved · 140 长尾                       │
│   • 净水药剂 · pending · -                                    │
│   • 防水胶 · approved · 140 长尾  [编辑] [删除]              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**i18n keys(zh):**

```ts
"topicProfile": {
  "seeds": {
    "scene": {
      "search": {
        "label": "搜索词场景",
        "desc": "面向「用户搜某类目」的关键词,目标占品类搜索结果",
        "placeholder": "如:污水处理药剂、防水胶、洗地机",
        "example": "扩词会得到:XX厂家、XX供应商、推荐XX、XX多少钱..."
      },
      "qa": {
        "label": "问答词场景",
        "desc": "面向「用户问推荐/对比」的问答短语",
        "placeholder": "如:洗地机推荐、义齿厂家选哪家",
        "example": "扩词会得到:哪家XX好、XX怎么选、XX对比..."
      },
      "intent": {
        "label": "意图场景",
        "desc": "面向「用户问怎么做」的意图查询",
        "placeholder": "如:瓦片怎么选、跨境并购流程",
        "example": "扩词会得到:如何选XX、XX攻略、XX教程..."
      },
      "brand": {
        "label": "品牌场景",
        "desc": "面向「用户搜品牌名」的品牌评估查询",
        "placeholder": "如:你的品牌名或品牌+业务线",
        "example": "扩词会得到:XX怎么样、XX详细介绍、XX客户评价..."
      }
    }
  }
}
```

**en mirror 略**(键名一致,英文文案翻译版)。

**组件骨架:**

```tsx
// frontend/src/components/SeedSceneTabs.tsx(新建)
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SceneType, SeedPromptItem } from '../types/topic';

const SCENES: SceneType[] = ['search', 'qa', 'intent', 'brand'];

export function SeedSceneTabs({
  seeds, onAdd, onExpand, onRemove,
}: {
  seeds: SeedPromptItem[];
  onAdd: (scene: SceneType, text: string) => void;
  onExpand: (scene: SceneType, text: string) => Promise<void>;
  onRemove: (scene: SceneType, text: string) => void;
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState<SceneType>('search');
  const grouped = SCENES.reduce((acc, s) => {
    acc[s] = seeds.filter(x => (x.scene_type || 'search') === s);
    return acc;
  }, {} as Record<SceneType, SeedPromptItem[]>);

  return (
    <div className="seed-scene-tabs">
      <div className="tabs">
        {SCENES.map(s => (
          <button
            key={s}
            className={s === active ? 'active' : ''}
            onClick={() => setActive(s)}
          >
            {t(`topicProfile.seeds.scene.${s}.label`)}
            <span className="badge">{grouped[s].length}</span>
          </button>
        ))}
      </div>
      <div className="desc">{t(`topicProfile.seeds.scene.${active}.desc`)}</div>
      <SeedEditor
        scene={active}
        seeds={grouped[active]}
        onAdd={(text) => onAdd(active, text)}
        onExpand={(text) => onExpand(active, text)}
        onRemove={(text) => onRemove(active, text)}
      />
    </div>
  );
}
```

**TypeScript 类型(`frontend/src/types/topic.ts`):**

```ts
export type SceneType = 'search' | 'qa' | 'intent' | 'brand';

export interface SeedPromptItem {
  text: string;
  scene_type?: SceneType;   // 老数据没有,默认 search
  status: 'pending' | 'approved' | 'rejected';
  submitted_at?: string;
  approved_at?: string;
  rejected_at?: string;
  reviewer_id?: number;
}

export interface QueryItem {
  text: string;
  scene_type?: SceneType;
  cluster_id?: number;
  status: 'pending' | 'approved' | 'rejected';
  selected: boolean;
  seed: string;
}
```

### Phase 4:UX 收尾 + 数据校验(0.5-1 天)

**改动:**

1. **场景 hint 校验(软提示,不挡保存)** — 用户在「问答词场景」录入 "污水处理药剂"(看起来是品类词),前端弹 toast:"这个词更像搜索词,要不要换到搜索词场景?",可一键移动。
2. **现有用户首次进入显示 banner**:"种子提示词支持 4 类场景了,你的现有种子已归入搜索词场景,可在编辑界面重新分类"
3. **每个场景的 examples 弹窗** — 点 i 图标看 5 条标杆 example seed,降低新手认知门槛
4. **每场景独立 quota(可选)** — 当前 200 上限改成"4 场景共享 200" or "每场景独立 200",建议后者(讯灵每类几百条),但要看 LLM 成本

---

## 4. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 老数据序列化漂移 | 中 | Pydantic `default="search"` 保底;backfill 脚本显式落盘一次 |
| LLM prompt 区分度不够,4 场景产出趋同 | 高 | Phase 2 完成必须做人工 sample review,失败要调 prompt 而非放过 |
| 前端 4 tab UX 复杂 | 中 | 每 tab 独立 examples + desc 文案 + 软校验 hint |
| 唯一性从 text 改成 (text, scene) 引起重复 | 低 | 同字符串跨场景共存是讯灵实测有效模式,**重复就是 feature** |
| 既有的 `QueryItem.seed` 字段语义稳定 | 低 | 不动 `seed` 字段,新增 `scene_type` 并继承 seed 的 scene |
| 用户老业务流程被打断 | 中 | banner + 老数据兼容 + 不强迫立刻分类(就当全是 search 也能跑) |

---

## 5. 验收清单

### Phase 1
- [ ] `SeedPromptItem` / `QueryItem` 加 `scene_type` 字段,Pydantic 反序列化老数据填默认值 search
- [ ] `(text, scene_type)` 联合唯一校验生效
- [ ] backfill 脚本跑完后 staging 库所有 seed/query 都有显式 `scene_type`
- [ ] Phase 1 单测覆盖 ≥ 90%

### Phase 2
- [ ] `/expand-queries` 接受 `scene` 参数,4 值全验
- [ ] 4 prompt 模板交付,4 个 sample seed(每场景 1 个)手测产出符合场景预期
- [ ] 一个 seed 跨场景扩 4 次,产出**有可见差异**(LLM 不能 4 次都吐同一坨)

### Phase 3
- [ ] 用户侧 + admin 侧 TopicEdit 都改完
- [ ] zh + en i18n key 全覆盖
- [ ] 切场景 tab 时 LLM 扩词按对应模板
- [ ] 老数据(全 search)在新 UI 里展示无错位

### Phase 4
- [ ] banner / hint / examples 上线
- [ ] 文档(本文)同步更新到 ENHANCEMENT.md 状态行

---

## 6. 后续可串联的工作(下一步)

完成本方案后,自然衔接 [`geo-vs-xunling.md`](./geo-vs-xunling.md) 的另外两个高优先级借鉴:

1. **推荐词二次包装** — 当前 selected_queries 直接喂 probe,改成「按 scene 加地域前缀 + 关联实体」生成实际 probe 词,probe 真实度大幅提升(讯灵 93% 字符串相似的"加顺德前缀"模式)
2. **probe 5 维指标** — Top1/3/5/可见/信源占比,前端 brand-growth/insights 加 5 个数字卡片

这两件事的输入数据(scene-tagged queries)就是本方案的产出,所以**先做 seed 分场景是这两个 follow-up 的前置条件**。

---

## 附录:涉及文件清单(供 Phase 1 启动时 grep)

```
backend/geo/models/ai_telemetry.py        # SeedPromptItem / QueryItem 数据模型
backend/geo/api/ai_telemetry.py           # expand_queries_for_topic / submit_seed_prompt 
                                          # / _append_seed_prompts / _validate_query_diff
backend/geo/services/query_expander.py    # (新建)4 套 scene prompt 模板
backend/scripts/backfill_seed_scene.py    # (新建)一次性 backfill 老数据
frontend/src/components/SeedSceneTabs.tsx # (新建)4 tab UI 组件
frontend/src/types/topic.ts               # TypeScript type 加 scene_type
frontend/src/pages/Dashboard/TopicProfile.tsx     # 用户侧入口
frontend/src/pages/Workbench/AdminTopicEdit.tsx   # admin 入口
frontend/src/i18n/zh.ts                   # zh i18n
frontend/src/i18n/en.ts                   # en i18n
ENHANCEMENT.md                            # 状态行追加
```
