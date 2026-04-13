# GEO Readiness Checker

## Project Overview
Python CLI tool that checks a website's GEO (Generative Engine Optimization) readiness — how well web content is optimized for AI-powered search engines (ChatGPT, Perplexity, Google AI Overviews, Copilot).

## Architecture
- **Single-file core**: `geo_checker.py` contains all logic (~4500 lines)
- **Package wrapper**: `geo_checker/` directory with `__init__.py` and `__main__.py` for pip install
- **Dependencies**: `requests`, `beautifulsoup4` only

## CLI Modes

### Free (no API key required)
All free modes work by fetching the target site and querying public APIs (Wikipedia, npm registry, GitHub search, etc.).

- Default: 23-category site analysis with 0-100 AI Visibility Score
- `--fix`: Show actionable fix recommendations
- `--compare`: Side-by-side multi-site comparison
- `--crawl-check`: Server log analysis for AI crawler activity
- `--crawl-test`: AI crawler accessibility test (no logs needed)
- `--authority-audit`: Off-page authority signals (GitHub, npm, PyPI, Wikipedia, etc.)

### Paid (require AI API keys)
All paid modes call real AI engines to measure what they actually say about the target.

- `--citation-check`: AI citation check via Perplexity. Requires `PERPLEXITY_API_KEY`.
- `--ai-visibility`: Full multi-engine AI visibility audit. Requires at least one of `PERPLEXITY_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.
- `--entity`: Entity GEO audit for brand/product/person (8 dimensions, 2 of which are free Wikipedia/Wikidata/platform checks). Requires `OPENAI_API_KEY`.

## Key Patterns
- `track_score(category, earned, max_points)` accumulates per-category scores
- `fetch(url)` caches HTTP responses in `_page_cache`
- `PASS/WARN/FAIL/INFO/FIX` are ANSI-colored status labels
- `SHOW_FIX` global toggles fix recommendation output
- `_query_openai()`, `_query_perplexity()`, `_query_anthropic()` are the AI engine helpers
- `_classify_framing(answer, brand)` classifies AI sentiment toward an entity

## Running
```bash
# With venv
.venv/bin/python geo_checker.py https://example.com

# Installed
geo-checker https://example.com
```

## Enhancement Tracking
See `ENHANCEMENT.md` for planned and completed features.
