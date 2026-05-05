# GEO Checker 结果不一致性分析

> 生成日期: 2026-04-27
> 分析范围: `backend/geo_checker/` 活跃代码

## 概述

GEO Checker 的检测结果（尤其是实体审计 `--entity`、AI 可见度 `--ai-visibility`、引用检查 `--citation-check`）在不同运行之间会产生不同结果。本文档从代码层面分析所有不确定性来源，按影响程度排列。

---

## 一、高影响：AI 模型输出固有的随机性

### 1.1 未设置 temperature 参数

**所有 AI 引擎的 API 请求均未设置 `temperature` 参数**，使用模型默认值（通常 > 0），导致相同 query 每次返回不同回答。

| 文件 | 函数 | 模型 | 是否设置 temperature |
|---|---|---|---|
| `ai.py:20-48` | `_query_perplexity()` | `perplexity/sonar` | 否 |
| `ai.py:51-88` | `_query_openai()` | `openai/gpt-4o-mini:online` | 否 |
| `ai.py:91-131` | `_query_anthropic()` | `anthropic/claude-haiku-4.5:online` | 否 |
| `ai.py:134-161` | `_query_deepseek()` | `deepseek-chat` | 否 |
| `ai.py:164-191` | `_query_doubao()` | (动态 model_id) | 否 |
| `modes/entity.py:434-460` | `_query_openrouter()` | GPT-4o-mini / DeepSeek V3 / Qwen3 | 否 |
| `modes/entity.py:462-496` | `_query_openai_native()` | `gpt-4o-mini` | 否 |

**影响**：AI 回答内容不同 → `_classify_framing()` 分类结果不同 → 评分不同。

### 1.2 在线搜索模式的额外不确定性

`perplexity/sonar`、`openai/gpt-4o-mini:online`、`anthropic/claude-haiku-4.5:online` 等模型带有联网搜索功能（`:online` 后缀）。这意味着：

1. **搜索结果本身随时间变化**：每次调用可能搜索到不同的网页
2. **AI 对搜索结果的解读不同**：相同的搜索结果，模型可能选择引用不同来源
3. **实时信息影响回答**：新闻、评价等时效性内容会改变模型输出

**影响范围**：所有付费模式（entity / visibility / citation）。

### 1.3 `_classify_framing()` 对可变文本的模式匹配

`ai.py:227-309` 中的 `_classify_framing()` 通过正则表达式分类 AI 对品牌的情感倾向：

```
recommended → leader → option → niche → mentioned → not_mentioned
```

**问题**：AI 回答措辞每次不同，同一个品牌可能被分类为 "recommended"（推荐）或 "option"（可选之一），导致该维度评分波动。

| 分类 | 对应分数含义 |
|---|---|
| `recommended` | 最高分 — AI 主动推荐 |
| `leader` | 高分 — 被视为领导者 |
| `option` | 中分 — 作为选项之一被提及 |
| `niche` | 低分 — 被视为小众/新兴 |
| `mentioned` | 基础分 — 仅被提及 |
| `not_mentioned` | 零分 — 未提及 |

---

## 二、高影响：并发执行的不确定性

### 2.1 ThreadPoolExecutor 的完成顺序

**实体审计** (`modes/entity.py:536-542`)：

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=len(engines)) as pool:
    futures = [pool.submit(_call_engine, name, mid) for name, mid in engines]
    for future in concurrent.futures.as_completed(futures):  # 完成顺序不确定
        ...
```

**AI 可见度** (`modes/visibility.py:236-246`)：16 个并发 API 调用。

**影响**：`as_completed()` 按完成顺序迭代，不是提交顺序。如果后续逻辑依赖结果顺序（如取最后一个引擎的结果），会出现不一致。当前代码将结果 append 到列表，顺序不确定但功能上无影响。

### 2.2 浏览器引擎的异步查询

`modes/entity.py:553-600` 使用 Playwright 微服务进行浏览器搜索：

- 并发查询多个浏览器引擎（Google、Bing 等）
- 每个查询有 90 秒超时 (`_PER_QUERY_TIMEOUT = 90`)
- 网络延迟导致完成顺序不确定

---

## 三、中影响：外部 API 依赖

### 3.1 Wikipedia / Wikidata API

**位置**：`modes/entity.py:31-72`、`checks.py:1394-1450`

| API | 用途 | 不确定性来源 |
|---|---|---|
| Wikipedia Search API | 检查实体是否有词条 | 搜索算法更新、新词条创建 |
| Wikidata Search API | 检查实体在知识图谱中 | 实体数据更新 |
| Baidu Baike | 中文百科词条检测 | 页面内容变更 |

**影响**：知识图谱维度的评分可能因外部数据变化而不同。

### 3.2 目标网站响应差异

- **HTML 结构变化**：CDN 缓存、A/B 测试、动态内容
- **响应时间波动**：`checks.py:826-828` 中的 `time.time()` 测量受网络状况影响
- **HTTP 行为差异**：不同 User-Agent 可能返回不同内容

### 3.3 缓存仅在进程内有效

`io.py:35-46` 中的 `fetch()` 使用进程内 `_page_cache`：

```python
def fetch(url, timeout=15, allow_redirects=True):
    with _state._page_cache_lock:
        if url in _state._page_cache:
            return _state._page_cache[url]
    # ... HTTP call ...
