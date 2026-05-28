# 种子提示词 4 维场景扩展 — 实施方案(终稿)

> 出发点:[`geo-vs-xunling.md`](./geo-vs-xunling.md) §6 借鉴点 #1。
> 方案版本:**v2(scene = 扩展维度,非 seed 属性)** —— v1 让用户选 scene,v2 自动 4 维全覆盖,UX 更简单、数据模型更轻。
> 目标:**用户录入一个中性 seed,系统并行 4 个 LLM 模板扩展出搜索/问答/意图/品牌 4 类长尾池**,前端按 tab 展示。
> 预计工作量:**4-5 天**(数据 0.5 天 + LLM/API 2.5 天 + 前端 1.5 天)。
> 风险:中(LLM 成本 4×、并发失败处理、老数据兼容)。

---

## 0. 一句话目标

```
旧流程: 1 seed →  1 LLM 调用 →  N 条 query(无分类)
新流程: 1 seed → 并行 4 LLM 调用 → 4 类 query 池(各贴 scene 标签)
```

**用户感知:多了 4 个 scene tab,但录入操作没变,产出覆盖 4 倍。**

---

## 1. 设计要点(为什么这样选)

| 决策 | 选择 | 不选的方案 + 原因 |
|---|---|---|
| scene 是 seed 的属性? | ❌ 否,seed 中性 | "seed 带 scene 属性"会逼用户分类录入,4× 操作量 + 认知负担 |
| scene 在哪里体现? | ✅ `QueryItem.scene_type` | 只有产出带场景,seed 不带 |
| 何时分维度? | ✅ 调用 `/expand-queries` 时 server 并行 fan-out | 让前端调 4 次:多 3 次往返 + 前端要做 promise.all 错误聚合 |
| 4 场景是默认全跑还是用户选? | ✅ 默认全勾,用户可取消勾选 | 强制全跑无视成本控制;默认不勾用户体验差 |
| 每场景产出条数 | 50 条 × 4 = 200(对齐 `MAX_EXPANSION_CANDIDATES`) | 各 100 条会突破老上限,改动面更大 |
| 老 query 数据 | ✅ 默认 `scene_type=search`,不强制 backfill | 强制 backfill 增加迁移风险,反正展示侧能 fallback |

---

## 2. 现状基线

### 数据模型(`backend/geo/models/ai_telemetry.py`)

```python
class SeedPromptItem(BaseModel):       # ← 这次不动它
    text: str
    status: ReviewStatus = "pending"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None

class QueryItem(BaseModel):            # ← 加 scene_type 字段
    text: str
    cluster_id: Optional[int] = None
    status: ReviewStatus = "approved"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    selected: bool = True
    seed: str = ""
```

### 扩展 API(`backend/geo/api/ai_telemetry.py:1233`)

```python
@router.post("/topics/{topic_id}/expand-queries")
async def expand_queries_for_topic(topic_id: int, payload: dict, ...):
    seed = (payload.get("seed") or "").strip()
    count = int(payload.get("count", 50))     # ← 单次调用,固定 1 个模板
    ...
    body = {"seed": seed, "count": count, "target": target, ...}
    async with httpx.AsyncClient(timeout=200.0) as client:
        r = await client.post(f"{TELEMETRY_SERVICE_URL}/suggest-queries", json=body)
    return r.json()                            # {"seed": ..., "queries": [...]}
```

---

## 3. 4 套 LLM 模板(直接可用)

放在 `backend/geo/services/query_expander.py`(新建)或 telemetry-service。**每个模板针对**「同一个 seed」**输出不同维度的产出**。

