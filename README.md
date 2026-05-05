# GEO Readiness Checker

A Python CLI tool that checks a website's readiness for **Generative Engine Optimization (GEO)** — the practice of optimizing web content for AI-powered search engines and assistants (ChatGPT, Google AI Overviews, Perplexity, Copilot, etc.).

Performs **25 categories** of checks across content quality, technical crawlability, authority signals, AI-specific optimization, cross-platform distribution, and search engine platform registration. Produces a **0-100 AI Visibility Score** with letter grade.

## Installation

### From pip (recommended)
```bash
pip install geo-checker
```

### From source
```bash
git clone https://github.com/yourusername/geo-checker.git
cd geo-checker
pip install -e .
```

### As a Claude Code skill
Copy the `.claude/commands/geo-check.md` file into your project's `.claude/commands/` directory:
```bash
mkdir -p .claude/commands
cp .claude/commands/geo-check.md your-project/.claude/commands/
```
Then use `/geo-check https://example.com` in Claude Code.

## Usage

### CLI
```bash
# Diagnose only (includes AI Visibility Score)
geo-checker https://example.com

# Diagnose + show fix recommendations
geo-checker https://example.com --fix

# Compare multiple sites side-by-side
geo-checker --compare https://site1.com https://site2.com https://site3.com

# Analyze server logs for AI crawler activity
geo-checker --crawl-check /var/log/nginx/access*.log

# Test if AI crawlers can access your site (no logs needed)
geo-checker --crawl-test https://example.com

# Audit off-page authority signals
geo-checker --authority-audit https://example.com

# [PAID] Quick AI citation check (needs PERPLEXITY_API_KEY)
geo-checker --citation-check https://example.com

# [PAID] Full AI Visibility Audit (needs at least one AI API key)
geo-checker --ai-visibility https://example.com

# [PAID] AI Visibility Audit with custom queries
geo-checker --ai-visibility https://example.com --queries "best payment tools" "x402 tools"
```

### Python module
```bash
python -m geo_checker https://example.com --fix
```

### Claude Code slash command
```
/geo-check https://example.com
/geo-check https://example.com --fix
```

## AI Visibility Score

Every run produces a **0-100 AI Visibility Score** with a letter grade (A+ through F). The score aggregates all 23 check categories with weighted points and displays a per-category breakdown:

```
============================================================
  AI VISIBILITY SCORE: 58/100  (Grade: D)
============================================================

  Category Breakdown:
  Category                    Score  Bar
  ------------------------- -------  --------------------
  AI Crawl Readiness         7.0/8   █████████████████░░░
  AI Optimization            5.0/5   ████████████████████
  Cross-Platform             5.0/5   ████████████████████
  HTTPS                      5.0/5   ████████████████████
  Meta Tags                  7.0/7   ████████████████████
  ...
```

## Competitive Comparison

Compare GEO readiness across multiple sites with `--compare`:

```
============================================================
  GEO COMPETITIVE COMPARISON
============================================================

  Category                      site1.com      site2.com
  ------------------------- -------------- --------------
  HTTPS                      5.0/5   (100%)  5.0/5   (100%)
  robots.txt                 6.0/8   ( 75%)  3.0/8   ( 38%)
  Cross-Platform             5.0/5   (100%)  1.5/5   ( 30%)
  ...
  AI VISIBILITY SCORE            72/100 (B)      45/100 (F)

  Winner: site1.com (72/100, Grade B)
  Lead: +27 points over site2.com
```

## What It Checks

