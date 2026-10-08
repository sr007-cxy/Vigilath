---
name: vigilath-geo
description: Audit, query, and optimize a brand's visibility and citation rate in AI search engines (ChatGPT / Perplexity / DeepSeek / Doubao (豆包) / Qwen (通义) / ERNIE (文心) / Yuanbao (元宝)) (GEO/AEO), and check brand sentiment monitoring and trending topics. **GEO / SEO health checks for any website require no login, are free, and are unlimited**: when the user says "测一下这个网站" ("test this website"), "这个站 GEO 怎么样" ("how is this site's GEO"), "SEO 分数多少" ("what is the SEO score"), or "帮我体检一下官网" ("give my website a health check"), run check directly. Other capabilities require authorization: use when the user asks about "在 AI 里被搜到几个" ("how many questions find us in AI"), "AI 搜索可见性" ("AI search visibility"), "GEO / AEO", "竞品在 AI 里怎么对比" ("how competitors compare in AI"), "怎么提升被 AI 引用" ("how to get cited more by AI"), "投放效果" ("placement performance"), "露出率" ("exposure rate"), "日报周报月报" ("daily/weekly/monthly reports"), or questions like "今天有没有负面" ("any negatives today"), "舆情" ("sentiment monitoring"), "品牌声量" ("brand share of voice"), "有什么热点可蹭" ("any trending topics to ride"), "调提示词" ("adjust prompts").
slug: vigilath-geo
version: 1.0.0
displayName: Vigilath · All-in-one AI search optimization pack
summary: Audit, diagnose, optimize, and monitor in one place; three capabilities need no registration and work right after install
tags: [GEO, AI search, SEO, sentiment monitoring, content generation]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Vigilath GEO Optimization

Connects this host agent to Vigilath to audit, review, optimize, and monitor a brand's visibility in AI search engines.

Two capability tiers, with completely different requirements:

- **No-login tier**: GEO + SEO health checks for any website (`check`), free, unlimited, no credentials needed. **Use this tier first**:
  users get a real score right away even without a Vigilath account.
- **Authorized tier**: a GEO agent bound to an account (a "little crayfish") that sees the account's real placement data, runs the one-click full diagnosis,
  checks sentiment monitoring, and drafts content. Requires device-code authorization done when the skill was installed.

## You only need to talk

**The `python scripts/…` commands below are run by the AI assistant itself; you do not need to type them.**
Once installed, just talk to it in plain language:

- "帮我测一下 example.com,AI 能不能读懂这个网站" ("Test example.com: can AI understand this website?")
- "客户问 AI 我们这行谁好,AI 会说到我们吗" ("When customers ask AI who is good in our industry, will AI mention us?")
- "这周数据怎么样,涨了还是跌了" ("How were this week's numbers, up or down?")
- "今天有没有负面" ("Any negative mentions today?")
- "给我出一份能发给老板的诊断报告" ("Give me a diagnostic report I can send to my boss")

The assistant decides which capability to call, whether authorization or a top-up is needed first, and then explains the results to you.
The commands and parameters later in this document are written for the assistant; you can skip them.

## Skills included in this bundle

This is a **bundle**: it has every capability of the eight skills below, so installing this one is enough; you do not need the individual ones.

| # | Skill | What it solves | Main command | Login required |
|---|---|---|---|---|
| 1 | **Website AI-friendliness audit** | Give a URL and learn whether AI crawlers can understand it: 25 categories, item by item | `check <url> --no-seo` | No |
| 2 | **Search engine optimization audit** | The 12 traditional search engine items you haven't got right yet | `check <url> --seo-only` | No |
| 3 | **Will AI recommend you** | Ask AI about the brand and paste back **the engines' exact words** | `mention <brand> <industry-term>` | No |
| 4 | **Brand AI search health report** | A report you can send to your boss: eight sub-audits · five-dimension scoring · eleven chapters · PDF export | `chat "跑一次全面诊断"` | Yes |
| 5 | **How customers ask AI** | How customers will ask AI, and on which questions you do not exist | `chat "帮我扩一批客户会问的问题"` | Yes |
| 6 | **Write an article AI will cite** | Write articles for the questions where you are absent, **checked against advertising law first** | `chat "针对…写一篇能被引用的文章"` | Yes |
| 7 | **AI search weekly report** | Up or down each week: six metrics + period-over-period change + target attainment | `data overview` / `data periods` | Yes |
| 8 | **Negative brand alerts** | Negative alerts from real social media scraping, with original post links and times | `chat "今天有没有负面"` | Yes |