```python
# backend/geo/services/query_expander.py
from typing import Literal

SceneType = Literal["search", "qa", "intent", "brand"]
ALL_SCENES: tuple[SceneType, ...] = ("search", "qa", "intent", "brand")
DEFAULT_SCENE: SceneType = "search"


SCENE_PROMPTS: dict[SceneType, str] = {
    "search": """你是 SEO 长尾词扩展专家。
针对种子词「{seed}」扩展 {count} 个**产品搜索意图**的长尾关键词。

扩展模式参考:
- {seed}厂家 / {seed}供应商 / {seed}生产商 / {seed}制造商
- 推荐{seed}厂家 / 优质{seed}供应商 / 高性价比{seed}
- {seed}多少钱 / {seed}评测 / {seed}排行
- {service_geo}{seed}(若 service_geo 非空)

要求:
1. 每条 ≤ 30 字,纯关键词形态,不带问号
2. 不要重复;不要疑问句(归 qa 场景)
3. 行业上下文:{industry}
4. 主体是品类/产品,不是品牌名

只输出 JSON: {{"queries": ["...", "..."]}}""",

    "qa": """你是 AI 问答词扩展专家。
针对种子词「{seed}」扩展 {count} 个**Q&A / 推荐 / 对比**类长尾问句。

扩展模式参考:
- 哪家{seed}好 / {seed}怎么选 / {seed}选哪家
- {seed}对比 / {seed}推荐排行 / 求推荐{seed}
- {seed}怎么样 / {seed}靠谱吗 / {seed}值不值
- {seed}选哪个 / {seed}哪个性价比高

要求:
1. 每条 ≤ 40 字,带「吗 / 呢 / 哪家 / 怎么 / 哪个」等问句词
2. 不要重复
3. 行业:{industry};地域:{service_geo}

只输出 JSON: {{"queries": ["...", "..."]}}""",

    "intent": """你是用户意图扩展专家。
针对种子词「{seed}」扩展 {count} 个**意图查询**(用户问怎么做、怎么用)。

扩展模式参考:
- 如何选{seed} / 怎么挑{seed} / {seed}怎么用
- {seed}攻略 / {seed}教程 / {seed}使用指南
- 新手{seed}怎么入门 / {seed}操作步骤
- {seed}使用方法 / {seed}什么时候用

要求:
1. 每条 ≤ 40 字,必须带「如何 / 怎么 / 攻略 / 指南 / 教程」等意图词
2. 不要重复;不要纯品类词(归 search 场景)
3. 行业:{industry}

只输出 JSON: {{"queries": ["...", "..."]}}""",

    "brand": """你是品牌评估词扩展专家。
针对品牌名「{target}」(种子词「{seed}」可作为辅助)扩展 {count} 个**品牌评估**长尾问句。

品牌全称:{target}
品牌别名(可用):{aliases}
业务上下文:{seed}(可与品牌组合,如「{target} {seed}」)

扩展模式参考:
- {target}怎么样 / {target}详细介绍 / {target}基本信息
- {target}公司概况 / {target}客户评价如何 / {target}性价比怎么样
- {target}实力如何 / {target}靠不靠谱 / {target}口碑
- {service_geo}{target}(若 service_geo 非空,加地域前缀变更精准)

要求:
1. 每条 30~40 字,主语必须是品牌名(target 或别名)
2. 涵盖正反两面(评价 / 口碑 / 性价比 / 客户 / 实力 / 资质)
3. 不要重复

只输出 JSON: {{"queries": ["...", "..."]}}""",
}


def render_prompt(
    scene: SceneType, *,
    seed: str, count: int, target: str,
    aliases: list[str], industry: str, service_geo: str,
) -> str:
    tpl = SCENE_PROMPTS.get(scene) or SCENE_PROMPTS["search"]
    return tpl.format(
        seed=seed, count=count,
        target=target or seed,
        aliases="、".join(aliases[:5]) or "(无)",
        industry=industry or "(未指定)",
        service_geo=service_geo or "",
    )
```

**brand 场景的特殊兜底:** 如果 `target`(品牌全称)为空或跟 seed 字符串相同,`render_prompt` 自动用 seed 填充,LLM 仍能产出可用的品牌评估句。

---

## 4. 4 个 Phase 渐进交付

### Phase 1:数据模型(0.5 天)

**改动文件:**
- `backend/geo/models/ai_telemetry.py` — `QueryItem` 加 `scene_type` 字段

**代码:**

```python
from typing import Literal
SceneType = Literal["search", "qa", "intent", "brand"]
DEFAULT_SCENE: SceneType = "search"
ALL_SCENES: tuple[SceneType, ...] = ("search", "qa", "intent", "brand")

class QueryItem(BaseModel):
    text: str
    scene_type: SceneType = DEFAULT_SCENE      # ← 新增,老数据默认 search
    cluster_id: Optional[int] = None
    status: ReviewStatus = "approved"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    selected: bool = True
    seed: str = ""
```

**SeedPromptItem 不动**(seed 是中性的)。

**老数据兼容:** Pydantic `default="search"` 保底,反序列化老 `queries_json` 时所有 query 自动归为 search 场景。**无需 backfill 脚本**。