| # | Category | Key Checks |
|---|----------|------------|
| 1 | HTTPS | Secure connection |
| 2 | robots.txt | AI bot directives (GPTBot, ClaudeBot, etc.) |
| 3 | llms.txt | Existence and structure validation |
| 4 | .well-known | ai-plugin.json, security.txt, gpc.json |
| 5 | sitemap.xml | Existence, lastmod, URL count |
| 6 | Platform Registration | Google Search Console, Bing Webmaster, IndexNow |
| 7 | Structured Data | JSON-LD / schema.org |
| 8 | Meta Tags | Title, description, OG, hreflang, canonical |
| 9 | Content Accessibility | Word count, content-to-HTML ratio, headings |
| 10 | AI Crawl Readiness | SSR detection, meta robots, paywall, semantic HTML |
| 11 | Content Quality | Readability, FAQ detection, citations, lists |
| 12 | Technical Crawlability | Canonical chains, redirects, HTTP/2, RSS feeds |
| 13 | Authority & Trust | Security headers, humans.txt, author markup (E-E-A-T) |
| 14 | AI Optimization | Freshness signals, entity consistency, API endpoints |
| 15 | Social Signals | Twitter cards, sameAs JSON-LD links |
| 16 | AI Answer Formats | Definitions, tables, steps, pros/cons, takeaways |
| 17 | Schema & Knowledge Panel | BreadcrumbList, Organization completeness |
| 18 | Mobile & Page Weight | Viewport, HTML size, cache headers |
| 19 | URL Normalization | www/non-www, trailing slashes, case consistency |
| 20 | Outbound & Media | Link quality, VideoObject, transcripts, table markup |
| 21 | Multilingual Depth | hreflang page existence and content verification |
| 22 | Cross-Platform Distribution | Brand presence on X, LinkedIn, YouTube, GitHub, Reddit, Facebook, Instagram, Medium, TikTok, Quora — with AI training context mapping |
| 23 | Multi-Page Sampling | Consistency across sampled internal pages |

## Additional Modes

### `--crawl-check` — AI Crawler Log Analysis
Analyzes server access logs for AI/LLM crawler activity. Supports glob patterns and `.gz` compressed files. Reports per-bot request counts, status codes, top pages, and identifies missing critical crawlers.

### `--crawl-test` — AI Crawler Accessibility Test
Tests if your site is accessible to AI crawlers without needing log files. Checks robots.txt rules per bot, simulates requests with real AI bot user agents (detects WAF/CDN blocking), and queries the Common Crawl index.

### `--authority-audit` — Off-Page Authority Audit
Audits off-page authority signals: online reviews (Trustpilot, G2, Capterra, Product Hunt), awards/accreditations, Google authority (indexed pages, Knowledge Panel readiness, Wikipedia/Wikidata), and authoritative platform mentions (GitHub, npm, PyPI, Crunchbase, LinkedIn, HackerNews). Uses platform JSON APIs for reliable detection.

### `--citation-check` — AI Citation Check (PAID)
Quick check whether AI engines cite your site. Sends brand-relevant queries to Perplexity AI and checks if your domain appears in the citations array. Produces a citation rate percentage, AI Visibility grade (A-F), and per-query breakdown. Requires `PERPLEXITY_API_KEY`.

### `--ai-visibility` — AI Visibility Audit (PAID)
Comprehensive audit that answers: **"Would AI recommend your brand for the right query?"** Tests across multiple AI engines (Perplexity, ChatGPT, Claude) based on available API keys.

**What it checks:**
- **Prompt Visibility** — Is your brand cited when relevant queries are asked?
- **Entity Clarity** — Does AI give consistent, accurate definitions of your brand? Detects brand confusion.
- **Competitor Position** — Who else gets cited? How is your brand framed (leader/option/niche)?
- **Answer Stability** — Runs each query 3x to distinguish stable vs. lucky mentions.
- **Content Gaps** — Identifies queries where your brand *should* appear but doesn't.

**Output:**
```
  GEO HEALTH SCORECARD
  Prompt Visibility       X/20  ████████████░░░░░░░░  60%
  Entity Clarity          X/20  ██████████████████░░  90%
  Competitor Position     X/20  ██████░░░░░░░░░░░░░░  30%
  Answer Stability        X/20  ████████████████░░░░  80%
  Content Gap             X/20  ██████████░░░░░░░░░░  50%
  ──────────────────────  ─────
  GEO HEALTH             X/100
```

