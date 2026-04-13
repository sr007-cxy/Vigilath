# GEO Checker — Planned Enhancements

## 1. Competitive Comparison Analysis ✅ DONE
Compare GEO readiness across multiple URLs (`geo_checker.py --compare url1 url2 url3`) with a side-by-side scorecard showing which site wins on each check.

**Implementation:** `--compare` flag accepts multiple URLs. Runs all 23 checks silently on each, collects per-category scores, displays a side-by-side table with scores, percentages, overall AI Visibility Score, winner, point lead, and per-category advantages.

## 2. AI Visibility Score ✅ DONE
A metric measuring how likely a site is to appear in AI-generated answers. Combines crawlability, structured data, content quality, authority, and freshness into a single 0-100 score.

**Implementation:** All 23 check categories now track earned/max points via `track_score()`. Produces a 0-100 score with letter grade (A+ through F). Displayed at the end of every report with a per-category breakdown and visual bar chart.

## 3. Brand Entity Establishment
Check if the brand exists as a recognized entity in Knowledge Graphs, Wikidata, Wikipedia — verifiable identity that AI engines trust.

## 4. Topical Authority & Content Depth
Assess whether the site covers its core topics comprehensively through interlinked content clusters, not just isolated pages.

## 5. E-E-A-T Signals
Check for author bio pages, credentials, external bylines, and expertise indicators beyond basic author meta tags.

## 6. Backlink Profile & External Mentions
Assess third-party corroboration: authoritative sites linking to or mentioning the brand.

## 7. Content Freshness & Update Cadence
Measure how frequently content is updated across the site, not just homepage freshness.

## 8. Multi-Format Content Coverage
Check for content diversity: video + transcripts, podcasts, PDFs, infographics — more formats = more AI surface area.

## 9. Cross-Platform Content Distribution ✅ DONE
Detect presence on YouTube, Medium, LinkedIn, Reddit, Quora — platforms AI models train on.

**Implementation:** New `check_cross_platform()` function detects brand presence on 10 platforms (X/Twitter, LinkedIn, YouTube, GitHub, Reddit, Facebook, Instagram, Medium, TikTok, Quora). Uses a two-phase approach: (1) on-page signals from sameAs JSON-LD, social links, and meta tags, (2) active URL probing for platforms not found on-page. Shows AI training context mapping each platform to the AI models it feeds. Scores 0-5 based on coverage count.

## 10. Overall GEO Report Export
JSON/HTML/PDF export for stakeholders, CI/CD integration, and tracking improvements over time.

## 11. AI Citation / Source Attribution Tracking ✅ DONE (v1)
Check whether the site is actually being cited by AI engines (Perplexity, ChatGPT with browsing, Gemini). This is the ultimate GEO outcome metric — measure whether AI-generated answers reference or link back to the site.

**v1 Implementation:** `--citation-check URL` flag (PAID feature, requires `PERPLEXITY_API_KEY` env var). Sends 5-6 brand-relevant queries to the Perplexity Sonar API (e.g., "What is {brand}?", "{brand} review", "Best alternatives to {brand}"). Checks if the target domain appears in the `citations` array or is mentioned in the answer text. Produces a citation rate percentage, AI Visibility grade (A-F), per-query breakdown, and actionable recommendations if citation rate is low. Handles rate limiting, auth errors, and graceful fallbacks.

### 11b. Expanded AI Visibility Audit (`--ai-visibility`) ✅ DONE v2
Evolve `--citation-check` into a comprehensive "Would AI recommend this brand for the right query?" audit. Core philosophy: **measure selection signals, not just mention counts.**

**Custom query support** — Accept `--queries "best AI payment tools" "x402 tools"` so users can test category-specific prompts relevant to their niche, not just generic brand queries.

**Entity definition consistency** — Send "What is {brand}?" and "What does {brand} do?" multiple times. Check whether AI gives a consistent, accurate definition (e.g., "MoltsPay is a payment layer for AI agents") or returns confused/contradictory answers. Flag brand confusion (e.g., AI confuses your product with wallets, exchanges, or unrelated tools).

**Competitor tracking** — For each query, report which competitors get cited alongside (or instead of) the brand. Track whether the brand is framed as a leader, alternative, or niche option. Output a competitor co-mention matrix.

**Answer stability** — Run each query 3x and classify results as: stable mention (3/3), unstable mention (1-2/3), or never mentioned (0/3). Brands want robust presence, not lucky one-off mentions.

