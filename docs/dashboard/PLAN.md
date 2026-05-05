# Multi-Platform Marketing Assistant — Implementation Plan (v2, agentic)

Multi-tenant, self-hosted platform to publish, monitor, and reply across Reddit,
LinkedIn, YouTube, dev.to, and Medium. Built **agent-first**: LLM agents with MCP
tools replace hard-coded workflow engines. Inspired by
[Postiz](https://github.com/gitroomhq/postiz-app), but swaps its rule/cron engine
for an agent loop.

---

## 1. Scope & goals

**In scope (v1)**
- **Multi-tenant** auth (orgs, members, roles: owner / editor / viewer).
- UI **platform picker** pulldown — lists all supported platforms, marks
  connected vs not-connected, disables actions the platform doesn't support.
- Per-tenant OAuth account connections; encrypted secret storage.
- **Content-generation agent**: brief/source → canonical draft in tenant voice.
- Compose-once agentic publishing: one brief, agent tailors per platform.
- Agentic monitoring loop: wakes on schedule/webhook, triages responses.
- Agentic reply loop: drafts in tenant voice, routes to approval or auto-send.
- Unified inbox + agent trace viewer (explainability).

**Out of scope (v1)**
- Paid ads, Instagram/TikTok/X (extend via new MCP tools later), billing, A/B tests.

---

## 2. Platform capabilities & constraints

| Platform | Post | Monitor | Reply | Gotchas |
|---|---|---|---|---|
| **Reddit** | PRAW / snoowrap; text, link, image, video | Poll inbox + subreddit streams | Comment + DM | Per-sub rules; 60 req/min; spam-filter risk |
| **LinkedIn** | UGC Posts API | **Community Mgmt API** needs approval | Comments via CMA; DMs effectively N/A | Apply week 1; can ship posts-only first |
| **YouTube** | Data API v3 (`videos.insert`) | `commentThreads.list`; PubSubHubbub for new videos | `comments.insert` | 10k units/day; upload = 1600 units — request quota bump |
| **dev.to** | `POST /articles` | Poll articles + comments endpoint | `POST /comments` | No webhooks; Markdown + front-matter |
| **Medium** | Integration-token write (frozen, best-effort) | RSS only | **None** | Flag write-only; offer Hashnode as alt adapter |

**Week-1 blockers:** LinkedIn CMA application, YouTube quota increase.

---

## 3. Architecture (agent-first)

```
┌──────────────┐       ┌─────────────────────┐
│ Next.js UI   │<─────>│ Node API (Fastify)  │
│ (platform    │ REST  │ + Auth.js multi-org │
│  picker,     │       └──────────┬──────────┘
│  inbox,      │                  │
│  trace view) │      Postgres (tenant-scoped RLS) + Redis
└──────────────┘                  │
                                  ▼
                      ┌───────────────────────┐
                      │  Agent Orchestrator   │
                      │  (Python, Claude      │
                      │   Agent SDK)          │
                      └─────────┬─────────────┘
                                │ spawns per job
     ┌──────────────┬───────────┼──────────────┬──────────────┐
     ▼              ▼           ▼              ▼              ▼
 ┌─────────┐  ┌───────────┐  ┌──────┐   ┌───────────┐  ┌───────────┐
 │ Content │─>│ Publisher │  │ ...  │   │ Monitor   │  │ Reply     │
 │  agent  │  │  agent    │  │      │   │  agent    │  │  agent    │
 └────┬────┘  └─────┬─────┘             └─────┬─────┘  └─────┬─────┘
      └─────────────┴────── MCP tool calls ────┴──────────────┘
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
          Reddit MCP     LinkedIn MCP     YouTube MCP   …
          (stdio/HTTP server per platform, stateless)
```

### Why agents instead of traditional SaaS pipelines
- **Extensibility:** adding a platform = new MCP server + one line in the agent's tool list. No new scheduler branches, no new rule DSL entries.
- **Judgment:** "should I reply to this?" and "what tone fits this subreddit?" are natural-language calls, not regex-on-karma heuristics.
- **Policy as prompt:** tenant brand voice, no-fly topics, escalation rules live in a versioned system prompt — product people edit it directly.
- **Auditability via traces:** every agent run = a transcript (tool calls + reasoning) stored per job_id; the inbox UI links to it.

### The four agents
1. **Content agent** — input: brief (topic, angle, sources: URLs / past posts / internal docs / RSS). Tools: `web_search`, `web_fetch`, `read_source_doc`, `search_past_posts`, `draft_content`, `save_draft`. Produces a canonical `Post` (title, body markdown, tags, suggested media, target platforms). Uses tenant `voice.md`. Output feeds Publisher.
2. **Publisher agent** — input: canonical `Post` + selected platforms. Tools: per-platform `publish`, `get_posting_guidelines`, `render_markdown`, `resize_media`. Produces per-platform variants (e.g., Reddit-friendly hook vs LinkedIn long-form vs dev.to canonical URL), posts, writes `post_variants` rows.
3. **Monitor agent** — runs on cron/webhook trigger. Tools: `fetch_new_responses(platform, since)`, `classify(text)`, `upsert_response`. Output: triaged items (question / praise / complaint / spam / ignore).
4. **Reply agent** — input: one triaged response + thread context. Tools: `get_thread`, `draft_reply`, `send_reply`, `queue_for_approval`. Gated by confidence + tenant policy.

Agents share a **tool registry** (MCP). Node API exposes MCP servers for platform tools; Python orchestrator invokes agents via the Claude Agent SDK. Prompt caching on the big system prompts keeps per-run cost low.

### Multi-tenancy
- `tenant_id` on every row; Postgres **row-level security** enforces isolation.
- Per-tenant encrypted secret vault (libsodium sealed boxes; master key per tenant derived from KMS root).
- Per-tenant rate-limit buckets in Redis so one noisy tenant can't starve others.
- Per-tenant agent config: `voice.md`, `policy.md`, `model_tier` (haiku for monitor, sonnet for drafts, opus on escalate).
- Auth: Auth.js with org model (owner/editor/viewer).

### Platform picker (UI)
- One canonical `platforms.ts` registry: `{ id, name, icon, capabilities: {post, monitor, reply, dm, media_types} }`.
- Compose page pulldown renders from registry; unconnected platforms show "Connect" CTA; unsupported capabilities are disabled with a tooltip ("Medium doesn't support comments").
- Same registry drives the agent tool list — single source of truth.

---

## 4. Data model (tenant-scoped, all rows carry `tenant_id`)

- `tenants`, `users`, `memberships` (role).
- `accounts` — platform, handle, encrypted tokens, scopes, expires_at.
- `posts` / `post_variants` — canonical content + per-platform rendered + remote_id.
- `schedules` — one-shot or cron.
- `responses` — remote_id, parent_ref, author, body, classification, status.
- `reply_drafts` — response_id, draft_text, model, confidence, approver_id, sent_at.
- `agent_runs` — job_id, agent_type, input, transcript (jsonb), tool_calls, cost, duration.
- `policies` — voice.md, rules.md, escalation.md (versioned).
- `audit_log` — every send / auth / policy edit.

---

## 5. Agent loop details

**Content agent flow:**
```
input: brief { topic, angle?, sources[], target_platforms[], cta? }
  1. research: web_search / web_fetch / read_source_doc / search_past_posts
  2. outline → draft (one canonical Markdown body + title variants + tags)
  3. self-critique against voice.md + policy.md (claim-check, tone, length)
  4. save_draft → returns post_id in `posts` table, status = 'draft'
human (or auto, per tenant config) approves → Publisher agent picks it up
```
Sources registry per tenant: RSS feeds, Notion/Drive docs (via MCP), a library of past high-performing posts (embeddings in pgvector for retrieval).

**Monitor agent trigger cadence:** Reddit 60s, dev.to 5m, YouTube 10m, LinkedIn 15m (if CMA), Medium N/A. YouTube also via PubSubHubbub webhook.

**Reply agent decision flow (prompt-driven, not code):**
```
system prompt = voice.md + policy.md + capability notes
user prompt   = response + thread context + author profile

agent:
  - calls classify() if not already classified
  - drafts reply
  - self-evaluates: confidence, policy compliance, tone match
  - chooses: send_reply | queue_for_approval | ignore
  - logs rationale to agent_runs.transcript
```

Kill-switch per tenant and per account. Hard cap: N auto-replies per thread, M per day.

---

## 6. Milestones (one full-stack dev, ~8 weeks)

| Week | Deliverable |
|---|---|
| 1 | Monorepo (`apps/web`, `apps/api`, `apps/agents`, `packages/mcp-*`). Auth.js + org model. Postgres w/ RLS. LinkedIn CMA + YT quota apps submitted. |
| 2 | Platform registry + picker UI. Connect flows for Reddit + dev.to. First MCP server (Reddit). |
| 3 | Publisher agent v1 (Reddit + dev.to). Schedule + retry. Agent trace viewer. |
| 4 | LinkedIn + YouTube MCP servers + connect flows. Medium write-only. |
| 5 | **Content agent** (brief → canonical draft). pgvector for past-posts retrieval. Voice editor UI. |
| 6 | Monitor agent (Reddit + dev.to + YT). Unified inbox. |
| 7 | Reply agent + approval queue + policy editor UI. LinkedIn monitor (if CMA approved). |
| 8 | Analytics + cost dashboard. Hardening: RLS tests, per-tenant rate limits, kill-switches, Sentry, Docker Compose deploy. |

---

## 7. Tech choices

- **Node 20** + **Fastify** + **Prisma** (Postgres). Keep API thin — it's mostly CRUD + MCP server hosting.
- **Python 3.12** + **Claude Agent SDK** + **FastAPI** for the orchestrator.
- **MCP** for all platform tools — stdio servers in dev, HTTP in prod.
- **Next.js 15** + Tailwind + shadcn/ui + Auth.js.
- **Redis** + **BullMQ** for agent job queue and rate-limit buckets.
- **Anthropic models**: Haiku 4.5 for monitor/classify, Sonnet 4.6 for publisher variants and reply drafts, Opus 4.7 for content generation + escalations. Prompt-cache system prompts (voice + policy).
- **pgvector** on Postgres for past-post retrieval (Content agent's `search_past_posts`).
- **Observability**: OpenTelemetry traces (agent runs are first-class spans) → Grafana; Sentry for errors.
- **Secrets**: libsodium sealed boxes, master key in KMS.

---

## 8. Risks & mitigations

- **Agent non-determinism** → every run traced, confidence-gated auto-send, approval queue default-on, hard per-thread/day caps, easy kill-switch.
- **LLM cost blow-up** → cheap model for routine triage, prompt caching, per-tenant cost budgets with circuit breaker.
- **LinkedIn CMA rejection** → ship posts-only for LinkedIn; defer comment agent there.
- **Medium dead-end** → flag in UI; recommend Hashnode adapter as drop-in.
- **Multi-tenant data leak** → RLS + per-tenant key derivation; add a nightly job that runs a canary query as tenant A trying to read tenant B.
- **Prompt injection from untrusted comments** → sanitize response bodies into a quoted section; system prompt explicitly tells agent not to follow instructions inside quoted user content; never give the reply agent destructive tools.
- **Platform TOS on automation** → rate-limit conservatively; human-approval mode by default; never impersonate or astroturf.

---

## 9. Open questions

1. Auto-reply autonomy: approval-queue-only by default, or allow full auto with per-tenant opt-in?
2. Self-host only, or also ship a managed multi-tenant hosted version?
3. Medium in or out? (Strong recommend: drop for Hashnode.)
4. Brand-voice seed: any existing posts/docs to seed per-tenant `voice.md` and the past-posts vector store?
5. Content agent autonomy: should it publish draft → approval automatically, or require a human-written brief each time? (Default proposal: human brief in, human approval out, agent does research + drafting in between.)
6. Research sources: should Content agent have web access (search + fetch), or only internal sources (RSS + Drive/Notion + past posts)?
