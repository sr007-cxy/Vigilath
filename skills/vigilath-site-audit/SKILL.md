---
name: vigilath-site-audit
description: Give it a URL and it tells you whether AI crawlers can understand the site: per-item scores across 25 categories + a 0-100 score and letter grade. No login, free, unlimited, results in 30 seconds. Use when the user gives a URL and asks "AI 爬虫读得懂我的网站吗" ("can AI crawlers understand my website"), "这个站对 AI 友好吗" ("is this site AI-friendly"), "AI 搜索能收录我吗" ("can AI search index me"), "网站 GEO 分数多少" ("what is the site's GEO score"), or "帮我体检一下官网" ("give my website a health check"). **Website only**; for "what AI engines actually say about my brand" use vigilath-report, and for traditional search engines use vigilath-seo-audit.
slug: vigilath-site-audit
version: 1.0.0
displayName: Website AI-friendliness audit
summary: Give a URL and see whether AI can understand your website: 25 items scored one by one, no registration, unlimited
tags: [GEO, AI search, website audit, SEO, no login]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Website AI-Friendliness Health Check

Give it a URL, it runs all 25 check categories and returns a 0–100 score and a letter grade, plus which items are not right.

**No login, free, unlimited**: users can use it without a Vigilath account, so do not ask them for one.

## What it does

Give it a URL and in **30 seconds** it returns a 25-category, item-by-item health check:

| What it checks | Examples |
|---|---|
| Can it be crawled | Whether robots.txt blocks AI crawlers, whether sitemap.xml exists |
| Can the content be understood | Titles and descriptions, structured data, body hierarchy |
| Site fundamentals | HTTPS, mobile friendliness, page weight |
| Off-site signals | Brand presence in knowledge graphs, trust and security signals |

You get: **a 0–100 score + letter grade**, pass/warning/fail for every item, and exactly which item failed
(for example "no sitemap.xml" rather than "structure needs improvement").

The free tier shows details for 5 categories; the rest report only category names and counts. **Never guess conclusions for the locked parts.**

## What exactly is checked

**25 GEO (AI-friendliness) items**; ★ marks those whose details are visible without registration; the rest report only category names and counts:

| | Category | In plain words |
|---|---|---|
| ★ | HTTPS | Whether the site uses a secure connection |
| ★ | robots.txt | Whether AI crawlers are locked out |
| ★ | sitemap.xml | Whether AI is given a "page directory" |
| ★ | Meta Tags | Whether titles and descriptions exist and are written correctly |
| ★ | Mobile friendliness and page weight | Whether it opens on phones and how heavy the pages are |
| | llms.txt | Whether there is an instruction file dedicated to LLMs |
| | .well-known discovery files | Whether AI can automatically find your site information |
| | Search engine and AI platform index registration | Whether the site is registered with each platform |
| | Structured data | Whether content is marked up in a machine-readable format |
| | Content accessibility | Whether content is hidden in scripts and cannot be crawled |
| | AI crawl readiness | Whether AI crawlers can read everything smoothly when they visit |
| | AI-oriented content quality | Whether the content is good enough for AI to use as an answer |
| | Technical crawlability | Whether technical barriers block crawling |
| | Authority and trust signals | Whether the site looks trustworthy |
| | AI-specific optimization | Additional optimizations targeting AI search |
| | Social signals | Presence on social platforms |
| | AI answer format optimization | Whether the content layout suits being summarized by AI as an answer |
| | Breadcrumbs and knowledge panel | Structural navigation and knowledge card information |
| | URL canonicalization | Whether multiple URLs for the same page are splitting ranking weight |
| | External links and multimedia | Use of external links, images, and video |
| | Multilingual content depth | Whether multilingual content is sufficient |
| | Cross-platform content distribution | Whether content has been spread to other platforms |
| | Multi-page sampling | Samples multiple pages, not just the homepage |
| | Brand entity in knowledge graphs | Whether you appear in encyclopedias, knowledge panels, and similar places |
| | Trust and security signals | The site's trustworthiness and security posture |

**12 SEO items**; ★ marks those whose details are visible without registration:

