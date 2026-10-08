---
name: vigilath-mention
description: Actually ask AI about your brand and paste back its exact words: 3 engines × 3 category questions customers would ask, showing whom AI recommends and whether you are included. **No login required**; 2 free runs per device per 30 days. Use when the user asks "AI 会不会推荐我" ("will AI recommend me"), "问 AI 我这行谁好会说到我吗" ("if someone asks AI who is good in my industry, will it mention me"), "AI 提不提我的品牌" ("does AI mention my brand"), or "客户问 AI 时我出现吗" ("do I show up when customers ask AI"). For the full multi-engine view use vigilath-report; to audit the website itself use vigilath-site-audit.
slug: vigilath-mention
version: 1.0.0
displayName: Will AI recommend you
summary: Ask AI about your brand and paste back its exact words to see whom it actually recommends
tags: [GEO, AI search, brand monitoring, competitor analysis, no login]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# AI Recommendation Check

**Actually ask AI engines** about the user's brand and paste back their exact words. The point is not a score but this sentence: **when a customer asks this way, whom does AI name?**

**No login required**, but every run really calls the engines, so there is a quota: 2 runs per device per 30 days (keyed on both device and egress IP).

## What it does

**Actually asks AI engines** about the brand and pastes back their exact words. Takes about 1–3 minutes.

- Asks 3 **category questions customers would ask** (such as "XX 推荐" ("XX recommendations"), "XX 哪个品牌好" ("which XX brand is best"), "如何挑选 XX" ("how to choose XX")),
  not self-check questions like "how is your company"
- Asks each question on 3 engines (DeepSeek / Doubao (豆包) / Yuanbao (元宝), etc., depending on which actually complete)
- Each result returns: **an excerpt of the engine's exact words**, whether you were mentioned, which sources the engine cited, and the collection time

**The most valuable output is often this sentence**: AI recommended three other companies, not you, with its exact words as proof.

No registration needed; 2 runs per device per 30 days.

## Ask it like this

- "客户问 AI 我们这行谁好,AI 会说到我们吗" ("When customers ask AI who is good in our industry, will AI mention us?")
- "AI 会不会推荐我们" ("Will AI recommend us?")
- "AI 提我们品牌的时候是怎么说的" ("What does AI say when it mentions our brand?")

## When to use

- "AI 会不会推荐我" ("will AI recommend me"), "客户问 AI 的时候我会出现吗" ("will I show up when customers ask AI")
- "AI 提不提我们品牌" ("does AI mention our brand"), "问 AI 我这行谁好,会说到我吗" ("if someone asks AI who is good in my industry, will it mention me")

**Do not use for**: auditing the website itself (use vigilath-site-audit, zero cost and unlimited), or the full multi-engine, multi-question view (use vigilath-report, authorization required).

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py mention 你的品牌名 商用清洁机器人
```

Brand name + at least one industry term. The industry term is key: the questions are **category questions** ("商用清洁机器人推荐" ("commercial cleaning robot recommendations"), "商用清洁机器人哪个品牌好" ("which commercial cleaning robot brand is best")), not "how is your brand". Answers to brand self-check questions always contain the brand name, so they cannot show whether the brand is recommended.

Runs for 1–3 minutes (the client polls on its own; you do not need to check repeatedly). Returns:

- `answers[]`: each question × each engine: `answer_excerpt` (**an excerpt of the engine's exact words**), `mentioned` (whether this brand was mentioned), `citations` (which sources the engine cited), `collected_at` (collection time)
- `mentioned_in` / `answered`: **counts**, not rates
- `engines_used`: the engines that actually completed this run

## How to read the results (where this skill most often goes wrong)

- **Read the engine's exact words to the user.** This is the one thing in the entire product line that cannot be fabricated, and it is what the user most needs to see. Reporting only a number throws away all the value.
- **Never convert to a percentage.** A sample of 3 questions does not support computing a rate. When you see `mentioned_in: 0, answered: 9`, say "you were not mentioned once in 9 answers"; **do not say "mention rate 0%"**, which implies a statistical measure that does not exist.
- **An engine marked `failed` did not complete.** Say honestly which one is missing; do not fill in the gap or substitute another engine's results.
- **If competitors are mentioned, say so directly.** What the user most needs to know is often "AI recommended three other companies, not you". Say it out loud; do not blur it to be polite.

## How quota is counted (the easiest thing to get wrong)

**Quota is deducted the moment a run starts**, not after it succeeds: cost is incurred as soon as the engine calls go out.
There are three cases around this, and **the three messages are completely different; do not mix them up**:

| Return | What happened | What to say |
|---|---|---|
| `status: "no_quota"` | The client **checked quota before starting**, found none → **nothing was started and nothing was consumed** | "The free quota is used up, so this run did not happen." **Do not** say "the run failed" |
| `status: "failed"` + `quota_refunded: true` | It ran, but **no engine answered** → the quota **has been refunded** | "This round produced no results; the quota has been refunded, so you can try again right away" |
| `status: "failed"` + `quota_refunded: false` | It ran and at least one engine answered, but the job was judged failed | "This round produced no results; try again in a while." Do not promise the quota will come back |

When the quota limit is hit, the response states whether the **device** quota or the **egress IP** quota was reached
(on a company network with a shared IP, a colleague may have used it first). **Relay this honestly and do not retry repeatedly.**

## Division of labor with the "full diagnostic report"

Users often think these two features overlap. In one sentence each:

- **This skill** = no login, 3 engines × 3 category questions, gives **the engines' exact words**, 1–3 minutes.
  "Get one piece of evidence right now."
- **vigilath-report** = authorization required, eight sub-audits + five-dimension scoring + eleven chapters + PDF export, 8–20 minutes.
  "Produce something you can send to your boss."

If you want more:

- The full view (more engines × more questions + competitor matrix + eleven-chapter report) → vigilath-report, which requires registration and authorization
- Just want to audit the website itself repeatedly → vigilath-site-audit, zero cost and unlimited