Plus **eight advanced audits** (see the next section) and the **industry leaderboard** (`industry`).

**The no-login parts**: items 1, 2, and 3, plus the AEO audit and AI crawler test among the advanced audits,
and the industry leaderboard: usable right after install without any authorization. Everything else requires running `login` once.

## What it does

Everything below is genuinely achievable, not marketing copy. The **no-login** items work right after install; the rest require running `login` once to authorize.

### Audits (no login, free)

| Capability | Input | Output |
|---|---|---|
| Website AI-friendliness health check | A URL | Per-item scores across 25 categories + a 0–100 score and grade, plus which items are not right |
| Traditional SEO health check | A URL | 12-category SEO scores + an independent score (**computed separately** from the GEO score) |
| AI recommendation check | Brand + industry term | **The engines' exact words** for 3 engines × 3 category questions: whom AI recommends and whether you are included |
| Industry leaderboard | Industry name | Who is TOP1 in AI for that industry, concentration, trend (only for industries already in the database) |
| AEO visibility audit | A URL | How well the page content fits "AI-generated answers", per-page scores, weakest signals, and priority improvements |
| AI crawler test | A URL | Whether mainstream AI crawlers can actually fetch the page, and Common Crawl indexing status |

The four items above are **zero cost and unlimited**. The items below are also **free and need no registration**, but every run really queries AI engines,
so they are rate-limited per device + egress IP (returns 429 when used up; relay this honestly and do not retry):

| Capability | Input | Output |
|---|---|---|
| Authority signal audit | A URL | Off-site reviews, mentions, and third-party trust signals |
| AI citation check | A URL | Whether AI engines cite this site when answering brand-related questions |
| Entity GEO audit | Brand/product/person name | **No URL needed**: how well AI knows this entity |
| Multi-site comparison | 2–5 URLs | Side-by-side GEO category scores: who is stronger and weaker |
| AI visibility audit | URL (optionally a brand name / custom questions) | Asks a batch of questions across several mainstream engines: a more complete version of `mention` |
| Competitor intelligence | URL (competitor domains optional) | Extracts from AI answers who is being recommended, source preferences, and the competitive landscape |

### All audit capabilities at a glance (the nine cards on the website, all callable here)

On the left is the card name on the website (users will ask using these names); on the right is what it actually gives you:

| Website card | In plain words | How to call | Login required | Cost |
|---|---|---|---|---|
| **AEO visibility audit** | Whether your pages are suitable for AI to summarize as answers: per-page scores, pointing out the weakest signals | `advanced aeo <url>` | No | **Free, unlimited** |
| **AI crawler test** | Whether mainstream AI crawlers can actually fetch your pages when they visit | `advanced crawl-test <url>` | No | **Free, unlimited** |
| **AI citation check** | Whether AI will cite your website as a source when customers ask related questions | `advanced citation <url>` | No | Free · rate-limited |
| **Authority signal audit** | What others say about you: third-party reviews, mentions, credibility signals | `advanced authority <url>` | No | Free · rate-limited |
| **AI visibility audit** | Asks a batch of questions across several AI engines to see how often you are mentioned: the extended version of `mention` | `advanced visibility <url>` | No | Free · rate-limited |
| **Entity GEO audit** | **Whether AI knows your brand/product/person**: no URL needed | `advanced entity <name>` | No | Free · rate-limited |
| **Competitive comparison audit** | Compare yourself side by side with rivals and see where you fall behind | `advanced compare <url1> <url2>` | No | Free · rate-limited |
| **One-click full diagnosis** | Runs all eight audits and produces an eleven-chapter report with PDF export, the kind you send to your boss | `chat "跑一次全面诊断"` | Yes | 1 free for life |
| **Sentiment monitoring** | What real people said about you on social media, with original post links | `chat "今天有没有负面"` | Yes | First 5 items free |

