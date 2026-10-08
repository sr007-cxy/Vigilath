---
name: vigilath-weekly
description: Tells you every week whether you went up or down in AI search: six fixed metrics + period-over-period change + target attainment, available as daily, weekly, or monthly reports. Use when the user asks "这周怎么样" ("how was this week"), "数据涨了吗" ("did the numbers go up"), "最近表现如何" ("how have we been doing lately"), "露出率多少" ("what is our exposure rate"), "达标了吗" ("did we hit the target"), or "上个月的报告" ("last month's report"). Authorization required, and the account must already be running monitoring.
slug: vigilath-weekly
version: 1.0.0
displayName: AI search weekly report
summary: Tells you every week whether you went up or down in AI, with six metrics at a glance
tags: [GEO, AI search, data reports, monitoring]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# AI Visibility Weekly Report

Fetches ready-made data from the account's report center: six metrics, period-over-period change, and target attainment for each daily / weekly / monthly period, plus per-platform performance.

## What it does

Fetches ready-made data from the report center to answer "did it go up or down".

| What to view | Content |
|---|---|
| Six metrics | Overall exposure rate · TOP1 exposure rate · TOP3 exposure rate · content neutral-positive rate · article citation rate · brand share of voice |
| Change and attainment | Whether it rose or fell versus the previous period, and whether this period hit the target |
| Per platform | Overall exposure rate per engine |
| Trend | The most recent periods viewed together |
| Period summary | The AI-written summary text for that period |
| Scheduled audit status | Whether the latest run finished and which platform errored |

These are **fetched directly, without going through an LLM**, so they are fast and cheap.

## Ask it like this

- "这周怎么样,涨了还是跌了" ("How was this week, up or down?")
- "达标了吗" ("Did we hit the target?")
- "上个月的报告说了什么" ("What did last month's report say?")
- "最近几期的走势" ("What's the trend over the last few periods?")

## When to use

- "这周怎么样" ("how was this week"), "涨了还是跌了" ("up or down"), "最近表现如何" ("how have we been doing lately")
- "露出率多少" ("what is our exposure rate"), "达标了吗" ("did we hit the target")
- "上个月的报告说了什么" ("what did last month's report say")

## Prerequisites

Authorization required: `python scripts/geo_client.py login`, and the account must **already be running monitoring** (no monitoring means no report data).

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py data overview    # Latest daily/weekly/monthly period: six metrics + change + attainment
python scripts/geo_client.py data period      # Details for one period + that period's AI summary text
python scripts/geo_client.py data periods     # The most recent periods, to see the trend
python scripts/geo_client.py data run         # Status of the latest scheduled audit (finished or not / how many items / which platform errored)
```

These are **fetched directly, without going through an LLM**, so they are fast and cheap.

## Metric names and definitions (stating a wrong number is worse than saying nothing)

**Copy the six metric names exactly from `metric_labels` in the response, without changing a single character**: "整体露出率" (overall exposure rate), "TOP1露出率" (TOP1 exposure rate), "TOP3露出率" (TOP3 exposure rate), "内容中正率" (content neutral-positive rate), "文章引用率" (article citation rate), "品牌声量度" (brand share of voice).

Do not invent terms such as "hit rate", "impression rate", or "coverage rate" to refer to them.

- **Only read out numbers returned by the API.** Never convert them yourself, and never add or divide numbers from different commands. Percentages are already final values (0–100, already rounded); **do not multiply by 100 again**.
- **Use the most recent fully tallied period.** The latest daily report is **yesterday**; today's is still running. When the user asks "how is today", first give the fully tallied numbers, then add today's in-progress data, **marked as still being tallied and for reference only**; never draw conclusions from it.
- **"Did we hit the target" is always answered from the weekly report**; do not answer it with daily report numbers.
- `data coverage` (whether a question has ever been found, deduplicated by question) and the overall exposure rate (by number of answers) are **two different measures**; never mix them or convert one into the other.

## Free tier and paywall

The first report is free; **from the second period on, it is subscription content**. When the user has no monitoring running, say honestly "there is no data yet" and explain that continuous monitoring is needed before weekly reports exist; do not substitute audit data.