**AI answer framing analysis** — Classify how the AI describes the brand: leader, recommended option, one-of-many, experimental, niche. Detect "recommended-action" language ("use X", "consider X", "one option is X") which is stronger than passive mentions.

**Missing-topic gap detection** — Test prompts where the brand *should* appear based on its category but doesn't. These are the highest-value content gaps to fix. Output as an actionable "create content for these topics" list.

**Category association check** — Verify AI associates the brand with the right concepts (e.g., "AI agent payments", "x402", "crypto payment SDK") rather than wrong categories. Weak association = weak entity positioning.

**Multi-model support** — Test across multiple AI engines when API keys are available:
- Perplexity (`PERPLEXITY_API_KEY`) — always-on web search with structured citations
- OpenAI (`OPENAI_API_KEY`) — ChatGPT with web search tool
- Claude (`ANTHROPIC_API_KEY`) — Claude with web search tool
- Compare visibility across engines since each has different training data and retrieval

**Composite GEO Health Score** — Output a scorecard with sub-scores:
```
Prompt Visibility:     X/20  (cited in how many target queries)
Entity Clarity:        X/20  (consistent, accurate definitions)
Source Coverage:        X/20  (present on platforms AI references)
Competitor Position:   X/20  (framing relative to competitors)
Content Gap Score:     X/20  (missing-topic coverage)
─────────────────────────────
GEO Health:           X/100
```

## 12. Prompt-Aligned Content Optimization
Detect whether content answers question-style queries directly (who/what/how/why patterns). AI engines serve content that maps to how users prompt them — pages structured around natural questions rank higher in AI answers.

## 13. Semantic Markup & Linked Data Quality
Beyond basic JSON-LD detection, validate that structured data uses specific, granular schema types (e.g., `HowTo`, `Recipe`, `Product` with reviews, `FAQPage`) rather than just generic `WebPage` or `Article`. Richer schema types give AI engines more extractable facts.

## 14. Content Deduplication / Cannibalization
Detect pages competing for the same topic or keywords, which confuses AI engines about which page to cite. Flag near-duplicate titles, meta descriptions, and content overlap across pages.

## 15. AI-Specific robots.txt Granularity
Beyond checking if AI bots are allowed or blocked, assess whether the site has a *strategic* allow/block policy. Check for `ai.txt`, per-bot directives, and whether the policy balances crawl access (for citation) vs. training opt-out (for IP protection).

## 16. Page-Level Entity Density
Measure how clearly each page defines and reinforces a single entity or topic. Pages with a clear primary entity help AI engines extract clean, attributable facts rather than muddled multi-topic content.

## 17. Conversational Content Readiness
Check for Q&A pairs, "People Also Ask" style content, and conversational tone that maps well to chat-based AI interfaces. Detect question headings (`<h2>How do I...?</h2>`) and direct-answer patterns.

## 18. AI Snippet Extraction Friendliness
Detect whether key facts appear in extractable positions (first paragraph, table cells, list items, definition lists) vs. buried deep in prose. AI engines preferentially extract content from prominent, structured positions.

## 19. Trust & Safety Signals
Check for privacy policy, terms of service, contact information completeness, DMCA/copyright pages, and business registration details. These are trust signals AI engines use to assess source credibility and determine citation-worthiness.

## 20. API & Data Feed Availability
Beyond OpenAPI detection, check for RSS feed richness (full content vs. excerpts), webhook/integration documentation, and machine-readable data exports. AI agents increasingly consume structured data feeds programmatically.

## 21. Entity GEO Audit (`--entity`) ✅ DONE
Audit the GEO readiness of a **brand, product, or person** — without requiring a URL. Instead of crawling a website, this mode queries AI engines to assess how well an entity is recognized, described, and recommended across AI-powered search.

**Usage:**
```bash
# Brand
geo-checker --entity "Stripe" --entity-type brand

# Product
geo-checker --entity "ChatGPT" --entity-type product

# Person
geo-checker --entity "Andrej Karpathy" --entity-type person
```

Requires `OPENAI_API_KEY` environment variable.

**How it works:** Two-phase approach: (1) Free checks probe Wikipedia, Wikidata, and 8 major platforms for entity presence, (2) Paid checks send targeted queries to OpenAI GPT-4o-mini with web search and analyze responses. Queries are tailored per entity type — brands get competitor/market queries, products get feature/alternative queries, persons get expertise/contribution queries. All 8 dimensions support `--fix` for actionable recommendations.

### Checks & Scoring (8 dimensions, 0-20 each, 160 total)