Beyond the website cards there is one more: **competitor intelligence** (`advanced competitive-intel <url>`, free, rate-limited):
it digs out from AI answers who is being recommended, which sources AI likes to cite, and what the competitive landscape looks like.

Plus the three basic ones: **website AI-friendliness audit** and **search engine optimization audit** (`check <url>`, free, unlimited),
and **will AI recommend you** (`mention`, 2 runs per device per 30 days).

> **What "has a quota" means**: **audits are never charged**. But each run of these items really queries AI engines,
> which has real cost, so each device is limited to a few runs (2–3 per 30 days) to prevent abuse as a free API.
> You will be told clearly when it is used up; registered accounts get a higher quota. **You never need to pay for audits.**

### Diagnosis and data (authorization required)

| Capability | Output |
|---|---|
| One-click full diagnosis | Eight sub-audits · five-dimension scoring · eleven-chapter report · PDF export (8–20 minutes per job) |
| Report center | Six metrics for daily / weekly / monthly reports + period-over-period change + target attainment + that period's AI summary |
| Trend | Metric comparison across recent periods: up or down |
| Cumulative coverage | How many questions and core keywords have ever found you in AI |
| Hit list | Which questions **have been hit** (with engines and counts), and which **were placed but not yet hit** |
| Scheduled audit status | Whether the latest run finished, how many items ran, which platform errored |
| Root-cause diagnosis | Attribution and narrative for why you are not appearing |

### Keyword selection and monitoring (authorization required)

| Capability | Output |
|---|---|
| Create topic | Create a new brand topic (**currently limited to 1 per account**; rejected if one exists) |
| View topic | The current brand topic, plus counts and lists of core keywords and expanded questions |
| Expand keywords | Four-dimensional scenario expansion (search / Q&A / intent / brand) for a core keyword, giving candidates. **Candidates only: nothing is saved and nothing is charged** |
| Set core keywords / add to ongoing monitoring | **This channel cannot do it** (see "Capability boundaries"): the response will include `blocked`; relay this honestly and point to the web app |

> How many items one expansion gives: the platform cap is **4 scenarios × 50 = 200 items**. `total` in the response is the actual count:
> when the topic has no official website the brand scenario is skipped, and items are deduplicated across scenarios, so it is usually somewhat under 200.
> **Read out `total`; do not say "expands 20 at a time"**: that was the old default and has since been aligned with the web app.

### Content and publishing (authorization required)

| Capability | Output |
|---|---|
| Generate articles | Produce drafts for specified questions (asynchronously), **checked against the advertising-law lexicon and qualification rules before publishing** |
| Article management | List articles / view full text / view pending review / approve / reject (with a reason) |
| Publishing plan | Confirm template → trigger generation |
| Publishing progress | Published list, published today |
| Actual external publishing | Controlled by platform guardrails and compliance gates; included in subscriptions |

### Sentiment monitoring (authorization required, separate business line)

| Capability | Output |
|---|---|
| Today's sentiment | KPIs · 7-day trend · risk distribution · latest brief · top high-risk items |
| Historical lookback | Which negatives appeared in a given period |
| Live trending list | Current hot topics, to find ones worth riding |
| Prompts | Change keywords / aliases / brand name |

> **The average sentiment score ranges from −1 to +1** (−1 very negative / 0 neutral / +1 very positive, ±0.2 is the positive/negative boundary),
> and **is not a percentage**; it is also a single report-level overall judgment for this round, **not the arithmetic mean of per-post scores**.
> Read it as is ("−0.31, leaning negative"); **never** say "−31%":
> the user will take it to mean "31% of the content is negative", which is not what it means at all.
>
> **The full diagnosis already includes a sentiment monitoring chapter at no extra charge.** If the user just ran a diagnosis and now wants a "sentiment quick check",
> first ask whether they want to see the one in the diagnosis; do not make them pay twice for the same content.

### Optimization tasks and account

| Capability | Output |
|---|---|
| Optimization task center | A to-do list compiled from daily report suggestions / regression retests / insight suggestions / placement suggestions |
| Handle tasks | Convert, execute, or dismiss an item |
| Knowledge base | Search materials you uploaded yourself and answer from them |
| Account | Check balance, top up (web QR code or direct agent payment), see what the current credentials can do |