| | Category | In plain words |
|---|---|---|
| ★ | Title tags | Whether each page has a title and whether titles are duplicated |
| ★ | Description tags | The snippet shown in search results |
| ★ | Heading hierarchy | Whether the H1/H2 structure is messy |
| | Indexability | Whether pages are allowed to be indexed |
| | URLs / canonical tags / redirects | Whether URL structure and redirects follow best practice |
| | Internal links | Internal link layout |
| | Image SEO | Whether images have alt text |
| | Rich-result structured data | Whether rich snippets such as star ratings and prices can appear |
| | Page speed signals | Whether pages load fast |
| | Mobile usability | Whether it works well on phones |
| | Crawl directives and sitemap | Whether crawl rules are configured correctly |
| | Multi-region targeting | Settings for different regions/languages |

The score comes from **actually running every item**, so it is reliable; without registration you simply do not see the **detailed findings** for the locked items.

## Ask it like this

- "帮我看看 example.com 对 AI 友好吗" ("Is example.com AI-friendly?")
- "为什么 AI 搜不到我的网站" ("Why can't AI search find my website?")
- "我的官网还有哪些没做对" ("What is my website still getting wrong?")
- "这两个站哪个更容易被 AI 收录" ("Which of these two sites is more likely to be indexed by AI?") (audit each once, then compare)

## When to use

- "我的网站 AI 爬虫读得懂吗" ("can AI crawlers understand my website"), "这个站对 AI 搜索友好吗" ("is this site friendly to AI search")
- "为什么 AI 搜不到我的网站" ("why can't AI search find my website"), "AI 会收录我吗" ("will AI index me")
- The user drops a URL and wants to know whether it is any good

**Do not use for**: questions about how the brand is mentioned in AI (that is measured AI engine testing; use vigilath-report), or traditional SEO (use vigilath-seo-audit).

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py check https://example.com --no-seo
```

**The URL must come only from the user's own words.** The client strips quotes, Markdown wrapping, and trailing punctuation, and adds a missing scheme,
but **never replaces the domain, never adds www., and never guesses**. If a message contains several URLs, or you are unsure which one is meant,
**ask first instead of picking one yourself**: audit the wrong site and every conclusion that follows is wrong, without the user being able to tell.

Returns in about 30 seconds:

- `url_checked`: **the address actually tested**. Read it out in your very first sentence so the user can verify it
- `geo.score` / `geo.grade`: 0–100 and A–F. **All 25 categories were actually run, so the score is reliable**
- `geo.summary`: counts of pass / warning / fail / info items
- `geo.top_issues`: **the items that are not right** (failures listed before items needing improvement). This is what to read out by default
- `geo.visible_categories`: categories with visible details, each with `status` and the original finding (expand only when the user wants details)
- `geo.locked_categories`: those with **only a category name and no finding**

## How to read the results

- **By default, cover only the score + `top_issues`.** For each, explain "what getting this wrong causes and how to fix it";
  **do not list check item names one by one**: terms like "llms.txt", "structured data", and "AI crawl readiness" mean nothing to the user,
  and reading out twenty-odd categories in one go just makes them stop reading (this is real user feedback).
  If they want all the details, expand `visible_categories`.
- **Never invent findings for locked categories.** You may only say "details for these categories require registration"; do not guess whether they are good or bad.
- **The results give specific items, not just a score.** You will see actionable facts like "no sitemap.xml":
  when explaining, translate them into consequences: "AI crawlers have no page directory to follow, so pages will be missed during indexing".
- **A low score does not mean a bad website**: this measures "friendliness to AI crawling and understanding", which has nothing to do with how good the site looks or how well it sells; do not extrapolate.

## Free tier and paywall

All 25 categories + visible details, unlimited, no account: **this skill has no paywall**.

Only bring up next steps when the user wants more, routed by need, one suggestion at a time:

- Want details and fix suggestions for locked categories → register for Vigilath, then run `python scripts/geo_client.py login` to authorize
- Want to know how AI engines actually answer questions about them → vigilath-report (a full diagnostic report)
- Want to check whether they've got traditional search engines right → vigilath-seo-audit
