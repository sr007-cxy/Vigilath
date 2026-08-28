# Vigilath

Vigilath is an end-to-end platform for Generative Engine Optimization (GEO),
Answer Engine Optimization (AEO), AI visibility measurement, brand monitoring,
and content operations.

This repository contains a React application, a FastAPI backend, a reusable
GEO audit package and CLI, an isolated conversational agent, and supporting
telemetry, browser-automation, and sentiment-monitoring services.

## Capabilities

- Website GEO audits across 25 scored categories, normalized to a 0-100 AI
  Visibility Score.
- Competitive, crawler, authority, citation, visibility, AEO, and entity
  audits.
- Multi-engine prompt telemetry, citations, rankings, and brand-growth
  reporting.
- Multi-source brand monitoring with sentiment, risk, brief, and response
  workflows.
- Query expansion, content generation, review, scheduling, publishing, and
  citation attribution.
- Account, membership, quota, Stripe, WeChat Pay, and MoltsPay flows.
- A conversational Pydantic AI service and an installable agent skill.

## Repository layout

| Path | Purpose |
| --- | --- |
| `frontend/` | React 19, TypeScript, Vite, TanStack Query, i18next, and reports |
| `backend/geo/` | Main FastAPI application with 20 mounted routers |
| `backend/geo_checker/` | Active audit package and CLI |
| `backend/geo/agent/` | Conversational agent in an isolated Python environment |
| `backend/api_engine/` | API-based AI engine adapters |
| `backend/browser_engine/` | Browser-engine client abstractions |
| `services/browser-service/` | Playwright browser automation |
| `services/telemetry-service/` | AI visibility telemetry and gateway |
| `services/sentinel-service/` | Multi-tenant brand monitoring |
| `services/ddg-proxy/`, `services/openrouter-proxy/` | Egress proxies |
| `skills/vigilath-geo/` | Standalone skill client |
| `docs/` | Architecture, operations, product, and historical design documents |

## Audit-engine source of truth

Only `backend/geo_checker/` is active runtime code. The root
`geo_checker.py` and `archive/geo_checker_v1_baseline.py` are frozen
pre-refactor baselines. Implement audit fixes and features in the package.

## Quick start

### CLI

Python 3.9 or later is required.

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/geo-checker https://example.com
```

```bash
geo-checker https://example.com --fix
geo-checker --compare https://a.example https://b.example
geo-checker --crawl-check '/var/log/nginx/access*.log'
geo-checker --crawl-test https://example.com
geo-checker --authority-audit https://example.com
geo-checker --aeo-visibility https://example.com
geo-checker --citation-check https://example.com
geo-checker --ai-visibility https://example.com
geo-checker --entity "Example Brand" --entity-type brand
geo-checker https://example.com --report json --lang en
```

Paid engine modes require credentials reported by `geo-checker --help`.
Supply them through environment variables and never commit them.

JSON report export works in this revision. The PDF and HTML CLI paths reference
missing upstream helpers and are not production-ready.

### Web application

```bash
cd backend
python -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
venv/bin/python -m uvicorn geo.main:app --host 127.0.0.1 --port 8070 --reload
```

```bash
cd frontend
npm ci
npm run dev
```

Vite listens on port 3000 and proxies `/api` to the local backend.

### Docker Compose

```bash
docker compose up --build
```

The root stack starts the frontend, backend, Sentinel, NewsNow, and a Sentinel
data volume. The agent, browser, telemetry, and proxy services use separate
deployment configurations.

## Default audit categories

The source of truth is `CHECK_REGISTRY` in
`backend/geo_checker/orchestrate.py`.

1. HTTPS
2. robots.txt
3. llms.txt
4. .well-known discovery
5. sitemap.xml
6. search-engine and AI-platform registration
7. structured data
8. meta tags
9. content accessibility
10. AI crawl readiness
11. content quality for AI
12. technical crawlability
13. authority and trust signals
14. brand entities in knowledge graphs
15. trust and safety signals
16. AI-specific optimization
17. social signals
18. AI answer formats
19. schema and knowledge-panel readiness
20. mobile friendliness and page weight
21. URL normalization
22. outbound links and media
23. multilingual content depth
24. cross-platform distribution
25. multi-page sampling

## Architecture

```text
Browser
  |
  v
React/Vite frontend
  |
  +--> Main FastAPI service (:8070) --> PostgreSQL / Redis
  +--> Agent service (:8010, isolated environment)
  +--> Sentinel (:8090), telemetry, browser automation, and proxies
```

The agent is isolated because Pydantic AI requires newer Pydantic and FastAPI
versions than the pinned main backend. Sentinel stores tenant data in
PostgreSQL schemas named `tenant_<account_id>`.

See [the documentation index](docs/README.md) and
[the architecture overview](docs/architecture.md).

## Configuration and secrets

Copy `backend/.env.example` to `backend/.env` and set only the values needed
by enabled features.

- Keep environment files, API keys, OAuth secrets, payment keys, private keys,
  cookies, session exports, and database passwords out of Git.
- Use a secret manager or protected deployment environment in production.
- Generate `SECRET_KEY` with a cryptographically secure random generator.
- Browser VNC access requires an explicit `VNC_PASSWORD`.

This revision contains no recognized API-token or private-key format in the
tracked tree. The review did find historical plaintext SSH and VNC passwords.
Rotate those values: deleting them from the current tree does not erase Git
history.

## Testing

```bash
python -m pytest
cd frontend && npm run lint && npm run build
```

Some tests require external services and credentials.

## Documentation policy

New and actively maintained documentation must be written in English. Legacy
Chinese-language design documents remain for historical context until they are
translated or retired.

## License

`pyproject.toml` declares the CLI package as MIT-licensed. This revision does
not include a standalone `LICENSE` file.