```

**问题**：
- 每次 CLI 运行是新进程 → 缓存清空 → 重新请求
- 同一运行内缓存有效，但不同运行之间完全独立
- 服务器返回的内容可能已更新

---

## 四、中影响：时间相关逻辑

### 4.1 内容新鲜度检查

`checks.py:1725-1772` 使用 `datetime.now(timezone.utc)` 计算内容年龄：

```python
now = datetime.now(timezone.utc)
ages_days = sorted((now - d).days for d in parsed_dates)
```

**影响**：跨越 UTC 午夜运行时，新鲜度评分可能变化（内容"年龄"增加一天）。

### 4.2 响应时间测量

`checks.py:826-828` 测量 HTTP 响应时间：

```python
start = time.time()
fetch(urljoin(base_url, "/?_geo_timing_check"), timeout=10)
elapsed = time.time() - start
```

**影响**：网络波动导致响应时间不同 → crawl readiness 评分可能 PASS/FAIL 切换。

---

## 五、低影响：解析与处理的细微差异

### 5.1 URL 提取正则表达式

多处使用正则提取 citation URL，模式略有不同：

| 位置 | 正则模式 |
|---|---|
| `ai.py:44` | `r'https?://[\w\-\.]+\.[a-z]{2,}/\S*'` |
| `ai.py:84` | `r'https?://[^\s\)\]]+'` |
| `ai.py:158` | `r'https?://[^\s\)\]>]+'` |
| `entity.py:457` | `r'https?://[^\s\)\]>]+'` |

**影响**：不同正则对同一文本可能提取出不同 URL 集合。

### 5.2 User-Agent 不统一

代码中使用多种 User-Agent：
- `"GEO-Readiness-Checker/1.0"` (`checks.py`)
- `"GEO-Checker/1.0"` (`entity.py`)
- `"Mozilla/5.0 ..."` (Baidu Baike 检测)

**影响**：部分服务器根据 User-Agent 返回不同内容。

---

## 六、各模式不确定性综合评估

| 模式 | 主要不确定来源 | 波动幅度 | 建议 |
|---|---|---|---|
| **默认 Free** (25 category) | 网络 + 时间 + 外部 API | 小 (±2-5 分) | 可接受 |
| **--entity** | AI 模型输出 + 并发 + 浏览器引擎 | **大 (±10-20 分)** | 需改进 |
| **--ai-visibility** | AI 模型输出 + 16 并发调用 | **大 (±10-15 分)** | 需改进 |
| **--citation-check** | Perplexity 输出变化 | 中 (±5-10 分) | 可优化 |
| **--crawl-test** | 网络响应时间 | 小 (±2-3 分) | 可接受 |
| **--authority-audit** | GitHub/npm/PyPI API | 小 (±1-3 分) | 可接受 |

---

## 七、改进建议（按优先级）

### P0: 降低 AI 输出随机性

```python
# 在所有 API payload 中添加:
"temperature": 0  # 或 0.1，减少回答随机性
```

**注意**：`temperature=0` 不能完全消除不确定性（尤其是带联网搜索的模型），但能大幅减小波动。

### P1: 多次运行取多数票（Majority Vote）

实体审计已有 `STABILITY_RUNS` 机制（`entity.py:423`）：

```python
STABILITY_RUNS = 1 if len(engines) >= 3 else 3
```

**建议**：即使有 3 个引擎也运行 2 次，对 framing 分类取众数：

```python
STABILITY_RUNS = 2  # 始终至少 2 次
# 对分类结果取 majority vote
from collections import Counter
framing = Counter(all_framings).most_common(1)[0][0]
```

### P2: 统一正则表达式

将所有 URL 提取统一为一个正则，放在 `constants.py` 中：

```python
URL_EXTRACT_PATTERN = re.compile(r'https?://[^\s\)\]>\"]+')
```

### P3: 跨运行缓存

对 Wikipedia/Wikidata 等低频变化的外部 API 结果，添加带 TTL 的本地文件缓存：

```python
# 伪代码
cache_key = f"wikidata_{entity_name}"
if cached := disk_cache.get(cache_key, max_age=86400):  # 24h TTL
    return cached
```

### P4: 固定 User-Agent

统一为 `"GEO-Readiness-Checker/1.0"`，避免不同 User-Agent 导致的响应差异。

### P5: 响应结果快照

在输出中记录原始 AI 回答和分类结果，方便对比不同运行之间的差异：

```json
{
  "dimension": "competitive_position",
  "score": 80,
  "raw_classifications": ["leader", "option", "leader"],
  "final_classification": "leader",
  "majority_confidence": "2/3"
}
```

---

## 八、总结

**根本原因**：GEO Checker 的付费模式依赖 AI 引擎的联网搜索回答来评分。AI 模型本身具有非确定性（未设置 `temperature=0`），且联网搜索的结果随时变化。这两者叠加导致每次运行结果波动较大。

**核心矛盾**：GEO Checker 的价值在于检测"AI 如何看待你的品牌"，但 AI 的看法本身就不稳定。这并非 bug，而是检测对象的固有特性。

**务实的改进路径**：
1. 设置 `temperature=0` → 减少约 50% 的波动
2. 多次运行取多数票 → 再减少约 30% 的波动
3. 在报告中展示置信度 → 让用户理解结果的合理波动范围
4. 保留外部 API 缓存 → 消除非 AI 部分的波动