**1. Entity Recognition (0-20)** — PAID
Ask "What is {entity}?" / "Who is {entity}?" multiple times. Score based on whether AI returns a substantive, accurate answer vs. "I don't know" or confused responses. Measures whether the entity exists in AI's knowledge at all.

**2. Entity Clarity (0-20)** — PAID
Run the same identity query 3x and compare answers for consistency. Detect contradictions, confusion with other entities, or vague/generic descriptions. High clarity = AI gives the same accurate definition every time.

**3. Category Association (0-20)** — PAID
Test whether AI associates the entity with the right concepts. For brands: industry/market category. For products: use case/problem space. For persons: field of expertise/known contributions. Score based on how strongly and correctly AI maps the entity to its domain.

**4. Competitive Position (0-20)** — PAID
For brands/products: ask "Best alternatives to {entity}" and "How does {entity} compare to competitors?" — analyze whether it's framed as a leader, recommended option, or niche player. For persons: ask about peers/leaders in their field — is the person mentioned among top experts?

**5. Sentiment & Framing (0-20)** — PAID
Analyze how AI describes the entity across all responses. Classify language as: strongly positive, positive, neutral, mixed, or negative. Detect "recommended-action" language ("use X", "consider X") which is stronger than passive mentions. For persons: detect credibility/authority language.

**6. Content Gap Analysis (0-20)** — PAID
Test queries where the entity *should* appear based on its type/category but doesn't. These represent the highest-value content creation opportunities. Output as an actionable "create content for these topics" list.

**7. Knowledge Graph Presence (0-20)** — FREE
Check if the entity exists in Wikipedia (search API), Wikidata (search API), and infer Google Knowledge Panel presence. These are the structured knowledge sources AI engines trust most — entities in knowledge graphs get cited far more reliably.

**8. Cross-Platform Footprint (0-20)** — FREE
Probe 8 major platforms AI models train on: X/Twitter, LinkedIn, YouTube, GitHub, Reddit, Facebook, Medium, TikTok. Uses the same profile URL probing and soft-404 detection logic as the URL-mode `check_cross_platform()` for consistency. Searched by name, not scraped from a URL.

### Entity-Type-Specific Queries

**Brand queries:**
- "What is {entity}?", "What does {entity} do?"
- "Is {entity} reliable/trustworthy?"
- "Best alternatives to {entity}"
- "{entity} vs competitors", "{entity} review"
- Industry/category queries (e.g., "best payment platforms")

**Product queries:**
- "What is {entity}?", "What does {entity} do?"
- "Is {entity} worth it?", "{entity} pros and cons"
- "Best alternatives to {entity}"
- "{entity} vs {competitor}" style queries
- Category queries (e.g., "best project management tools")

**Person queries:**
- "Who is {entity}?"
- "What is {entity} known for?"
- "{entity} contributions to {field}"
- "Top experts in {field}" / "Leaders in {field}"
- "{entity} publications/work/research"

### Output

```
============================================================
  ENTITY GEO AUDIT: "Stripe" (brand)
============================================================

  Entity Recognition     18/20  ██████████████████░░  90%
  Entity Clarity         16/20  ████████████████░░░░  80%
  Category Association   14/20  ██████████████░░░░░░  70%
  Competitive Position   12/20  ████████████░░░░░░░░  60%
  Sentiment & Framing    16/20  ████████████████░░░░  80%
  Content Gap            10/20  ██████████░░░░░░░░░░  50%
  ──────────────────────  ─────
  ENTITY GEO SCORE       86/120

  Grade: B

  Key Findings:
  - AI consistently recognizes Stripe as an online payment platform
  - Strong positive sentiment; frequently recommended
  - Competitors mentioned: PayPal, Square, Adyen
  - Content gaps: "Stripe for marketplaces", "Stripe vs Adyen 2024"
```

---

## Bug Fixes Applied

- **Facebook soft-404 detection** — Facebook returns HTTP 200 for non-existent pages but redirects to `/login/`. Detection now catches login redirects and expanded soft-404 phrases ("content isn't available", "does not exist", etc.).
- **GitHub detection in authority audit** — Switched from guessing `github.com/{brand}` (fails when org name differs from domain) to GitHub Search API which finds repos by name regardless of owner.
- **npm detection in authority audit** — Switched from scraping search page (returns 403) to npm registry JSON API (`registry.npmjs.org/{package}`).
- **PyPI detection in authority audit** — Switched from project page (returns captcha) to PyPI JSON API (`pypi.org/pypi/{package}/json`).
