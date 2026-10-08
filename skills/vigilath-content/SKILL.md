---
name: vigilath-content
description: Write an article that AI is willing to cite, targeting the question where you are absent, and run it through an advertising-law lexicon and qualification check before anything else. Use when the user says "帮我写篇文章" ("write me an article"), "针对这个问题写点内容" ("write something for this question"), "怎么让 AI 引用我" ("how do I get AI to cite me"), or "写一篇能被 AI 引用的稿子" ("write a piece AI will cite"). The outline and first paragraph are free; the full text and images are paid. Actual external publishing is outside this skill.
slug: vigilath-content
version: 1.0.0
displayName: Write an article AI will cite
summary: Draft content for the questions where you are absent, with advertising-law and qualification checks before publishing
tags: [GEO, content generation, compliance, advertising law]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Write Articles AI Will Cite

Pick a question where AI answers do not mention you, and write an article that is **structurally the kind AI is willing to cite**.

**The differentiator is not "AI writes articles"**: the market is full of that. It is **running every draft through an advertising-law lexicon and qualification check**: superlatives, absolute claims, and efficacy claims without a license are caught before publishing.

## What it does

Writes an article for the question where you are absent, **structured so that AI is willing to cite it**.

- Generates drafts (asynchronously); the style can follow your existing articles
- **Runs an advertising-law lexicon and qualification check before publishing**: superlatives, absolute claims, and unlicensed efficacy claims are caught as soon as the draft is written
- Article management: list, view full text, view pending review, approve / reject (with a reason)
- The free tier is the outline + first paragraph + compliance pre-check result; **the full text and images are paid**

The differentiator is not "AI writes articles" (the market is full of that), but **catching compliance problems for you before anything goes out**.

## Ask it like this

- "针对「XX 哪家好」写一篇能被 AI 引用的文章" ("Write an article for 'Which XX is best' that AI will cite")
- "我这篇稿子有没有违反广告法的地方" ("Does my draft violate advertising law anywhere?")
- "看看待审的文章有哪些" ("Show me the articles pending review")

## When to use

- "帮我写篇文章" ("write me an article"), "针对这个问题写点内容" ("write something for this question")
- "怎么让 AI 引用我" ("how do I get AI to cite me"), "写一篇能被 AI 引用的稿子" ("write a piece AI will cite")

## Prerequisites

Authorization required: `python scripts/geo_client.py login`.

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py chat "针对「XXX 哪家好」这个问题写一篇能被 AI 引用的文章"
python scripts/geo_client.py chat "看看我的文章列表 / 这篇合规检查过了吗"
```

Drafting is **asynchronous**: after starting it, give an honest time estimate instead of waiting in place.

## Compliance check

- When the compliance check reports an error, **quote exactly which sentence triggered which rule** and suggest a fix; do not vaguely say "there is a small problem".
- **Never write qualifications, certifications, awards, or results guarantees on the user's behalf.** By default, none are written. If the user has real qualifications to include, have them provide the details and remind them that they must be able to prove these claims.
- When the user asks for phrases like "industry No. 1", "the best", or "100% effective", state directly that these trigger advertising law in China and offer compliant alternatives.

## Free tier and paywall

- **Free**: outline + first paragraph + compliance pre-check result
- **Paywall**: full text + images → paid

When you hit the paywall, state clearly what will be done, what it costs, and how much balance remains, then stop and wait for the user's reply.

## Balance and payment (read this when you hit a 402)

Actions such as publishing articles and placements are **paid** and draw from the account's compute-credit wallet.

```bash
python scripts/geo_client.py wallet          # Check balance: look before any paid action
python scripts/geo_client.py topup 100       # Top up 100 CNY: returns how to pay
python scripts/geo_client.py topup 100 --tx 0x…   # After paying on-chain, submit the transaction hash for reconciliation
```

**A `402` is not an error**; it means "payment required": the response includes how much more is needed, the current balance, and two top-up paths.

- **Have the user top up on the web** (WeChat QR code): give them the page address from `web` in the response
- **Pay yourself** (direct agent payment): pay using the network, asset, recipient address, and amount in `accepts`,
  then reconcile with `topup <amount> --tx <hash>` once you have the transaction hash

Three notes:

- **Read out the amount and recipient address exactly as given; do not change a single character or compute them yourself.** Money sent to the wrong address cannot be recovered.
- **Do not retry repeatedly when the balance is insufficient**: every attempt will fail and make the user think the system is broken. Stop and state how much needs to be topped up.
- **Do not decide the top-up amount for the user.** Report the amount this order needs and let them decide.

## Boundaries

**This skill does not do actual external publishing.** Generated articles stay as drafts; sending them out is a separate matter (see the distribution skill; the public API currently does not grant publishing permission). Do not promise "publishing on your behalf".