**唯一性约束维持原样:** `text` 唯一(不是 `(text, scene_type)`),因为同一句长尾不会在不同 scene 模板下重复产出(prompt 已隔离形态)。

### Phase 2:API + LLM fan-out(2 天)

**改动文件:**
- `backend/geo/api/ai_telemetry.py:1233` `expand_queries_for_topic` — 改成 fan-out 4 路并行
- `backend/geo/services/query_expander.py`(新建)— 4 套模板 + render
- telemetry-service `/suggest-queries`(如不可改,fallback 在 query_expander.py 自己跑 LLM)

**API 改动:**

```python
import asyncio

@router.post("/topics/{topic_id}/expand-queries")
async def expand_queries_for_topic(topic_id: int, payload: dict, ...):
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)

    seed = (payload.get("seed") or "").strip()
    if not seed:
        raise HTTPException(400, "seed cannot be empty")

    # 用户可指定 scene 子集,默认全 4 个
    scenes = payload.get("scenes") or list(ALL_SCENES)
    invalid = [s for s in scenes if s not in ALL_SCENES]
    if invalid:
        raise HTTPException(400, f"invalid scenes: {invalid}")

    count_per_scene = int(payload.get("count_per_scene", 50))
    count_per_scene = max(5, min(count_per_scene, MAX_EXPANSION_CANDIDATES // 4))

    target = t.target or ""
    aliases = _safe_load_json(t.target_aliases_json, [])
    industry = t.industry or ""
    profile_obj = _safe_load_json(t.profile_json, {})
    service_geo = str(profile_obj.get("service_geo") or "").strip()[:200]
    profile_cases = _extract_profile_cases(profile_obj)

    # 并行 fan-out
    async with httpx.AsyncClient(timeout=200.0) as client:
        async def call_one(scene: SceneType) -> tuple[SceneType, dict]:
            body = {
                "seed": seed, "scene": scene, "count": count_per_scene,
                "target": target, "aliases": aliases,
                "industry": industry, "service_geo": service_geo,
                "profile_cases": profile_cases,
            }
            url = f"{TELEMETRY_SERVICE_URL}/suggest-queries"
            try:
                r = await client.post(url, json=body)
                if r.status_code != 200:
                    return scene, {"queries": [], "error": r.text[:300]}
                return scene, r.json()
            except httpx.HTTPError as e:
                return scene, {"queries": [], "error": str(e)[:300]}

        results = await asyncio.gather(*[call_one(s) for s in scenes])

    # 聚合产出 + 写 expansion_log_json
    out_scenes: dict[str, dict] = {}
    total_count = 0
    for scene, data in results:
        qs = data.get("queries") or []
        out_scenes[scene] = {
            "queries": qs,
            "model": data.get("model"),
            "error": data.get("error"),
        }
        total_count += len(qs)
        # 给每条 query 贴 scene 标签(写日志用)
        excerpt = ", ".join(qs[:3])
        _append_expansion_log(
            t, seed=seed, model=data.get("model") or "deepseek",
            expanded_count=len(qs), raw_excerpt=excerpt, scene=scene,
        )
    db.commit()

    return {"seed": seed, "scenes": out_scenes, "total_count": total_count}
```

**返回 schema:**

```jsonc
{
  "seed": "防水胶",
  "total_count": 200,
  "scenes": {
    "search":  {"queries": ["防水胶厂家", "防水胶供应商", ...], "model": "deepseek-chat"},
    "qa":      {"queries": ["哪家防水胶好", "防水胶怎么选", ...], "model": "deepseek-chat"},
    "intent":  {"queries": ["如何选防水胶", "防水胶使用攻略", ...], "model": "deepseek-chat"},
    "brand":   {"queries": [], "error": "target is empty, brand scene skipped"}
  }
}
```

**部分失败容忍:** 4 路并行,其中 1 路失败不影响其他 3 路返回;前端按 `scenes[scene].error` 决定要不要展示错误标签。

**telemetry-service 改动**(假设我们能改):

```python
# telemetry-service /suggest-queries
def suggest_queries(payload: dict):
    scene = payload.get("scene") or "search"
    if scene not in ALL_SCENES:
        raise HTTPException(400, "invalid scene")
    prompt = render_prompt(scene, **{
        k: payload.get(k) for k in (
            "seed", "count", "target", "aliases",
            "industry", "service_geo", "profile_cases",
        )
    })
    result = call_llm(prompt)
    return {"queries": result, "model": "deepseek-chat", "scene": scene}
```

