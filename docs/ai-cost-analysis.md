# AI 调用成本分析

> 本文说明 `/api/check/advanced/visibility` / `/api/check/advanced/entity` / `/api/check/advanced/citation` 这类"付费高级检测"每一次请求为什么会调那么多次外部 AI,怎么算钱,以及哪里可以收敛。

## 0. 简表

| 端点 | 单次请求 AI 调用数 | 典型成本 | 耗时 |
|---|---|---|---|
| `/visibility` | **~30 次**(2026-04-17 `STABILITY_RUNS` 3→1 后) | **$0.08 – $0.50** | 40-60 秒 |
| `/entity` | ~50-60 次 | $0.15 – $1.00 | 1-2 分钟 |
| `/citation` | ~5-7 次 | $0.02 – $0.05 | 30-60 秒 |
| `/aeo`(免费) | 0 次 | $0 | < 5 秒 |
| `/compare` / `/crawl-test` / `/authority`(免费) | 0 次 | $0 | 3-30 秒 |

其余默认 check(`/api/check` / `/api/check/anonymous`)**完全不调 AI**,只拉目标站点自己的 HTML + 公开 API(Wikipedia / Wikidata / GitHub search 等),纯免费。

---

## 1. `/visibility` 为什么是 90 次

最贵的端点,值得细看。

代码位置:`backend/geo_checker/modes/visibility.py::ai_visibility`。

### 1.1 调用数的构造

```
调用总数 = query 数 × AI 引擎数 × STABILITY_RUNS
```

三个乘子,每个都不是 1:

#### 乘子 A:query 数(~10)

`ai_visibility` 不是只问"你知道这个品牌吗?"一次,而是**按 5 类 intent 构造问题集**:

| 类别 | 问题数 | 例子 |
|---|---|---|
| Entity Definition | 3 | `What is {brand}?` / `What does {domain} do?` / `Who is {brand} for?` |
| Competitive | 3 | `Best alternatives to {brand}` / `{brand} review` / `Is {brand} reliable and trustworthy?` |
| Category Association | 0-2 | 从 meta description 派生:`Best tools for {site_description[:80]}` |
| Custom | 0-N | 用户通过 `--queries` / `custom_queries` 追加的自定义 |
| Gap Detection | 2 | `Top {description-derived} solutions` / `Best {brand} competitors 2025` |

典型站点 query 数 = 3 + 3 + 1 + 0 + 2 = **9-10** 条。用户传了 `--queries` 会再加。

#### 乘子 B:AI 引擎数(通常 3)

```python
if openrouter_key:
    engines["Perplexity"] = ("perplexity", openrouter_key)
    engines["ChatGPT"]    = ("openai", openrouter_key)
    engines["Claude"]     = ("anthropic", openrouter_key)
```

只要设了 `OPENROUTER_API_KEY` 就全开三个主流引擎。还可以追加:

- `DEEPSEEK_API_KEY` → +1 引擎(国产对齐中文场景)
- `DOUBAO_API_KEY` + `DOUBAO_MODEL_ID` → +1 引擎(ByteDance Ark)

典型生产配置 = 3 engines(Perplexity / ChatGPT / Claude via OpenRouter)。

**设计意图**:AI 引擎之间彼此训练数据 / 检索逻辑 / 排序偏好不同,一个品牌可能 Perplexity 认识、ChatGPT 不认识。只问一个引擎会偏颇。

#### 乘子 C:`STABILITY_RUNS`(**2026-04-17 已从 3 改为 1**)

```python
STABILITY_RUNS = 3  # geo_checker/modes/visibility.py
```

**同一个 query × 同一个引擎,跑 3 次**。为什么?

AI 引擎的 `temperature > 0`,同一 prompt 两次回答会不同。尤其是边界情况:
- 某次回答里提到你的域名,某次没提 → 品牌是否"被 AI 提及"? 3 次采样 → 用投票/均值得出稳定答案。
- 某次推荐竞品 A,某次推荐竞品 B → 综合 3 次才能看清真正的竞品池。