**Be clear about what it cannot do too**: engine selection, query scheduling, and frequency are fixed by the platform; neither you nor the user can specify them;
each account is currently limited to 1 brand topic; actual external publishing is controlled by platform guardrails and does not happen on demand.

One more capability **is not usable here**: the platform agent can "operate the current page for the user" (fill forms / switch topics / navigate),
but that requires a web page context. A skill installed in a terminal has no page to operate, so do not promise this to the user.

### What the homepage claims map to here

Users often ask using the wording on the official homepage; do not get caught out:

| What the homepage says | What it maps to here |
|---|---|
| **Brand profile + one-click AI auto-fill** | When creating a topic, the official website is fetched automatically and a brand profile is extracted: `chat "帮我建主题,官网是…"` |
| **Core keywords → prompt expansion** | `chat "帮我扩一批客户会问的问题"`, with candidates for each of the four scenario types |
| **Content draft generation and review** | `chat "写一篇…"` to generate → list / view full text / approve / reject |
| **Covers 6 major AI platforms** | Measurements run on Chinese engines (DeepSeek / Doubao (豆包) / Qwen (通义) / ERNIE (文心) / Yuanbao (元宝), etc.). **Which ones exactly is given by `engines_used` in each response**; do not quote a fixed number |
| **8-stage automated content seeding** | Core keywords → intent expansion → clustering → generation → distribution → engine crawling → data collection → review. The first four steps are callable here; distribution and review are controlled by platform guardrails and payment |
| **Super list rankings / review reputation / PR distribution** | These are **service deliverables**, not self-service features in the skill: when users ask, explain that they are service deliverables requiring contact with the team and are not among the self-service features |

## When to use

Invoke this skill when the user (or an upstream task) involves any of the following:

**Website health check (no login; consider this first)**

- "**测一下**这个网站" ("**test** this website"), "这个站 **GEO/SEO 怎么样**" ("**how is** this site's **GEO/SEO**"), "帮我**体检**一下官网" ("give my website a **health check**")
- "我的网站对 AI 友好吗" ("is my website AI-friendly"), "为什么 AI 抓不到我的站" ("why can't AI crawl my site")
- The user gives a URL and wants to know whether it is any good: **do not ask for an account; run `check` directly**

**GEO visibility**

- "我的品牌/产品在 AI 搜索里**被搜到几个**问题?" ("for **how many** questions is my brand/product found in AI search?"), "AI 可见性怎么样?" ("how is my AI visibility?")
- "帮我**诊断** GEO/AEO 现状" ("**diagnose** my current GEO/AEO status"), "为什么 AI 不引用我?" ("why doesn't AI cite me?")
- "**竞品**在 AI 引擎里的表现对比" ("compare **competitors'** performance in AI engines")
- "今天/累计**投放效果**、命中率" ("today's/cumulative **placement performance**, hit rate"), "**日报/周报/月报**怎么样" ("how is the **daily/weekly/monthly report**"), "**达标**了吗" ("did we **hit the target**")
- "怎么**优化/产稿**提升被 AI 引用率" ("how to **optimize / produce content** to raise the AI citation rate")

**Sentiment monitoring** (same token, same API)

- "今天**舆情**怎么样 / 有没有**负面** / 风险高不高" ("how is **sentiment** today / any **negatives** / is the risk high")
- "**以前/某段时间**有哪些负面" ("what negatives were there **before / during some period**") (can go far back)
- "今天有什么**热点/热榜**可以蹭" ("any **trending topics / hot lists** to ride today")
- "帮我把**提示词/关键词**改成 ⋯⋯" ("change my **prompts / keywords** to ...")

Do not invoke for ordinary questions unrelated to the two categories above.

## How to use

The script is `scripts/geo_client.py`: pure standard library, zero dependencies.

