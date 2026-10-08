from dotenv import load_dotenv
load_dotenv()

import logging
import os
import time

# Playwright browsers: use shared location so both root and claudeuser can find them
_shared_pw = "/opt/geo/backend/.cache/ms-playwright"
if os.path.isdir(_shared_pw) and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _shared_pw

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from jwt import PyJWTError as JWTError
from geo.api import geo, seo, auth, membership, oauth, contact, payment, moltspay_payment, wechat_payment, advanced, account, balance, sentiment, ai_telemetry, ai_telemetry_reports, admin_review, admin_content_review, admin_media, content_trace, abuse_report, action_items, content, engine_sessions, content_templates, admin_crawl, platform_rules, compliance, admin_users, admin_llm_usage, admin_call_policy, gateway_portal, blog, industry_library, workbench_probe, report_export, rwj_channel, media_library, aitoearn_channel, full_diagnosis, skill_install, quick_check, advanced_anon, skillpay_api, wechat_auth, phone_auth, publish_accounts, publish_dispatch, agency_management
from geo.utils.error_handler import global_exception_handler, AppException
from geo.utils.request_log import configure_request_log

_timing_logger = logging.getLogger("geo.timing")

# Per-request JSONL log for /api/check* — a file separate from geo.timing,
# one line per API request, with user / tier / url / duration / status / score.
# Log path backend/logs/requests.jsonl (runtime CWD = /backend).
configure_request_log()

app = FastAPI(
    title="GEO Readiness Checker API",
    description="API for checking website readiness for Generative Engine Optimization (GEO)",
    version="0.1.0"
)

# CORS. IMPORTANT: do not combine `allow_origins=["*"]` with
# `allow_credentials=True` — starlette's CORSMiddleware silently drops the
# Access-Control-Allow-Origin header in that case (the CORS spec forbids
# wildcard + credentials). Symptom: browser fetch gets "No
# 'Access-Control-Allow-Origin' header is present" even though the endpoint
# itself works fine from curl. Fix: enumerate the dev origins explicitly,
# and use `allow_origin_regex` as a safety net for LAN IPs during testing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8070",
        "http://test.example.com:12080",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.\d+\.\d+\.\d+):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Global exception handlers. Note: in Starlette the base `Exception` is intercepted by ServerErrorMiddleware
# which still `raise exc` after calling the handler, so uvicorn logs AppException (e.g. 429
# quota exhausted, 402 membership gate) as an "uncaught exception" ERROR stack. The proper home for business exceptions
# is ExceptionMiddleware — this layer only handles **explicitly registered** exception classes, and after handling successfully
# does not re-raise. So each business exception below must be registered individually.
app.add_exception_handler(AppException, global_exception_handler)
app.add_exception_handler(RequestValidationError, global_exception_handler)
app.add_exception_handler(JWTError, global_exception_handler)
# The base `Exception` is the final fallback; only truly unexpected errors reach here.
app.add_exception_handler(Exception, global_exception_handler)

