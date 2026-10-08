---
name: vigilath-radar
description: How your customers will ask AI, and on which questions you simply do not exist: expands about 40 real questions in one go and flags the ones where you are completely absent. Use when the user asks "客户会怎么搜我" ("how will customers search for me"), "用户会问 AI 什么" ("what will users ask AI"), "我该盯哪些问题" ("which questions should I track"), "哪些提问我没覆盖" ("which questions am I not covering"), or "帮我找关键词" ("find keywords for me"). Viewing candidates is free; turning them into ongoing tracking is paid.
slug: vigilath-radar
version: 1.0.0
displayName: How customers ask AI
summary: Lists 40 questions customers really ask in one go and flags the ones where you are completely absent
tags: [GEO, AI search, keywords, keyword selection]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Customer Question List

Expand the user's brand/industry into **the things customers actually say to AI** (not search keywords, but complete questions), and flag the questions where the user does not exist at all.

## What it does

Expands a brand/industry into **the things customers actually say to AI**, and flags the questions where you do not exist.

- About 40 candidates per run, grouped into four scenario types: search / Q&A / intent / brand
- Each one is marked with one of three states: **hit** (tested, AI mentioned you), **miss** (tested, not mentioned), **untested** (never tested at all)
- Seeing the gaps is free; turning selected questions into **ongoing monitoring** is paid

**Never conflate** the three states: describing "untested" as "miss" fabricates a gap for the user out of thin air.

## Ask it like this

- "客户会拿什么问题问 AI" ("What questions will customers ask AI?")
- "我该盯哪些问题" ("Which questions should I track?")
- "哪些提问我完全没覆盖" ("Which questions am I not covering at all?")

## When to use

- "客户会怎么问 AI" ("how will customers ask AI"), "用户会拿什么问题搜我这行" ("what questions will users search about my industry")
- "我该盯哪些问题" ("which questions should I track"), "哪些提问我没覆盖到" ("which questions am I not covering")
- "帮我找关键词 / 选词" ("find / pick keywords for me")

## Prerequisites

Authorization required: `python scripts/geo_client.py login`.

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py chat "帮我扩一批客户会问 AI 的问题,行业是 XXX"
python scripts/geo_client.py data coverage    # Cumulative number of hit questions / core keywords
```

Expansion covers four scenario types (search / Q&A / intent / brand). The platform cap is
**4 scenarios × 50 = 200 items**; the actual count is `total` in the response: when the topic has no official website the brand scenario is skipped,
and items are deduplicated across scenarios, so it is usually somewhat under 200. **Read out `total`; do not recite a fixed number.**

**These are only candidates; nothing is saved yet and nothing is charged.**

## The difference between the three states

Each question has one of three states, which **must be described separately and never merged**:

- **Hit**: tested, and the brand appeared in the AI answer
- **Miss**: tested, and the brand did not appear in the AI answer ← this is the real gap
- **Untested**: **never tested at all**

Describing "untested" as "miss" fabricates a gap for the user out of thin air. Other products blur these two together; we must not.

## Free tier and paywall

- **Free**: full preview of candidates + gap markers, view as much as you like
- **Paywall**: **adding selected questions to ongoing monitoring for continuous tracking** (entering scheduled monitoring audits) is paid

The dividing line is easy to explain to users: **seeing the gaps costs nothing; having us keep watch for you costs money.**

Before adding anything to ongoing monitoring you must stop and confirm, stating clearly that this step starts billing. If the user only wants to take a look, stop after the preview and do not add anything to monitoring on your own initiative.

## When the user wants to put it into practice

**This channel cannot save anything**: setting seed keywords and adding questions to ongoing monitoring are only available in the platform's own web app
(this is not "you haven't paid"; it is "the person talking through the skill channel is not necessarily the account owner"). The tool returns `blocked`.
When this happens, **do not leave the user stuck and do not be vague**; offer these two options:

- Fill it in themselves on the **topic settings page** of the Vigilath web app: name the page only, and **do not describe what the buttons look like or where they are**;
  you have never seen that page;
- Or say the same thing to the GEO assistant **in the Vigilath web app**: the assistant there can save it directly for them,
  and will first show a confirmation card stating how many credits one round costs.

**Never** say "I'll fill it in for you" and then get blocked, and **never** invent nonexistent steps for them to click through.

## What happens after saving (say this proactively)

This is the part most likely to trip users up, because it is all implicit:

- **It does not start running immediately.** After saving, it waits for the platform's next cycle anchor: if submitted in the morning, there is usually no activity that day.
  Without this warning, they will watch the page waiting for a result that will not come that day.
- **Credits are deducted per round**; one round = number of prompts × number of engines ("cells"), settled the next day.
- **A round takes quite a while**, and if an engine fails in one round, it will be retried in the next round at the earliest.
- **To stop**, turn off scheduled audits on the topic settings page; neither you nor the user can change the scheduling frequency.