Requires at least one of: `PERPLEXITY_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

Use `--queries` to add custom prompts for your niche:
```bash
geo-checker --ai-visibility https://example.com --queries "best AI payment tools" "x402 ecosystem"
```

## Output

- **PASS** (green) — Good, meets GEO standards
- **WARN** (yellow) — Could be improved
- **FAIL** (red) — Missing or broken, needs attention
- **INFO** (blue) — Informational, optional improvement
- **FIX** (cyan) — Actionable fix recommendation (only with `--fix`)

## Browser Session Management

Browser engine adapters (通义千问 / DeepSeek / 文心一言 / 豆包) require saved login sessions to query AI engines headlessly. Sessions are stored as Playwright `storage_state` JSON files in `backend/data/browser_sessions/`.

### Check all sessions

```bash
# Check session health for all platforms (local analysis + online verification)
python backend/scripts/check_sessions.py

# Check specific platforms only
python backend/scripts/check_sessions.py --only qwen deepseek

# Re-login invalid sessions interactively (requires GUI)
python backend/scripts/check_sessions.py --fix

# Force re-login all platforms regardless of status
python backend/scripts/check_sessions.py --fix --all
```

### Individual platform login

Each platform also has its own login script for manual use:

```bash
# Interactive login (launches headed browser)
python backend/scripts/qwen_login.py
python backend/scripts/deepseek_login.py
python backend/scripts/wenxin_login.py
python backend/scripts/doubao_login.py

# Import token directly (no GUI needed)
python backend/scripts/deepseek_login.py --token 'eyJhbGci...'
python backend/scripts/doubao_login.py --token 'eyJhbGci...'

# Import full state from JSON export
python backend/scripts/qwen_login.py --import state.json

# Check current session status
python backend/scripts/deepseek_login.py --status

# Clear saved session
python backend/scripts/deepseek_login.py --clear
```

## Test Environment Deployment

### Server Topology

```
Internet → 123.125.194.100:12080 → 172.80.40.102:80 (nginx)
                                       ├── /              → /opt/geo/frontend/dist (SPA)
                                       ├── /api           → localhost:8000 (backend)
                                       └── /api/browser-cn/     → 172.80.40.103:8092
```

> **Note**: 101 (vm01) 原部署的 Playwright Browser Service (global/海外引擎) 已于 2026-04-29 下线清理，原因：Cloudflare Turnstile 无法在服务器端通过，海外引擎浏览器自动化方案不可行。海外引擎改走 API（OpenAI/Anthropic/Perplexity）。

### Services

| Server | IP | Service | Port | Unit | App Path |
|--------|-----|---------|------|------|----------|
| vm02 | 172.80.40.102 | Backend (FastAPI + uvicorn) | 8000 | `geo-backend.service` | `/opt/geo/backend` |
| vm02 | 172.80.40.102 | Frontend (nginx static) | 80 | `nginx.service` | `/opt/geo/frontend/dist` |
| vm03 | 172.80.40.103 | Playwright Browser Service (cn) | 8092 | `browser-service.service` | `/opt/browser-service` |

- SSH: `root` / `REDACTED_VM_PASSWORD`
- OS: Ubuntu 22.04 (all three)
- Python: 3.10 (browser-service), 3.12 (backend)

### Deploy Commands

**Backend + Frontend (102):**
```bash
# From local machine
sshpass -p 'REDACTED_VM_PASSWORD' ssh root@172.80.40.102 'bash -s' << 'EOF'
cd /opt/geo
git pull --ff-only origin feature/playwright
systemctl restart geo-backend.service
cd frontend && npm ci && npm run build
systemctl reload nginx
EOF
```

**Playwright microservice (103 only, CN engines):**
```bash
sshpass -p 'REDACTED_VM_PASSWORD' rsync -avz --delete \
  -e 'sshpass -p REDACTED_VM_PASSWORD ssh -o StrictHostKeyChecking=no' \
  services/browser-service/app/ root@172.80.40.103:/opt/browser-service/app/
sshpass -p 'REDACTED_VM_PASSWORD' ssh root@172.80.40.103 'systemctl restart browser-service.service'
```

### Smoke Test

```bash
# Backend health
curl -s http://172.80.40.102:8000/health
# → {"status":"healthy"}

# Frontend
curl -sI http://172.80.40.102/
# → HTTP/1.1 200 OK

# Playwright (cn)
curl -s http://172.80.40.103:8092/docs
# → Swagger UI HTML
```

## Dependencies

- `requests>=2.28` — HTTP fetching
- `beautifulsoup4>=4.12` — HTML parsing

## License