`STABILITY_RUNS=3` 的代价是**线性放大调用数 3 倍**。

### 1.2 乘起来

```
90 = 10 queries × 3 engines × 3 stability_runs
```

### 1.3 并发度影响耗时不影响成本

`geo_checker/modes/visibility.py` 里:

```python
with ThreadPoolExecutor(max_workers=8) as pool:
    # 90 个 task 并发 submit,每次 8 个同时跑
```

- 90 / 8 ≈ **12 批次**
- 单调用典型 3-10 秒(带 `:online` 联网搜索的模型)
- 总耗时:**60-180 秒**(best case 40s,worst case 400s+ 撞前端 axios 300s 超时)

并发度不降低钱,只降低墙钟时间。

---

## 2. 单次调用的钱从哪来

上游走 OpenRouter 统一网关(`https://openrouter.ai/api/v1/chat/completions`):

| Engine 参数 | 实际模型 | 定价(2026-04 OpenRouter 公开页) |
|---|---|---|
| `perplexity/sonar` | Perplexity Sonar(含实时联网搜索) | **~$5 / M input tokens · ~$5 / M output · $0.005 / search** |
| `openai/gpt-4o-mini:online` | GPT-4o-mini + 开启 web browsing | **~$0.15 / M input · ~$0.60 / M output · $0.004 / browse** |
| `anthropic/claude-haiku-4.5:online`(当前) | Claude Haiku 4.5 + 联网 | **~$1 / M input · ~$5 / M output · $0.006 / search** |
| ~~`anthropic/claude-sonnet-4:online`~~(历史) | Claude Sonnet 4 + 联网 | ~$3 / M input · ~$15 / M output · $0.006 / search(2026-04-17 换成 Haiku 4.5,成本 -67%) |

每次调用 token 用量大约:

- Input: prompt(问题本身)+ `"messages":[...]` 壳 ≈ 50-200 tokens
- Output: AI 回答 ≈ 300-1500 tokens(我们设的 `max_tokens=1024` 是上限)
- 若是 `:online` 模型,还会 billable 一次 web search

单次调用典型成本:

| 引擎 | 低估($) | 高估($) |
|---|---|---|
| Perplexity(含 search) | 0.008 | 0.020 |
| GPT-4o-mini(含 browse) | 0.005 | 0.012 |
| Claude Sonnet 4(含 search) | 0.015 | 0.040 |

按**平均 $0.012 / call** 估算:

$$
\text{90 calls} \times \$0.012 = \$1.08 \text{ per /visibility request}
$$

**实际范围 $0.25 – $1.50**,看目标站点是否冷门(冷门→AI 说得多→output tokens 多→贵)。

### 2.1 月度预估

| 用户日均 `/visibility` 次数 | 月度 AI 账单 |
|---|---|
| 10 次/天 | ~$300 |
| 50 次/天 | ~$1,500 |
| 200 次/天 | ~$6,000 |

**这是定价 / 收费策略的硬成本线**。定价的会员 visibility 权益必须覆盖每次 $0.5-$1.5 直接成本 + 其他开销。

---

## 3. 为什么不能简单砍掉?

每个乘子都有设计理由,不是随便选的:

### 3.1 query 数(10 条)不能砍到 1

单条 query 的信号太弱。例子:
- 只问 `"What is MoltsPay?"` → AI 不知道 → 得出"品牌不 visible"
- 但问 `"Best alternatives to MoltsPay"` → AI 可能在列举中提到 → 其实 visible

多 query 的目的是**覆盖不同 intent 下的答案池**,给出更全面的"AI 是否认识这个品牌"画像。

### 3.2 引擎数(3)不能砍到 1

不同 AI 引擎的训练 cut-off、知识库、实时检索源都不一样:
- Perplexity 强在实时(sonar online)
- ChatGPT 在开发者工具圈认知更好
- Claude 在严肃 B2B / 学术场景回答更稳

只问一个 = 结论偏向那一家的偏好。AI Visibility Audit 的卖点就是**跨引擎视野**。