- **Website health check** (no login, free, unlimited; runs all 25 GEO + 12 SEO categories for the whole site, ten-odd seconds to a minute):
  ```bash
  python scripts/geo_client.py check https://example.com
  python scripts/geo_client.py check https://example.com --no-seo   # GEO only
  ```
  **The URL must come only from the user's own words.** The client strips quotes, Markdown wrapping, and trailing punctuation, and adds a missing scheme,
  but **never replaces the domain, never adds www., and never guesses**. After the run starts, read `url_checked` (the address actually tested) from the response
  to the user to verify; if one message contains several URLs, or you are unsure which one is meant, **ask first instead of picking one yourself**.

  Returns two halves, `geo` / `seo`, each with a 0–100 score and grade. **The scores come from a full run and are real.**
  When reading results, **by default cover only the score + the items in `top_issues`**, explaining for each "what getting this wrong causes and how to fix it";
  **do not list check item names one by one**: terms like "llms.txt", "structured data", and "AI crawl readiness" mean nothing to the user,
  and reading out twenty-odd of them in one go just makes them stop reading. If they want all the details, expand `visible_categories`.
  Items in `locked_categories` **give only a category name, no finding**, and no fix suggestions: **never guess whether they are good or bad**;
  for full details, follow `note` to guide the user to register and then `login`.

- **Will AI recommend you** (no login, 2 runs per device per 30 days): **actually asks AI** about the brand and pastes back its exact words.
  ```bash
  python scripts/geo_client.py mention 你的品牌名 商用清洁机器人
  python scripts/geo_client.py mention-status <task_id>    # Check again after a timeout
  ```
  The questions are **category questions** ("XX 推荐" ("XX recommendations"), "XX 哪个品牌好" ("which XX brand is best")), not "how is your company":
  answers to brand self-check questions always contain the brand name, so they cannot show whether the brand is recommended. Takes about 1–3 minutes.
  **Read the engine's exact words from `answer_excerpt` to the user**; that is the most valuable output.
  `mentioned_in`/`answered` are counts; **do not convert them to percentages**: a sample of 3 questions does not support computing a rate.

  Two quota rules; do not get them wrong:
  - The client **checks the quota once before starting** (check only, no deduction). When there is no quota it returns `status: "no_quota"`,
    and that attempt **consumed nothing**: say honestly that the quota is used up, do not retry, and do not call it "the run failed".
  - If, once running, **no engine answered**, the quota is **refunded** (`quota_refunded: true` in the response),
    and the user can try again right away. **Do not say "the quota has been counted" anymore**: that was the old behavior.

  **Division of labor with the "one-click full diagnosis"** (users often think the two overlap; explain clearly):
  Quick check = no login, 3 engines × 3 category questions, gives **the engines' exact words**, 1–3 minutes;
  Full diagnosis = authorization required, eight sub-audits + five-dimension scoring + eleven-chapter report + PDF export, 8–20 minutes.
  One is "get a piece of evidence now", the other is "produce something you can send to your boss".

- **Advanced audits** (no login; the first two are zero cost and unlimited, the rest are free but rate-limited):
  ```bash
  python scripts/geo_client.py advanced aeo https://example.com          # AEO visibility audit
  python scripts/geo_client.py advanced crawl-test https://example.com   # AI crawler test
  python scripts/geo_client.py advanced authority https://example.com    # Authority signals (free, rate-limited)
  python scripts/geo_client.py advanced citation https://example.com     # AI citation check (free, rate-limited)
  python scripts/geo_client.py advanced entity 某某品牌 brand             # Entity audit, no URL needed (free, rate-limited)
  python scripts/geo_client.py advanced compare https://a.com https://b.com   # Multi-site comparison (free, rate-limited)
  python scripts/geo_client.py advanced visibility https://example.com   # AI visibility audit (free, rate-limited)
  python scripts/geo_client.py advanced competitive-intel https://example.com  # Competitor intelligence (free, rate-limited)
  ```
  `note` in the response states whether the item is zero cost or has a quota, and `quota` gives the remaining runs.
  **When the quota is used up it returns 429; relay this honestly and stop.** Audits are not charged; rate limits exist only to prevent abuse; registered accounts get a higher quota.

