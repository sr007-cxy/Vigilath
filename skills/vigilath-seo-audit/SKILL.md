---
name: vigilath-seo-audit
description: The traditional search engine checklist: 12 items you haven't got right yet, with per-category SEO scores + an independent 0-100 score. No login, free, unlimited, results in 30 seconds. Use when the user gives a URL and asks "SEO 怎么样" ("how is my SEO"), "我的 SEO 分数" ("my SEO score"), "搜索引擎优化做得好不好" ("is my search engine optimization good"), "title 和 meta 有没有问题" ("are there problems with my title and meta"), or "网站标题标签对不对" ("are my site's title tags right"). **Traditional SEO only**; for whether AI crawlers can understand the site use vigilath-site-audit. The two scores are computed separately and never merged.
slug: vigilath-seo-audit
version: 1.0.0
displayName: Search engine optimization audit
summary: What you're still missing for Baidu and Google: 12-item SEO score, no registration, unlimited
tags: [SEO, website audit, no login]
license: Apache-2.0
homepage: https://github.com/zen7-duze/geo-skill
---

# Traditional SEO Health Check

Give it a URL, it runs all 12 SEO check categories and returns an independent 0–100 score and grade.

**No login, free, unlimited.**

## What it does

Give it a URL and it checks 12 traditional SEO categories one by one: title tags, descriptions, heading hierarchy (H1/H2),
image alt text, links, indexability, and more, returning an **independent 0–100 score**.

The free tier shows details for 3 categories (title tags / descriptions / heading hierarchy); the rest report category names only.

**This score and the GEO score use two separate measures; always report them separately**: a site may score 85 on traditional SEO
while its AI-friendliness is only grade C, and that contrast is exactly what the user most needs to hear.

## What exactly is checked

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

- "我的 SEO 做得怎么样" ("How is my SEO?")
- "title 和 meta description 有没有问题" ("Are there problems with my title and meta description?")
- "搜索引擎优化还有哪些没做" ("What search engine optimization have I not done yet?")

## When to use

- "我的 SEO 做得怎么样" ("how is my SEO"), "SEO 分数多少" ("what is my SEO score")
- "title / meta description / H 标签有没有问题" ("are there problems with my title / meta description / H tags")
- "搜索引擎优化还有哪些没做" ("what search engine optimization have I not done yet")

**Do not use for**: AI search friendliness (use vigilath-site-audit).

## How to use

> The commands below are **run by the AI assistant, not typed by you**: just ask it in the words above.

```bash
python scripts/geo_client.py check https://example.com --seo-only
```

**The URL must come only from the user's own words.** The client strips quotes, Markdown wrapping, and trailing punctuation and adds a missing scheme,
but **never replaces the domain and never guesses**. `url_checked` in the response is the address actually tested: read it out first so the user can verify;
if one message contains several URLs, ask first instead of picking one yourself.

Returns `seo.score` / `seo.grade` / `seo.top_issues` / `seo.visible_categories` /
`seo.locked_categories`, with the same structure as the website health check.

**When reading results, by default cover only the score + the items in `top_issues`**, explaining for each "what this causes and how to fix it";
**do not list all 12 category names one by one**: the user wants "what should I fix", not a checklist.
Expand `visible_categories` only if they want full details.

## Do not mix the two scores

**The SEO score and the GEO score are two independent measures; always report them separately.**

- Do not add them, average them, or combine them into a "composite score"
- A site can easily score 85 on SEO while its AI-friendliness is grade C; this is exactly the fact worth pointing out to the user:
  "You've done well on traditional search, but AI follows a different set of rules that you haven't addressed yet"
- When the user asks "so what's my score", honestly give both numbers plus a sentence on what each measures; do not merge them into one just to make the answer easier

## Free tier and paywall

All 12 categories + visible details, unlimited, no account: **there is no paywall**.

To see details and fix suggestions for locked categories → register and then `login`; to also look at the AI side → vigilath-site-audit.
