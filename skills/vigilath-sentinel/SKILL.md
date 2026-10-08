---
name: vigilath-sentinel
description: Negative brand alerts from real social media scraping: posts by real people, with original post links and publish times, not "how AI answers reputation questions". Use when the user asks "今天有没有负面" ("any negative mentions today"), "舆情怎么样" ("how is sentiment"), "有人在骂我们吗" ("is anyone bashing us"), "风险高不高" ("is the risk high"), "有什么热点可以蹭" ("any trending topics to ride"), or "帮我改提示词" ("change my prompts"). Two tracks: a one-off quick check (nothing to create; just give a brand name) and daily health monitoring (subscription, with history and alerts). Today's first few items and the trending list are free; history, alerts, and prompt configuration are paid.
slug: vigilath-sentinel
version: 1.0.0
displayName: Negative brand alerts
summary: What real people said on social media, with original post links and times, not something AI made up
tags: [sentiment monitoring, brand monitoring, risk alerts]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Negative Brand Alerts

Scrapes **posts by real people on real social media**, classifies sentiment and grades risk, with original post links and publish times.

⚠️ This is **completely different** from "asking AI reputation questions and seeing how AI answers". We scrape content that real people actually posted. When users compare against other vendors' reports, it is worth pointing this out directly.

## Two tracks: tell them apart first

| | One-off quick check | Daily health monitoring |
|---|---|---|
| Needs setup first? | **No**, just give a brand name | Must be activated first (**this channel cannot activate it**; do it in the web app) |
| How long a run takes | About 1–5 minutes | Runs automatically every day |
| What it gives | Items captured in this round, risk grading, negative details | Today's overview + 7-day trend + historical lookback + negative alerts |
| When to use | "Take a look right now for anything negative" | "Watch it for me every day and alert me to negatives" |

**If the user just wants a quick look, run the one-off check**: do not make them activate anything first. Only bring up daily monitoring when they want "automatic daily watching /
alerts / looking far back in time".

## What it does

Scrapes **posts by real people on real social media**, not "asking AI reputation questions".

| What to view | Content | Which track |
|---|---|---|
| One-off quick check | Run one round on the spot: item count, risk distribution, negative details | One-off |
| Today's overview | Sentiment KPIs, 7-day trend, risk distribution, latest brief, top high-risk items | Daily |
| Negative details | Each item has **the original post link and publish time**; conclusions without a link are not stated | Both |
| Historical lookback | Which negatives appeared in a given period (can go far back) | Daily |
| Live trending list | Current hot topics, to find ones worth riding | Free |
| Prompts | Change keywords / aliases / brand name | Daily |

The free tier is today's first 5 items + the full trending list; history, alert notifications, and prompt configuration are paid.

## Ask it like this

- "帮我查一下 XX 现在有没有负面" ("Check whether XX has any negative mentions right now") (one-off, no activation needed)
- "今天有没有负面" ("Any negative mentions today?"), "舆情怎么样" ("How is sentiment?")
- "上个月有哪些负面" ("What negatives were there last month?")
- "今天有什么热点可以蹭" ("Any trending topics to ride today?")
- "把提示词加上 XX" ("Add XX to the prompts")

## When to use

- "查一下 XX 有没有负面" ("check whether XX has negatives"), "现在测一下舆情" ("check sentiment now") (→ one-off quick check)
- "今天有没有负面" ("any negatives today"), "舆情怎么样" ("how is sentiment"), "有人在骂我们吗" ("is anyone bashing us"), "风险高不高" ("is the risk high")
- "以前某段时间有哪些负面" ("what negatives were there during some past period") (can go far back, → daily monitoring)
- "今天有什么热点可以蹭" ("any trending topics to ride today")
- "帮我把提示词/关键词改成 ⋯⋯" ("change my prompts / keywords to ...")

**This is a separate business line**: do not upsell AI visibility features along the way. Buyers of sentiment monitoring and buyers of GEO are often two different departments, with different trigger phrases, data sources, and decision makers.

## Prerequisites

Authorization required: `python scripts/geo_client.py login`.

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py chat "帮我跑一次单次舆情快检,品牌是 XXX"   # Nothing needs to be created first
python scripts/geo_client.py chat "快检跑完了吗"                        # Ask again after about 1-5 minutes
python scripts/geo_client.py chat "今天舆情怎么样?有没有负面?"          # Only available with daily monitoring enabled
python scripts/geo_client.py chat "上个月有哪些负面"
python scripts/geo_client.py chat "今天有什么热点可以蹭"
python scripts/geo_client.py chat "把提示词加上 XXX"
```

The one-off quick check has two steps, **start + fetch results later**: after starting, tell the user "about 1–5 minutes" first; do not poll repeatedly in place.
If the same brand was checked within 24 hours, the previous result is returned directly.

## How to read the results

- **The average sentiment score ranges from −1 to +1** and **is not a percentage**: −1 very negative / 0 neutral / +1 very positive,
  with ±0.2 as the positive/negative boundary. It is also **a single report-level overall judgment** for this round,
  **not the arithmetic mean of per-post scores**: do not explain it that way.
  Read it as is: "−0.31, leaning negative". **Never** say "−31%":
  the user will take it to mean "31% of the content is negative", which is a different thing, and they cannot tell the difference.
  When the user asks "what scale is this score on", give them the two sentences above directly.
- Stance / intent / factuality judgments must be stated with the Chinese terms; do not read out English enum values.
- Give **the original post link and publish time** for every negative item. Do not state conclusions you cannot link to.
- What is shown is content **within the freshness window** (the last few days by default), not the full inventory: when the user asks "how many in total", explain this scope.
- When a sentiment monitoring task errors, do not show error details to the user; just say honestly "scraping for this account has no results for now".

- The **actual time range covered** by the one-off quick check's evidence is given in the response; state it accordingly and do not call it "the last 30 days".
  When the user finds this range does not match newer content they found elsewhere, **say honestly that this is what we captured in this round**;
  do not argue or promise "the latest will be here soon": the coverage is what it is.
- The one-off quick check **has no history and no alerts**; for those, use daily monitoring.

## Overlap with the "one-click full diagnosis" (clarify proactively)

**The eighth sub-audit of the full diagnosis is sentiment monitoring; it runs the same pipeline at no extra charge.**
If the user just ran a diagnosis and now wants a "sentiment quick check", first ask whether they want to see the one in the diagnosis:
do not make them pay twice for the same content.

The reverse also applies: if they just want a quick look for negatives, **do not** upsell "run a full diagnosis"; that is an 8–20 minute heavy job.

## Free tier and paywall

- **Free**: today's first 5 items + full trending list
- **Paywall**: historical lookback, alert notifications, prompt configuration → paid
- When the call limit is hit, the response says explicitly "try again in a few minutes / continue in the web app"; that is not a fault, so do not treat it as an error and retry

Configuring prompts actually triggers scraping, so it is a step that requires stopping to confirm.