- **Industry leaderboard** (also no login, free): see who is TOP1 in AI search for an industry and how concentrated it is.
  ```bash
  python scripts/geo_client.py industry                    # List of industries in the database
  python scripts/geo_client.py industry 商用清洁机器人      # Details for that industry
  ```
  **The industry database is built one industry at a time; it does not cover all industries.** Three hard rules:

  1. **State the sample scope before reporting numbers.** `sample_note` in the response says how many industries are built and the maximum number of brands on a leaderboard;
     without this, a user who sees only a few brands will immediately conclude "the data is unreliable".
  2. When the industry is not in the database, the response returns `已建库: false` (in database: false): say honestly "this industry is not in the database yet" and give them the
     `已建库的行业` (industries in the database) list. **Never** answer with data from a similarly named industry ("工业清洁机器人" (industrial cleaning robots) is not
     "商用清洁机器人" (commercial cleaning robots); one character apart means a different set of brands).
  3. Questions about "industry trends" may only be answered with the `series` from `heat` (industry share of voice) / `cr3` (brand concentration) / `top1`
     in the response. **Never** use a single brand's numbers as industry numbers: the two look alike,
     the user cannot tell when you get it wrong, and they will make decisions based on it.

- **Conversational** (let the GEO agent run tools itself and give conclusions + structured cards):
  ```bash
  python scripts/geo_client.py chat "今天投放效果怎么样?累计被搜到几个问题?"
  ```
  Output = a natural-language answer + a "structured card" JSON block (hit counts, competitors, etc.). **Treat the numbers in the structured card as authoritative**; do not rely only on the natural-language part.

- **Data only** (no LLM, faster and cheaper; use when you only need numbers):

  | Command | What you get |
  |---|---|
  | `data overview` | Latest report center data: six metrics + change + attainment for the latest daily/weekly/monthly period |
  | `data period` | Details for one period: six metrics + change + overall exposure rate per platform + that period's AI summary text |
  | `data periods` | Six metrics for the most recent periods (newest → oldest), to see the trend |
  | `data coverage` | Cumulative number of hit questions / core keywords (deduplicated by question) |
  | `data publish` | Publishing progress + list of published articles |
  | `data diagnosis` | Progress or result summary of the one-click full diagnosis |
  | `data run` | Execution status of the latest scheduled audit (finished or not / how many items / which platform errored) |

  ```bash
  python scripts/geo_client.py data overview
  ```

  The old names `today` / `growth` / `batch` / `report` still work (equivalent to `overview` / `overview` / `run` / `diagnosis` respectively); new integrations should always use the new names in the table above.

- **Check permissions** (what the current token can do; decides whether to bring up "drafting/publishing"):
  ```bash
  python scripts/geo_client.py capabilities
  ```

## Authentication

`check` **needs no credentials** and runs without a token installed. All other commands require one:

The script reads credentials from environment variables, and **never hardcodes or prints the token**:

- `VIGILATH_AGENT_TOKEN` (required): a 1-year account token issued by Vigilath on sign-up.
- `VIGILATH_BASE` (optional): defaults to `https://vigilath.cn/api/agent/v1`; the one-line installer fills it in automatically from your site domain, so you usually do not need to touch it.

When credentials expire you can reauthorize (the terminal shows a pairing code; the user approves it in the browser; no need to reinstall the skill):

```bash
python scripts/geo_client.py login
```

## Data definitions

- **Only read out numbers returned by the API**; never convert them yourself, and never add or divide numbers from different commands. Returned percentages are already final values (0–100, already rounded); **do not multiply by 100 again**.
- **Copy the six metric names exactly from `metric_labels` in the response**: "整体露出率" (overall exposure rate), "TOP1露出率" (TOP1 exposure rate), "TOP3露出率" (TOP3 exposure rate), "内容中正率" (content neutral-positive rate), "文章引用率" (article citation rate), "品牌声量度" (brand share of voice). **Do not invent** terms such as "hit rate", "impression rate", or "coverage rate" to refer to them.
- **Use the "fully tallied" period**: the latest daily report is **yesterday**; today's is still running. When the user asks "how is today", first give the fully tallied numbers, then add today's in-progress data, **marked as still being tallied and for reference only**; never draw conclusions from it.
- **"Did we hit the target" is always answered from the weekly report**; do not answer it with daily report numbers.
- `coverage` (whether a question has ever been found, deduplicated by question) and the overall exposure rate (whether each answer mentions the brand, by answer) **are two different measures**; never mix them, convert one into the other, or explain them as the same metric in the same sentence.
- `data diagnosis` is the "one-click full diagnostic report", **not** the report center's daily/weekly/monthly reports; do not confuse them.
  Its response contains `report_url`: the **full, clickable report address**. When the user asks "where can I see the full report",
  give it as a Markdown link, and explain that it only opens when logged in with the same account, and that a PDF can be exported from the report page for forwarding.
  **Do not** just say "it's on the diagnosis page" and leave them to assemble the address in the browser.
