# GEO Readiness Checker

## Project Overview
Python CLI tool that checks a website's GEO (Generative Engine Optimization) readiness — how well web content is optimized for AI-powered search engines (ChatGPT, Perplexity, Google AI Overviews, Copilot).

## Architecture
- **Runtime package**(2026-04-17 重构后)**:`backend/geo_checker/` 是活跃代码,12 文件(`__init__.py` / `__main__.py` / `state.py` / `io.py` / `output.py` / `constants.py` / `checks.py` 2958 行 / `orchestrate.py` / `ai.py` / `reports.py` + `modes/` 下 8 个 mode)。**所有性能 / 功能改动都只进这个 package**
- **Dependencies**: `requests`, `beautifulsoup4`, `sqlalchemy`(backend),`fastapi` / `uvicorn`(生产 runtime)

### `geo_checker` 的三份同源文件(只有一份活跃)

历史演进导致仓库里有三份字节相同 / 同源的 `geo_checker` 代码。**修改时注意不要动错**:

| 位置 | 状态 | 加载时机 | 是否改 |
|---|---|---|---|
| `backend/geo_checker/`(package,12 文件) | **活跃** | uvicorn 从 `/backend` 启动,`import geo_checker` 命中这里;`pip install -e .` 后的 `geo-checker` CLI 也走这里 | **所有改动进这里** |
| `geo_checker.py`(根,8065 行单体) | **冻结**,= pre-refactor tag | 只在用户显式 `python geo_checker.py <url>` 时运行,runtime 不加载 | **不要改**,上游合并基准 |
| `archive/geo_checker_v1_baseline.py`(8065 行) | **冻结**,字节与根文件相同 | 从不加载 | **不要改**,只读历史归档 |

**为什么保留根文件 + archive 两份冻结**:

- 根文件:保留上游 `Yaqing2023/GEO` 的"原版 CLI"形态,方便 `git pull upstream main` 时肉眼对照
- archive:独立的只读归档,未来若根文件被删或更新,archive 仍是 pre-refactor-package-2026-04-17 的字节级基准
- 两份同源看似冗余,但职责不同:一个是"CLI 入口",一个是"历史基准"

**改动原则**:

- 加 check / 修 bug / 性能优化 → 改 `backend/geo_checker/` 对应文件
- 合上游 → 先 `git diff pre-refactor-package-2026-04-17 upstream/main -- geo_checker.py` 看 hunk,然后手动 porting 到 `backend/geo_checker/` 的对应模块;根文件 + archive 不动
- 回归对比 → 跑 `python geo_checker.py <url>`(根文件)vs `POST /api/check/anonymous`(package),两边 category 分数应一致(近期验证过 moltspay.com 两边 80/A 完全相同)

## CLI Modes

### Free (no API key required)
All free modes work by fetching the target site and querying public APIs (Wikipedia, npm registry, GitHub search, etc.).

- Default: 25-category site analysis with 0-100 AI Visibility Score
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
