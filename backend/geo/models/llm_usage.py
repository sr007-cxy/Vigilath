"""LLM call accounting -- one detail row per LLM API call + daily rollup.

Background: on 2026-08-13 the api.deepseek.com balance ran out (402), and the one-click full diagnosis narrative layer / Yuanbao sentiment monitoring synthesis /
geo-agent were all half-dead. While debugging we found the app layer had **no record at all** of LLM calls -- how many calls,
which pipeline burned them; we could only see one total on the third-party usage page. These two tables fill that gap.

Writers: five processes -- backend / telemetry-service / sentinel-service / geo-agent / geo_checker,
all via `geo.services.llm_usage.record_llm_call()` (telemetry / sentinel each have a mirror implementation,
see V-networks/worker-center/app/llm_usage.py, services/sentinel-service/storage/llm_usage.py).
Reader: `geo/api/admin_llm_usage.py` (admin "Model usage" page, admin-only).

Accounting failures **never affect business logic** -- record_llm_call is fully wrapped in try and uses its own session.

Three metric definitions (read these before changing anything here):
  1. One detail row = one provider HTTP request actually sent, including failures. Calls that never sent HTTP (no key,
     degraded directly) are not recorded at all, otherwise the failure rate is distorted.
  2. **"Call count" is always SUM(requests_count), never COUNT(*)** -- one pydantic-ai run
     records 1 row but represents N round trips (result.usage().requests); this column lets both granularities coexist safely.
  3. NULL in token columns = not obtained, 0 = provider explicitly returned 0. The semantics differ; the schema deliberately has no
     server_default -- do not add one.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from geo.database import Base

# Writer process id
SERVICE_BACKEND = "backend"
SERVICE_TELEMETRY = "telemetry"
SERVICE_SENTINEL = "sentinel"
SERVICE_AGENT = "agent"
SERVICE_CHECKER = "checker"
ALL_SERVICES = (SERVICE_BACKEND, SERVICE_TELEMETRY, SERVICE_SENTINEL, SERVICE_AGENT, SERVICE_CHECKER)

# Call kind -- "usage" for chat / embedding / image is not the same unit; the page shows them separately
KIND_CHAT = "chat"
KIND_EMBEDDING = "embedding"
KIND_IMAGE = "image"
KIND_RERANK = "rerank"
ALL_KINDS = (KIND_CHAT, KIND_EMBEDDING, KIND_IMAGE, KIND_RERANK)

# Status -- the first three are all billed by the provider (truncated / parse_error fail only after getting a 200);
# for reconciliation, "billed requests" = status in (ok, truncated, parse_error).
STATUS_OK = "ok"
STATUS_TRUNCATED = "truncated"          # 200 but finish_reason=length
STATUS_PARSE_ERROR = "parse_error"      # 200 but response body failed to parse
STATUS_HTTP_ERROR = "http_error"        # 4xx / 5xx
STATUS_NETWORK_ERROR = "network_error"  # Never reached the provider (timeout / connection dropped)
BILLED_STATUSES = (STATUS_OK, STATUS_TRUNCATED, STATUS_PARSE_ERROR)

# Sentinel values for missing context -- the row count with these values is the "call site without context" leak metric,
# permanently exposed on the page; it should normally be 0.
FEATURE_UNKNOWN = "unknown"


class LlmCallLogORM(Base):
    """Call details. Kept for LLM_USAGE_DETAIL_KEEP_DAYS days (default 90), purged by llm_usage_rollup.purge_details.

    No foreign keys: three processes write across databases, and FKs would block telemetry / sentinel writes on
    deletes of users / topics in the main DB.
    """

    __tablename__ = "llm_call_log"
    __table_args__ = (
        Index("idx_llm_call_created", "created_at"),              # Scanned by purge
        Index("idx_llm_call_day_feature", "bj_date", "feature"),  # rollup + feature ranking
        Index("idx_llm_call_day_service", "bj_date", "service"),
        Index("idx_llm_call_user", "user_id"),
        Index("idx_llm_call_trace", "trace_id"),
    )

    # SQLite rowid alias only recognizes INTEGER PRIMARY KEY; BIGINT loses autoincrement
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)

    created_at = Column(DateTime, nullable=False)          # UTC
    # Beijing date, **computed at write time** -- DB timezone functions differ between SQLite / PG at query time;
    # same metric definition as geo/api/ai_telemetry_reports.py:341 _bj_date (UTC+8)
    bj_date = Column(String(10), nullable=False)

    service = Column(String(16), nullable=False, default=SERVICE_BACKEND)  # One of ALL_SERVICES
    host = Column(String(32), nullable=False, default="")   # Distinguishes the standalone prod sentinel host
    feature = Column(String(64), nullable=False, default=FEATURE_UNKNOWN)  # Feature scenario

    provider = Column(String(24), nullable=False)           # deepseek|openrouter|glm|qwen|dashscope|ark|...
    model = Column(String(64), nullable=False, default="")
    # Host of base_url -- distinguishes official direct / neolink aggregation gateway / openrouter.
    # provider cannot be guessed from base_url: DEEPSEEK_BASE_URL now points to neolink, so guessing is always wrong.
    endpoint_host = Column(String(48), nullable=False, default="")
    call_kind = Column(String(12), nullable=False, default=KIND_CHAT)      # One of ALL_KINDS

    status = Column(String(16), nullable=False, default=STATUS_OK)
    error_kind = Column(String(24), nullable=False, default="")   # http_402|http_429|timeout|network|...
    http_status = Column(Integer, nullable=True)

    # NULL = not obtained, 0 = provider explicitly returned 0 -- deliberately no default, see metric definition 3 in the module docstring
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cached_tokens = Column(Integer, nullable=True)
    reasoning_tokens = Column(Integer, nullable=True)
    token_source = Column(String(10), nullable=False, default="none")   # provider|none

    requests_count = Column(Integer, nullable=False, default=1)  # See metric definition 2
    attempt_no = Column(SmallInteger, nullable=False, default=0)  # Internal retry round
    latency_ms = Column(Integer, nullable=False, default=0)

    user_id = Column(Integer, nullable=True)
    topic_id = Column(Integer, nullable=True)
    ref_type = Column(String(16), nullable=False, default="")    # doc|report|run|plan|task|session
    ref_id = Column(String(64), nullable=False, default="")
    trace_id = Column(String(64), nullable=False, default="")    # Ties N calls of one business action together
    call_group_id = Column(String(32), nullable=False, default="")  # One logical call including internal retries

    est_cost_usd = Column(Numeric(12, 6), nullable=True)   # Reserved, not filled yet (no unit price table)
    # finish_reason / raw usage that cannot be normalized, etc. **Never store prompt bodies or api keys**;
    # error text goes through redaction inside record_llm_call
    extra_json = Column(Text, nullable=True)


class LlmUsageDailyORM(Base):
    """Daily rollup, kept forever. Aggregated from details by geo/services/llm_usage_rollup.py.

    Read-path metric definition (following the proven report_daily_rollup pattern): history reads this table; **today (Beijing date)
    is never persisted**, the read path aggregates details directly (live tail).
    """

    __tablename__ = "llm_usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "bj_date", "service", "feature", "provider", "model", "call_kind",
            name="uq_llm_usage_daily_cell",
        ),
        Index("idx_llm_usage_daily_day", "bj_date"),
        Index("idx_llm_usage_daily_feature", "feature", "bj_date"),
    )

    id = Column(Integer, primary_key=True)
    bj_date = Column(String(10), nullable=False)
    service = Column(String(16), nullable=False)
    feature = Column(String(64), nullable=False)
    provider = Column(String(24), nullable=False)
    model = Column(String(64), nullable=False, default="")
    call_kind = Column(String(12), nullable=False, default=KIND_CHAT)

    calls = Column(Integer, nullable=False, default=0)       # = SUM(requests_count)
    rows_n = Column(Integer, nullable=False, default=0)      # Detail row count (may differ from calls)
    ok_calls = Column(Integer, nullable=False, default=0)
    error_calls = Column(Integer, nullable=False, default=0)

    prompt_tokens = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    completion_tokens = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    total_tokens = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    cached_tokens = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    reasoning_tokens = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)

    # Token coverage -- calls without tokens must not be mixed into the headline numbers; the page annotates using these two columns
    calls_with_tokens = Column(Integer, nullable=False, default=0)
    calls_without_tokens = Column(Integer, nullable=False, default=0)

    latency_ms_sum = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    users_n = Column(Integer, nullable=False, default=0)

    source = Column(String(8), nullable=False, default="rollup")
    updated_at = Column(DateTime, nullable=False)