- **No conclusions about periods you have not queried.** Cross-period comparisons such as "same as yesterday" or "up from last week" must come from
  **two periods of data actually fetched** via `data periods` or two `data period` calls. If the user has not asked about a period this turn
  and you have not queried it, **never** casually add "about the same as yesterday": that is fabricated, and it sounds completely real.
  If there is no comparable period, say directly "there is no data for the previous period yet".
- **Only read out credits returned by the API.** `wallet` gives the **balance**; when the user asks "where did it go / spending details /
  why did it decrease", the balance cannot answer that: it requires the transaction history. **Never** reverse-engineer a spending breakdown
  from balance differences; they will reconcile against it. When the skill channel cannot reach the transaction history API, say honestly "details are on the
  account page in the web app"; do not make anything up.

## Balance and payment (read this when you hit a 402)

Actions such as publishing articles and placements are **paid** and draw from the account's compute-credit wallet.

```bash
python scripts/geo_client.py wallet              # Check balance: look before any paid action
python scripts/geo_client.py topup 100           # Top up 100 CNY: returns how to pay (including the web top-up address)
python scripts/geo_client.py topup 100 --tx 0x…   # After paying on-chain, submit the transaction hash for reconciliation
```

**A `402` is not an error**; it means "payment required": the response includes how much more is needed, the current balance, and two top-up paths.

- **Have the user top up on the web** (WeChat QR code, preferred): give them the page address from `web` in the response.
  Crediting is confirmed by the WeChat callback; check `wallet` a few seconds after payment and do not assume it arrives instantly.
- **Pay yourself** (direct agent payment, for overseas scenarios): pay using the network, asset, recipient address, and amount in `accepts`,
  then reconcile with `topup <amount> --tx <hash>` once you have the transaction hash.

> This section used to say `topup --wechat` was "preferred". The endpoint that command called **was never implemented**
> and only returned 404, so it has been removed. When the user says "top up for me" to the GEO assistant **in the Vigilath web app**,
> the assistant can produce a WeChat QR payment order directly; the skill channel cannot reach that capability, so this only points to web top-up.

Three notes:

- **Read out the amount and recipient address exactly as given; do not change a single character or compute them yourself.** Money sent to the wrong address cannot be recovered.
- **Do not retry repeatedly when the balance is insufficient**: every attempt will fail and make the user think the system is broken. Stop and state how much needs to be topped up.
- **Do not decide the top-up amount for the user.** Report the amount this order needs and let them decide.

### What costs money and how to pay

**Audits are always free** (the 11 items above + the one-click full diagnosis), with no subscription or top-up needed. Only two things cost money:

| What costs money | How it is charged |
|---|---|
| **Ongoing monitoring** (tracking questions, producing daily/weekly/monthly reports) | Monthly subscription; tiers and prices **are whatever `plans` returns** |
| **Publishing articles to paid media** | Media library price × 1.2, minimum ¥4 |

> **Always look prices up live; do not memorize them.** This section once hardcoded "three tiers: ¥99 / ¥299 / ¥1599",
> while the tier slugs in the client are `watch10 / watch30 / watch100`: the two had long since diverged,
> and reading them out quoted wrong prices to users. `plans` can be queried without login; run it before making a recommendation.

#### Activating a monthly subscription: two steps, and **the second is the one most often forgotten**

```bash
python scripts/geo_client.py plans                    # First check tiers and prices (no login needed)
python scripts/geo_client.py subscribe watch30        # ① Start → returns 402
python scripts/geo_client.py subscribe watch30 --order <order-number>   # ② After paying, you must call again with the order number
```

