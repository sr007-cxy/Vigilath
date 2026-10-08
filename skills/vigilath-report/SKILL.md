---
name: vigilath-report
description: An AI visibility diagnostic report you can send to your boss: eight sub-audits, five-dimension scoring, eleven chapters, PDF export. Actually asks DeepSeek / Doubao (豆包) / Qwen (通义) / ERNIE (文心) / Yuanbao (元宝) about the user's brand and pastes back the engines' exact words. Authorization required; a full job takes 8-20 minutes. Use when the user asks "AI 到底提不提我" ("does AI actually mention me"), "AI 搜索里能搜到我吗" ("can I be found in AI search"), "给我出一份诊断报告" ("give me a diagnostic report"), "我的 AI 可见性怎么样" ("how is my AI visibility"), or "为什么 AI 不推荐我" ("why doesn't AI recommend me"). For a quick look at the website itself, use vigilath-site-audit.
slug: vigilath-report
version: 1.0.0
displayName: Brand AI search health report
summary: A report you can send straight to your boss: eight audits, five-dimension scoring, eleven chapters, PDF export
tags: [GEO, AI search, diagnostic report, competitor analysis]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# AI Health Report (one-click full diagnosis)

**Actually ask AI engines** about the user's brand, paste back their exact words and collection times, and synthesize a five-dimension score and an eleven-chapter report.

Eight sub-audits: website health check / answer engine optimization / crawler accessibility / off-site authority / measured AI visibility / brand entity recognition / competitor intelligence / sentiment monitoring.

## What it does

A diagnostic report you can send straight to your boss, **eleven chapters + PDF export**; a full job takes 8–20 minutes.

Eight sub-audits: website health check · answer engine optimization · crawler accessibility · off-site authority ·
measured AI visibility · brand entity recognition · competitor intelligence · sentiment monitoring.

The report includes: five-dimension scoring (with **formulas and inputs**), per-question hit status and ranking,
competitor comparison, user personas and scenario intent, prioritized actions and a roadmap.

**Every number drills down to the engine's exact words and collection time**: when the user questions a figure, give them the exact words.

Each account gets **one free full report for life**; after that, monthly quotas follow the subscription tier.

## Ask it like this

- "给我出一份完整的 AI 可见性诊断报告" ("Give me a full AI visibility diagnostic report")
- "我要一份能发给老板的东西" ("I need something I can send to my boss")
- "为什么 AI 推荐竞品不推荐我" ("Why does AI recommend competitors and not me?")

## When to use

- "AI 到底提不提我" ("does AI actually mention me"), "AI 搜索里能不能搜到我" ("can I be found in AI search"), "为什么 AI 推荐竞品不推荐我" ("why does AI recommend competitors and not me")
- "给我出一份诊断报告" ("give me a diagnostic report"), "有没有能发给老板的东西" ("is there something I can send to my boss")

**Do not use for**: only auditing the website (use vigilath-site-audit, 30 seconds), or only traditional SEO (use vigilath-seo-audit).

## Prerequisites

Authorization required: `python scripts/geo_client.py login` (the terminal shows a pairing code; the user approves it in the browser).

**No topic needs to be created first and no official website is required**: the user can run it with just a brand name; without a website, the four site-side audits are skipped automatically and the four AI-side audits run as usual. Do not refuse or push the user to build a site just because information is missing.

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

Start (natural language; the platform agent decides the parameters):

```bash
python scripts/geo_client.py chat "帮我跑一次全面诊断,品牌叫 XXX,行业词是 XXX"
```

Check progress or fetch results:

```bash
python scripts/geo_client.py data diagnosis
```

## How to give the user the full report

The response contains `report_url`: **the full, clickable report address**. When the user asks "where can I see the full report / send me a copy":

1. Give `report_url` as a Markdown link; **do not** just say "it's on the diagnosis page" and leave them to assemble the address in the browser
   (user feedback explicitly said "having to type the URL into the browser myself is inconvenient");
2. Explain that it only opens when **logged in with the same account**: it is not a public share link;
3. For a version to forward to others, export a PDF from the report page.

`data diagnosis` returns a **summary** (five-dimension score + executive summary + key findings + quick-win actions);
the chapter-by-chapter details are on the report page. Do not read out the summary and say "that's the whole report".

## How long it takes

- **A full job takes 8–20 minutes.** After starting it, **tell the user honestly how long to wait and let them get on with other things**, then they can come back and ask "is the report ready?".
- **Never check progress repeatedly in the same turn.** Start → give the time estimate → stop. Run `data diagnosis` again the next time the user asks.
- Do not say things like "almost done" or "just a moment" that leave the user waiting around.

## Data sources

- **Every number drills down to the engine's exact words and collection time**: this is the fundamental difference between this report and similar products; when the user questions something, give them the exact words.
- If an engine did not complete, say honestly "N engines were measured this time"; **do not fill the gap with estimates**.
- Questions marked "untested" in the report were never tested, which **is not the same as a miss**; never conflate the three states.

## Free tier and paywall

**Each account gets one free full report (eleven chapters + PDF) for life.** After it is used, monthly quotas follow the subscription tier.

The user must know this before starting: this is a paid / irreversible action and requires a pause for confirmation. Say clearly "This will use your free diagnostic report and take about 8–20 minutes. Run it now?" and wait for their reply before starting.