如果 telemetry-service 不在我们控制,在 `query_expander.py` 自己实现 LLM 调用(参考 `content_generator._generate_one` 现成代码)。

**`_append_expansion_log` 加 scene 字段:**

```python
def _append_expansion_log(t, *, seed, model, expanded_count, raw_excerpt, scene="search"):
    try:
        logs = json.loads(t.expansion_log_json or "[]")
    except Exception:
        logs = []
    logs.append({
        "at": datetime.utcnow().isoformat(),
        "seed": seed, "scene": scene,
        "model": model, "expanded_count": expanded_count,
        "raw_excerpt": raw_excerpt,
    })
    t.expansion_log_json = json.dumps(logs, ensure_ascii=False)
```

### Phase 3:前端 UI(1.5 天)

**改动文件:**
- `frontend/src/types/topic.ts` — 加 `SceneType` 类型 + `QueryItem.scene_type` 字段
- `frontend/src/pages/Dashboard/TopicProfile.tsx`(用户侧)— 拓词区改造
- `frontend/src/pages/Workbench/AdminTopicEdit.tsx`(admin 侧)— 同步
- `frontend/src/components/SceneExpander.tsx`(新建)— 复用组件
- `frontend/src/i18n/zh.ts` / `en.ts` — i18n keys

**UI 草图:**

```
┌─ 种子提示词扩展 ─────────────────────────────────────────────┐
│                                                              │
│  [防水胶________________________________]                   │
│                                                              │
│  扩展维度(每维 50 条):                                     │
│  ☑ 搜索词    ☑ 问答词    ☑ 意图    ☑ 品牌                  │
│                                                              │
│                                              [拓 词]         │
└──────────────────────────────────────────────────────────────┘
              ↓ 拓词后展示分类池
┌─ 扩展结果 ─ 共 200 条 ──────────────────────────────────────┐
│                                                              │
│  [搜索词 50] [问答词 50] [意图 50] [品牌 50]                │
│   ───────                                                    │
│                                                              │
│  ☐ 防水胶厂家             ☐ 防水胶供应商                    │
│  ☐ 防水胶生产商           ☐ 推荐防水胶厂家                  │
│  ☐ 高性价比防水胶         ☐ 优质防水胶供应商                │
│  ...                                                         │
│                                                              │
│  [本场景全选] [全场景全选] [送 probe] [生成文章]            │
└──────────────────────────────────────────────────────────────┘
```

**组件骨架:**

```tsx
// frontend/src/components/SceneExpander.tsx(新建)
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SceneType, QueryItem } from '../types/topic';

const SCENES: SceneType[] = ['search', 'qa', 'intent', 'brand'];

interface Props {
  onExpand: (seed: string, scenes: SceneType[]) => Promise<Record<SceneType, QueryItem[]>>;
  onSelect: (queries: QueryItem[]) => void;
}

export function SceneExpander({ onExpand, onSelect }: Props) {
  const { t } = useTranslation();
  const [seed, setSeed] = useState('');
  const [enabledScenes, setEnabledScenes] = useState<Set<SceneType>>(new Set(SCENES));
  const [results, setResults] = useState<Record<SceneType, QueryItem[]> | null>(null);
  const [activeTab, setActiveTab] = useState<SceneType>('search');
  const [loading, setLoading] = useState(false);

  const handleExpand = async () => {
    if (!seed.trim()) return;
    setLoading(true);
    try {
      const data = await onExpand(seed.trim(), Array.from(enabledScenes));
      setResults(data);
      // pick first non-empty scene as active
      const firstNonEmpty = SCENES.find(s => (data[s]?.length || 0) > 0);
      if (firstNonEmpty) setActiveTab(firstNonEmpty);
    } finally { setLoading(false); }
  };

  return (
    <div className="scene-expander">
      <input
        value={seed}
        onChange={e => setSeed(e.target.value)}
        placeholder={t('topicProfile.seeds.placeholder')}
      />
      <div className="dimensions">
        <span>{t('topicProfile.seeds.dimensions')}</span>
        {SCENES.map(s => (
          <label key={s}>
            <input
              type="checkbox"
              checked={enabledScenes.has(s)}
              onChange={e => {
                const next = new Set(enabledScenes);
                e.target.checked ? next.add(s) : next.delete(s);
                setEnabledScenes(next);
              }}
            />
            {t(`topicProfile.seeds.scene.${s}.label`)}
          </label>
        ))}
      </div>
      <button onClick={handleExpand} disabled={loading || enabledScenes.size === 0}>
        {loading ? t('topicProfile.seeds.expanding') : t('topicProfile.seeds.expand')}
      </button>

      {results && (
        <div className="results">
          <div className="tabs">
            {SCENES.map(s => {
              const count = results[s]?.length || 0;
              return (
                <button
                  key={s}
                  className={s === activeTab ? 'active' : ''}
                  onClick={() => setActiveTab(s)}
                  disabled={count === 0}
                >
                  {t(`topicProfile.seeds.scene.${s}.label`)}
                  <span className="badge">{count}</span>
                </button>
              );
            })}
          </div>
          <QueryList items={results[activeTab] || []} onSelect={onSelect} />
        </div>
      )}
    </div>
  );
}
```