# Include API routes
app.include_router(geo.router, prefix="/api", tags=["geo"])
app.include_router(seo.router, prefix="/api", tags=["seo"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(wechat_auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(phone_auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(membership.router, prefix="/api", tags=["membership"])
app.include_router(oauth.router, prefix="/api/oauth", tags=["oauth"])
app.include_router(contact.router, prefix="/api", tags=["contact"])
app.include_router(payment.router, prefix="/api/payment", tags=["payment"])
app.include_router(moltspay_payment.router, prefix="/api/payment", tags=["moltspay"])
app.include_router(wechat_payment.router, prefix="/api/payment", tags=["wechat"])
app.include_router(advanced.router, prefix="/api", tags=["advanced"])
app.include_router(full_diagnosis.router, prefix="/api", tags=["full-diagnosis"])
# No /api prefix: GET /skill is the "one address" given to AI agents (content negotiation; browsers redirect to /install)
app.include_router(skill_install.router, tags=["skill-install"])
# Login-free engine quick test (S2): no auth, quota-limited by device + egress IP (services/anon_quota.py)
app.include_router(quick_check.router, prefix="/api", tags=["quick-check"])
# Login-free entry to advanced audits: two zero-cost ones unlimited, four paid ones limited by device + IP
app.include_router(advanced_anon.router, prefix="/api", tags=["advanced-anon"])
# Pay Skill: paid entry for WeChat Agent Pay (X402)
app.include_router(skillpay_api.router, prefix="/api", tags=["skillpay"])
app.include_router(account.router, prefix="/api/account", tags=["account"])
app.include_router(balance.router, prefix="/api", tags=["balance"])
app.include_router(sentiment.router, prefix="/api", tags=["sentiment"])
app.include_router(ai_telemetry.router, prefix="/api", tags=["ai-telemetry"])
app.include_router(ai_telemetry_reports.router, prefix="/api", tags=["ai-telemetry-reports"])
app.include_router(report_export.router, prefix="/api", tags=["report-export"])
app.include_router(industry_library.router, prefix="/api", tags=["industry-library"])
app.include_router(admin_review.router, prefix="/api", tags=["admin"])
app.include_router(admin_review.topics_router, prefix="/api", tags=["admin-topics"])
app.include_router(admin_review.self_router, prefix="/api", tags=["topics-self"])
app.include_router(admin_content_review.router, prefix="/api", tags=["admin-content-review"])
# Generation provenance (reverse lookup of implicit labels under the "Labeling Measures") — item 5 of the filing correction list
app.include_router(content_trace.router, prefix="/api", tags=["content-trace"])
app.include_router(admin_media.router, prefix="/api", tags=["admin-media"])
# Reports and complaints — item 13 of the filing correction list. The /report endpoints **do not require login** (reporters are usually not users)
app.include_router(abuse_report.router, prefix="/api", tags=["abuse-report"])
app.include_router(action_items.router, prefix="/api", tags=["action-items"])
app.include_router(action_items.self_router, prefix="/api", tags=["action-items-self"])
app.include_router(content_templates.router, prefix="/api", tags=["content-templates"])
app.include_router(content.router, prefix="/api", tags=["content"])
app.include_router(engine_sessions.router, prefix="/api", tags=["engine-sessions"])
app.include_router(admin_crawl.router, prefix="/api", tags=["admin-crawl"])
app.include_router(platform_rules.router, prefix="/api", tags=["platform-rules"])
app.include_router(compliance.router, prefix="/api", tags=["compliance"])
app.include_router(compliance.admin_router, prefix="/api", tags=["compliance-admin"])
app.include_router(admin_users.router, prefix="/api", tags=["admin-users"])
app.include_router(agency_management.admin_router, prefix="/api", tags=["admin-agencies"])
app.include_router(agency_management.agency_router, prefix="/api", tags=["agency"])
app.include_router(agency_management.account_router, prefix="/api", tags=["account-agency"])
app.include_router(admin_llm_usage.router, prefix="/api", tags=["admin-llm-usage"])
# Call management: unified policy tiers for internal and external + call records (see geo/services/call_policy.py)
app.include_router(admin_call_policy.router, prefix="/api", tags=["admin-call-policy"])
app.include_router(gateway_portal.router, prefix="/api", tags=["gateway-portal"])
app.include_router(workbench_probe.router, prefix="/api", tags=["workbench-probe"])
app.include_router(blog.router, prefix="/api", tags=["blog"])
app.include_router(rwj_channel.router, prefix="/api", tags=["rwj-channel"])
# Media library: unified user-side entry (merged across suppliers, vendors hidden); /api/rwj/* is the ops-side live source
app.include_router(media_library.router, prefix="/api", tags=["media-library"])
app.include_router(aitoearn_channel.router, prefix="/api", tags=["aitoearn-channel"])
# Self-hosted account pool (server_pool channel). Three routers, three auth schemes:
#   publish_accounts.router   JWT admin/operator — admin console register/list/rebind/pool level
#   publish_dispatch.router   JWT admin/operator — create publish tasks
#   publish_dispatch.service_router  service token X-Publish-Service-Token — worker claim/receipt
# Workers use a token rather than a JWT: they run behind residential networks with no user identity, nor should they have one.
app.include_router(publish_accounts.router, prefix="/api", tags=["publish-accounts"])
app.include_router(publish_dispatch.router, prefix="/api", tags=["publish-dispatch"])
app.include_router(publish_dispatch.service_router, prefix="/api", tags=["publish-worker"])

# The GEO optimization agent (conversational) runs as a **separate service** (option B, agents.geo.service:app + separate venv);
# nginx reverse-proxies /api/agent/* to it. The main backend does not mount the agent or install pydantic-ai — fully isolating version conflicts.

# Sentiment monitoring scheduled jobs — only the leader process starts APScheduler.
# Deployment: with multiple workers, give a separate systemd unit GEO_SCHEDULER_LEADER=1,
# web workers do not set it → web traffic and scheduled job processes are isolated.
@app.on_event("startup")
def _start_sentiment_scheduler() -> None:
    try:
        from geo.services.sentiment_scheduler import maybe_start_from_env
        maybe_start_from_env()
    except Exception as e:  # noqa: BLE001
        logging.warning("sentiment scheduler failed to start: %s", e)


@app.on_event("shutdown")
def _stop_sentiment_scheduler() -> None:
    try:
        from geo.services.sentiment_scheduler import shutdown
        shutdown()
    except Exception:  # noqa: BLE001
        pass


# On startup clean up zombie generating rows left by the previous interrupted process — solution_generator runs in a
# daemon thread and is killed on backend restart, leaving DB status stuck at generating, and frontend polling
# never gets a result. The 10-minute threshold is STALE_GENERATING_THRESHOLD.
@app.on_event("startup")
def _reset_stale_solution_generating() -> None:
    try:
        from geo.services.solution_generator import reset_stale_generating
        reset_stale_generating()
    except Exception as e:  # noqa: BLE001
        logging.warning("reset stale solution rows failed: %s", e)
    try:
        from geo.services.full_diagnosis_generator import reset_stale_full_diagnosis
        reset_stale_full_diagnosis()
    except Exception as e:  # noqa: BLE001
        logging.warning("reset stale full-diagnosis rows failed: %s", e)
    # Fallback refunds: reports that were charged, failed, and never refunded (historical rows interrupted before the refund logic launched,
    # and anything that slipped through). Idempotent; once converged to 0 it is an empty scan.
    try:
        from geo.services.full_diagnosis_generator import refund_stuck_full_diagnosis
        refund_stuck_full_diagnosis()
    except Exception as e:  # noqa: BLE001
        logging.warning("refund stuck full-diagnosis reports failed: %s", e)


# Phase D.1 — content auto-generation cron (separate leader env, decoupled from sentiment)
@app.on_event("startup")
def _start_content_scheduler() -> None:
    try:
        from geo.services.content_scheduler import maybe_start_from_env
        maybe_start_from_env()
    except Exception as e:  # noqa: BLE001
        logging.warning("content scheduler failed to start: %s", e)


@app.on_event("shutdown")
def _stop_content_scheduler() -> None:
    try:
        from geo.services.content_scheduler import shutdown
        shutdown()
    except Exception:  # noqa: BLE001
        pass

# Request-level context for model call accounting: give each request a trace_id so the multiple model calls
# triggered within one request can be chained and drilled into on the "model usage" page (geo/services/llm_usage.py).
#
# **Only trace_id is set here, user_id is not looked up** — the JWT sub is the username, not the id, and getting the id costs another
# DB query; adding a query to every request (including the many that never call models) is not worth it. user_id is filled in by the
# entry points that actually call models via usage_ctx(user_id=...).
@app.middleware("http")
async def _llm_usage_trace(request: Request, call_next):
    try:
        from geo.services.llm_usage import usage_ctx
    except Exception:  # noqa: BLE001 — a problem in the accounting module must never take down the request
        return await call_next(request)
    import uuid
    with usage_ctx(trace_id=uuid.uuid4().hex, service="backend"):
        return await call_next(request)


# Endpoint-level timing. Only logs /api/check* to keep noise down — other
# endpoints (auth, usage polling, account pages) are fast and not interesting
# for perf analysis. Also stamps the response with X-Process-Time in ms so
# the browser network tab / curl can see it even without tail'ing logs.
@app.middleware("http")
async def _log_check_timing(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    response.headers["X-Process-Time"] = str(elapsed_ms)
    path = request.url.path
    if path.startswith("/api/check"):
        _timing_logger.info(
            f"http method={request.method} path={path} "
            f"status={response.status_code} elapsed_ms={elapsed_ms}"
        )
    return response


@app.get("/")
async def root():
    return {"message": "GEO Readiness Checker API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