The 402 returned in ① contains: `WeixinPay-Required` (payment code), `X-Out-Trade-No` (order number),
`amount` (the cost this time). Pass the **payment code** to the host's `weixinpay_pay` tool to request the user's authorization,
and **remember the order number**.

② After the user authorizes, call again with the order number; only then is it actually activated. **Skip this step and it will never activate.**

When you hit `503 PAY_UNAVAILABLE`, this site has not enabled WeChat Agent Pay: **read out** the message in the response
(usually "please purchase on the web") as is; do not look for another route yourself, and do not retry.

#### How to read the responses

| `code` | Meaning | What to do |
|---|---|---|
| `PAYMENT_REQUIRED` | Payment required | Read out the amount and launch WeChat Pay |
| `NOT_PAID` | Not fully paid yet / order lookup temporarily unavailable | Retry later with the **same** order number; **do not place a new order** (that would create a second charge) |
| `SUCCESS` | Activated | Tell the user which tier was activated |
| `already_fulfilled` | This order was already activated earlier | Explain that it was a duplicate request; no need to pay again |
| `REFUNDED` | Activation failed, **automatically refunded** | Tell the user the money has been returned via the original method; **do not pay again** |
| `FULFILL_AND_REFUND_FAILED` | Activation failed and the refund also failed | Ask the user to contact support; **do not pay again** |

Crediting and activation **are determined by our order lookup**, not by the user saying "I've paid".

## Capability boundaries

**Getting what this channel can and cannot do wrong is a source of hallucination.** The table below reflects the real tool set:

| What the user wants | In this channel |
|---|---|
| Website health check / SEO health check / advanced audits / industry leaderboard / AI recommendation quick check | **Yes**, runs directly without login |
| View report center data, hit lists, diagnostic reports, sentiment monitoring, balance | **Yes**, via `data` / `chat` after `login` |
| Create a topic, preview keyword expansion | **Yes**, via `chat` |
| **Set seed keywords / save questions into ongoing monitoring** | **No**: the platform hard-blocks the skill channel. Have the user do it on the topic settings page of the Vigilath web app, or tell the GEO assistant in the web app |
| **Start a batch run / enable daily sentiment monitoring / top up** | **No**: same as above; these can only be done by telling the GEO assistant in the web app |
| Actual external publishing | **No**, controlled by internal platform guardrails |

- For things out of reach, **say plainly that it cannot be done here and point to the web app**.
  **Never** describe interfaces you have not seen: do not say "there's an XX button in the web app, go click it"; you do not know what is on the page;
  name only the page (such as "topic settings page" or "account page") and let the user find it.
- `check` measures **the site's own friendliness to AI** (static signals such as crawlability, structure, metadata, and authority signals),
  which **is not the same as** what AI engines actually say about the brand. When the user asks "does AI actually mention / cite me",
  do not answer with the health check score: that requires the "one-click full diagnosis" (via `chat`, authorization required, 8–20 minutes per job).
- Engine selection, query scheduling, and frequency are fixed by the Vigilath platform; **neither you nor the user can specify them**, only view the results.
  After saving, **it does not start running immediately** and waits for the platform's next cycle: say this proactively,
  or the user will watch the page waiting for a result that will not come that day.
- **The one-click full diagnosis is a heavy job, 8–20 minutes per job**: after starting it, tell the user honestly how long to wait and let them come back later; **do not check progress repeatedly in the same turn**, and do not give unfounded time promises like "almost done".
- When the response contains `gated: true`: this is a **paid capability that must be activated**; the user will be told clearly that activation is required and should contact Vigilath. **The system makes no silent changes; when not activated it says so clearly and never pretends it has taken effect.** Do not retry, and do not try again with different wording.
- One token = one account; it can only see that account's own data.
- A `401` error = the token is invalid/expired. Have the user run `python scripts/geo_client.py login` to reauthorize;
  if that still fails, contact Vigilath. **Do not retry repeatedly.**
- `401` = credentials expired: run `python3 scripts/geo_client.py login` to reauthorize; **do not** tell the user to contact us,
  and **do not** rerun the install command (that would overwrite the already installed skill directory).