**API client:**

```ts
// frontend/src/services/topic.ts
export async function expandQueries(
  topicId: number,
  seed: string,
  scenes: SceneType[],
  countPerScene = 50,
): Promise<{ seed: string; total_count: number; scenes: Record<SceneType, { queries: string[]; model?: string; error?: string }> }> {
  const r = await api.post(`/topics/${topicId}/expand-queries`, {
    seed, scenes, count_per_scene: countPerScene,
  });
  return r.data;
}
```

**TypeScript 类型(`frontend/src/types/topic.ts`):**

```ts
export type SceneType = 'search' | 'qa' | 'intent' | 'brand';

export interface QueryItem {
  text: string;
  scene_type?: SceneType;
  cluster_id?: number;
  status: 'pending' | 'approved' | 'rejected';
  selected: boolean;
  seed: string;
}
```

**i18n keys(zh):**

```ts
"topicProfile": {
  "seeds": {
    "placeholder": "输入种子词,如「防水胶」「跨境并购」",
    "dimensions": "扩展维度(每维 50 条):",
    "expand": "拓 词",
    "expanding": "扩展中…",
    "scene": {
      "search": {
        "label": "搜索词",
        "desc": "面向「用户搜某类目」的关键词,目标占品类搜索结果"
      },
      "qa": {
        "label": "问答词",
        "desc": "面向「用户问推荐/对比」的问答短语"
      },
      "intent": {
        "label": "意图",
        "desc": "面向「用户问怎么做」的意图查询"
      },
      "brand": {
        "label": "品牌",
        "desc": "面向「用户搜品牌名」的品牌评估查询"
      }
    },
    "selectThisScene": "本场景全选",
    "selectAll": "全场景全选",
    "sendToProbe": "送 probe",
    "generateContent": "生成文章"
  }
}
```

英文版同 key 镜像。

### Phase 4:UX 收尾(0.5 天)

1. **拓词 loading 状态 + 4 维并行进度** — 4 个 scene tab 用骨架屏占位,完成一个亮一个,降低等待感
2. **错误分维度展示** — 某 scene 失败时该 tab 显示「品牌场景扩词失败:target 为空」,其他 3 个 tab 正常展示
3. **空 brand 场景兜底** — 如果 `target`(品牌全称)未填,brand 场景默认禁用 checkbox + 灰显「需在「资料」节填写品牌全称」
4. **expansion_log 在 admin 侧展示** — admin 可看历史扩词记录,知道哪个 scene 扩了多少次、模型版本

---

## 5. 工作量分解