### 3.3 `STABILITY_RUNS=3` 是**现在最该收敛的**

3 次采样在统计上能压住 temperature 噪声,但真实 ROI:

| STABILITY_RUNS | 调用数 | 分数方差(经验) |
|---|---|---|
| 1 | 30 | ±5-8 分 |
| 2 | 60 | ±3-5 分 |
| 3 | 90 | ±2-3 分 |
| 5 | 150 | ±1-2 分 |

边际收益递减。**从 3 降到 1**:
- 成本:$1.08 → **$0.36**(-67%)
- 耗时:180s → **60s**
- 精度:±2 分 → ±5 分(**用户端感知不到差别**)

这是 `issue_list.md` 中 issue **#2**(P0)的核心优化方向。已在 `docs/性能处理方案.md §2` 落地。

---

## 4. 成本控制的 4 个收敛点

### 4.1 `STABILITY_RUNS` 3 → 1(P0,本来就在做)

- 省 67% 钱 + 67% 时间
- 精度掉 2 分,业务感知无

### 4.2 默认不开 Claude(节约)

Claude Sonnet 4 是 3 个引擎里**最贵的**(output $15/M vs GPT-4o-mini 的 $0.60/M)。

保留 Perplexity(实时搜索强) + ChatGPT(最普及),拿掉 Claude:
- engines 3 → 2
- 成本再 -45%(Claude 占 3 引擎里一半多开销)

### 4.3 结果缓存

同一 URL 24 小时内重跑,返回缓存:
- 成本 $0
- 需要 `_page_cache` 等价物 for AI visibility results(issue #8 迁 Redis 后顺便做)

### 4.4 `max_tokens=1024` 再降

现在 output 上限 1024,实际 AI 回答通常 300-800 词。降到 `max_tokens=600`:
- 截断风险(少数长回答被截)
- 省 20-30% output cost

**建议:不做。** 截断会导致 citation 提取漏掉后半段 URL,反而损伤数据质量。

---

## 5. 其他端点的调用数

### 5.1 `/entity`(~50-60 次)

和 `/visibility` 同构,但 query 集不同(8 维度评估:knowledge graph / recognition / category / sentiment / competitors / content gap / platform presence / answer stability)。

公式:`queries × engines × STABILITY_RUNS`
- queries 集 ≈ 6-8
- engines 通常 3
- STABILITY_RUNS = 3

典型 $0.8 / 次。优化手段和 `/visibility` 一样。

### 5.2 `/citation`(~5-7 次)

只用 **Perplexity** 一个引擎问 5-7 条 query,**不做 STABILITY**。

```
5-7 queries × 1 engine × 1 run = 5-7 calls
```

成本 $0.02-0.05 / 次,是最便宜的付费端点。不需要砍。

### 5.3 `/aeo` / `/compare` / `/crawl-test` / `/authority`(免费)

**不调 AI**。只抓目标站点 + Wikipedia / Wikidata / npm / PyPI / GitHub 等免费公开 API。

---

## 6. 监控建议

未来考虑加的观测(目前没做):

1. **OpenRouter 消费看板**:OpenRouter Dashboard 自带用量 / 费用曲线,建议每周导出一次 CSV 进 Grafana
2. **Per-request cost 日志**:在 `ai.py::_query_*` 返回 `usage` 字段(OpenRouter 的 response 里带 token count),在 `geo.timing` logger 追加 `cost=` 维度,每个请求能回溯花了多少钱
3. **月度预算告警**:超过月度 budget 70% 发邮件 / Slack

这些都是 P2,当前日均流量不值得先投。

---

## 7. 关键参考

| 主题 | 位置 |
|---|---|
| `ai_visibility` 调用构造逻辑 | `backend/geo_checker/modes/visibility.py` |
| `_query_perplexity/openai/anthropic` | `backend/geo_checker/ai.py` |
| 本文相关的收敛项 | `docs/issue_list.md` #2 |
| 执行方案 | `docs/性能处理方案.md §2` |
| OpenRouter 定价(动态) | https://openrouter.ai/models |