| Phase | 任务 | 工时 | 文件 |
|---|---|---|---|
| 1 | `QueryItem.scene_type` 字段 + 单元测试 | 0.5 天 | `models/ai_telemetry.py`、单测 |
| 2 | `query_expander.py` 4 模板 | 0.5 天 | 新建 |
| 2 | `expand_queries_for_topic` fan-out 重写 | 0.5 天 | `api/ai_telemetry.py` |
| 2 | telemetry-service 或 fallback LLM 调用 | 0.5 天 | 跨服务 |
| 2 | 集成测试 + 4 模板手测样本 | 1 天 | 必做 |
| 3 | `SceneExpander.tsx` 组件 | 0.5 天 | 新建 |
| 3 | 集成到 TopicProfile + AdminTopicEdit | 0.5 天 | 改 2 处 |
| 3 | i18n zh + en + API client | 0.5 天 | 4 文件 |
| 4 | UX 收尾(loading / 错误展示 / 兜底) | 0.5 天 | UI 微调 |
| **合计** | | **5 天** | |

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| LLM 成本 4× | 中 | (a) 默认 brand 场景仅 target 非空时跑;(b) 用户可取消勾选场景;(c) 每场景 50 条上限不松 |
| 4 路并行有 1 路超时 | 中 | `asyncio.gather` 不抛(用 try/except 包),错误隔离到单 scene |
| 4 场景产出趋同 | 高 | Phase 2 完成前必须做人工 sample review(4 seed × 4 scene = 16 输出全看一遍) |
| 老 query 没 scene_type | 低 | Pydantic 默认 search;前端兜底 `scene_type ?? 'search'` |
| 同 seed 多次拓词产出累积 | 中 | 现有去重逻辑(text 唯一)继续生效,跨场景同句不重复 |
| brand 场景在 target 为空时无意义 | 中 | API 层检测 + 前端禁用 checkbox + 文案提示 |

---

## 7. 验收清单

### Phase 1
- [ ] `QueryItem.scene_type` 字段加好,默认值 `search`
- [ ] Pydantic 反序列化老 `queries_json` 不报错,所有 query 自动归 search
- [ ] 单测:`QueryItem` 4 种 scene_type 轮 round-trip

### Phase 2
- [ ] `query_expander.py` 4 套 prompt 模板交付,`render_prompt` 单测通过
- [ ] `/expand-queries` 接受 `scenes` 数组参数,默认全 4 个
- [ ] fan-out 并行:实测一次调用产出 ≤ 200 query 分 4 类
- [ ] 部分失败容忍:故意构造一个 scene 失败,其他 3 个仍返回
- [ ] 人工 sample review:4 个测试 seed(品类词 / 问答词 / 意图词 / 品牌词)各跑 4 场景,产出符合预期形态
- [ ] `expansion_log_json` 记录 scene 字段

### Phase 3
- [ ] `SceneExpander.tsx` 在用户侧 + admin 侧都接入
- [ ] zh + en i18n 全覆盖
- [ ] 复选框默认全勾,可单独取消
- [ ] 拓词后按 scene 切 tab,每 tab 内可勾选 query 送 probe / 生成文章
- [ ] 老数据(无 scene_type)展示无错位,默认归 search tab

### Phase 4
- [ ] 4 scene 并行 loading 进度可见
- [ ] 某 scene 失败时该 tab 显示错误,其他 tab 正常
- [ ] brand 场景在 target 为空时禁用 + 文案引导

---

## 8. 后续衔接

完成本方案后,自然解锁:

1. **推荐词二次包装**(`geo-vs-xunling.md` §6 #2):4 个分类 query 池里,挑送 probe 的 query 时按 scene 走不同变形规则(brand 场景加地域前缀,intent 场景加关联实体)
2. **probe 5 维指标**(`geo-vs-xunling.md` §6 #4):4 类 query 各自跑 probe,在前端 brand-growth/insights 加 5 个数字卡片,**按 scene 拆分展示更直观**
3. **媒体类型差异化文章**(`geo-vs-xunling.md` §6 #5):content_generator 可按 query.scene_type 用不同 prompt(brand 场景写品牌稿,intent 场景写教程稿)

---

## 附录:涉及文件清单

```
# Phase 1
backend/geo/models/ai_telemetry.py            QueryItem 加 scene_type

# Phase 2
backend/geo/services/query_expander.py        (新建)4 prompt 模板 + render
backend/geo/api/ai_telemetry.py               expand_queries_for_topic fan-out 重写
# telemetry-service /suggest-queries          (外部服务)接收 scene 参数

# Phase 3
frontend/src/types/topic.ts                   SceneType 类型 + QueryItem.scene_type
frontend/src/components/SceneExpander.tsx     (新建)组件
frontend/src/pages/Dashboard/TopicProfile.tsx 用户侧入口
frontend/src/pages/Workbench/AdminTopicEdit.tsx  admin 入口
frontend/src/services/topic.ts                expandQueries API client
frontend/src/i18n/zh.ts                       i18n zh
frontend/src/i18n/en.ts                       i18n en

# 文档
ENHANCEMENT.md                                状态行追加 "Seed 4-scene Expansion"
```
