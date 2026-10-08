"""SQLAlchemy ORM models + Pydantic schemas -- sentiment monitoring accounts / knowledge base / task logs.

Database: the main GEO SQLAlchemy DB (geo_checker.db).
yuqin's own tables (posts/analyses/briefs/drafts) are managed by the sentinel-service microservice,
not modeled here -- the frontend fetches via sentinel-service GET endpoints.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from geo.database import Base


KeywordKind = Literal["entity", "legal", "exec", "project", "competitor", "custom"]


class KeywordItem(BaseModel):
    term: str = Field(..., min_length=1)
    is_new: Optional[bool] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


class KeywordGroup(BaseModel):
    name: str = Field(..., min_length=1)
    kind: KeywordKind
    items: list[KeywordItem] = Field(default_factory=list)


def flatten_keyword_groups(groups: list[KeywordGroup]) -> list[str]:
    """Expand groups into a flat keyword list (sent to sentinel-service); groups with enabled=False are skipped."""
    seen: set[str] = set()
    out: list[str] = []
    for g in groups:
        for it in g.items:
            if it.enabled is False:
                continue
            t = it.term.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def account_symbol(acc) -> str:
    """The account's storage symbol on the sentinel-service side -- use the stock ticker if present, else the brand name.

    The stock ticker is optional (most customers are not listed companies), but on the sentinel side posts /
    analyses / briefs / drafts are all archived and fetched by symbol; an empty symbol
    leaves the whole workspace blank. So the "ticker -> brand name" fallback must be **written once, used everywhere**:
    if writes (monitor/analyze/brief) and reads (posts/briefs/today) are off by even one step in metric definition,
    data is written under key A and read from key B.

    sentinel-service `/run-monitor` internally also does `symbol = req.ticker or req.target`,
    consistent with this -- remember to update that side when changing this.

    The argument is duck-typed: ORM rows and the SimpleNamespace snapshot in full_diagnosis both work.
    """
    return (getattr(acc, "ticker", "") or "").strip() or (getattr(acc, "target", "") or "").strip()


# ─────────────────────────── ORM ──────────────────────────────


class SentimentAccountORM(Base):
    """User's sentiment monitoring account (config + state machine)."""
    __tablename__ = "sentiment_accounts"
    # Uniqueness metric definition = (user, brand name, stock ticker).
    # Before 2026-09-07 it was (user_id, ticker): once the ticker became optional, a second unlisted brand
    # also had an empty ticker, and the old constraint turned it straight into an IntegrityError (500).
    __table_args__ = (
        UniqueConstraint("user_id", "target", "ticker", name="uq_user_target_ticker"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    target = Column(String, nullable=False)             # Brand name
    # Stock ticker -- **optional**; unlisted entities use an empty string (the column is NOT NULL, store "" not NULL).
    # It only feeds the "fetch by ticker" crawlers (Eastmoney Guba/announcements/research/industry, Sina stocks, Xueqiu);
    # persisting/fetching always uses the `symbol` property below; do not use this column as a key directly.
    ticker = Column(String, nullable=False, default="", server_default="")
    aliases_json = Column(Text, default="[]")
    intent = Column(Text, nullable=True)
    keywords_json = Column(Text, default="[]")
    # Structured groups: list[KeywordGroup], for categorized management in the UI + Excel import.
    # Legacy field keywords_json is kept as a flat fallback; the pipeline prefers the flattened keyword_groups_json.
    keyword_groups_json = Column(Text, default="[]")
    excludes_json = Column(Text, default="[]")
    # Media allowlist (list of platform codes, e.g. ["xueqiu","weibo"]). Empty list = run all 15 platforms.
    # Aligned with PLATFORM_CATALOG in services/sentinel-service/search/plan.py.
    media_allowlist_json = Column(Text, default="[]")
    notify_emails_json = Column(Text, default="[]")

    # WeChat official account album URL list -- weekly watch on KOL/competitor official accounts, enumerated via the getalbum API.
    # Empty list = weixin_album crawler disabled.
    weixin_album_urls_json = Column(Text, nullable=False, default="[]", server_default="[]")
    # newsnow source id list (e.g. ['weibo','zhihu','v2ex','toutiao']) -- pulls trending lists from the self-hosted
    # newsnow container, filtering titles locally by aliases+keywords. Empty list = disabled.
    newsnow_sources_json = Column(Text, nullable=False, default="[]", server_default="[]")

    active = Column(Boolean, nullable=False, default=True)
    # Hour of day to run in Beijing time (0-23). NULL = unspecified, follow the global cron cadence.
    # Billing metric definition is "one charge per account per Beijing date", so this only shifts the time, not the cost.
    run_hour = Column(Integer, nullable=True)

    # Task state machine
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)     # pending / running / success / failed
    last_run_error = Column(Text, nullable=True)
    last_run_stats = Column(Text, nullable=True)        # JSON: {monitor:{...}, analyze:{...}, brief:{...}}

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def symbol(self) -> str:
        """Storage symbol on the sentinel-service side (posts/analyses/briefs are all archived by it)."""
        return account_symbol(self)

    knowledge = relationship("SentimentKnowledgeORM", back_populates="account",
                             cascade="all, delete-orphan")
    run_logs = relationship("SentimentRunLogORM", back_populates="account",
                            cascade="all, delete-orphan")


class SentimentKnowledgeORM(Base):
    """Knowledge base, 3 documents (brand_voice / legal_redlines / response_playbook)."""
    __tablename__ = "sentiment_knowledge"
    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_account_key"),)

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("sentiment_accounts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    key = Column(String, nullable=False)                # brand_voice / legal_redlines / response_playbook
    body = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("SentimentAccountORM", back_populates="knowledge")


class SentimentRunLogORM(Base):
    """Task execution log (for troubleshooting + history display)."""
    __tablename__ = "sentiment_run_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("sentiment_accounts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    trigger = Column(String, nullable=False)            # first_run / cron / manual
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)             # running / success / failed
    error = Column(Text, nullable=True)
    stats_json = Column(Text, nullable=True)            # Same shape as last_run_stats
    duration_s = Column(Integer, nullable=True)

    account = relationship("SentimentAccountORM", back_populates="run_logs")


class SentimentPlatformORM(Base):
    """Media platform catalog (global config, not user-private).
    Replaces the hardcoding previously scattered across 3 places:
      - frontend/src/constants/sentimentPlatforms.ts
      - services/sentinel-service/search/plan.py PLATFORM_CATALOG
      - backend/geo/services/sentinel_client.py _PLATFORM_CODE_TO_DOMAIN
    Adding a platform / changing a domain / adjusting region categories only needs INSERT/UPDATE on this table -- no code change, no restart.
    Seeded by migrations/010_sentiment_platforms.py."""
    __tablename__ = "sentiment_platforms"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=False, unique=True, index=True)  # 'xueqiu', 'eastmoney' …
    domain = Column(String, nullable=False)                          # 'xueqiu.com', passed to sentinel as `site:`
    # category is the legacy single axis from 010 (finance/social/forum/video/news/overseas);
    # from 011 it is split into two orthogonal dimensions media_type + industry -- the UI only reads those two; category exists only as a
    # legacy column to avoid breaking existing INSERT rows; new code must not depend on it.
    category = Column(String, nullable=False)
    media_type = Column(String, nullable=False, default="")          # web/weibo/wechat/app/forum/short_video/long_video/...
    industry = Column(String, nullable=False, default="")            # finance/general/tech/science/party_media/health/overseas_en
    region = Column(String, nullable=False)                          # mainland / hk / overseas
    name_zh = Column(String, nullable=False, default="")
    name_en = Column(String, nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)          # Sort order within a category
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────── Pydantic ─────────────────────────


class SentimentAccountCreate(BaseModel):
    enterprise_user_id: Optional[int] = Field(default=None, ge=1)
    target: str = Field(..., min_length=1)
    # Optional -- unlisted entities just leave it empty (the frontend label always said "选填" ("optional"), yet here it enforced min_length=1,
    # so users without a ticker got a 422 right in onboarding).
    ticker: str = ""
    aliases: list[str] = Field(default_factory=list)
    intent: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    keyword_groups: list[KeywordGroup] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    media_allowlist: list[str] = Field(default_factory=list)
    notify_emails: list[EmailStr] = Field(default_factory=list, max_length=10)
    weixin_album_urls: list[str] = Field(default_factory=list)
    newsnow_sources: list[str] = Field(default_factory=list)
    run_hour: Optional[int] = Field(default=None, ge=0, le=23)
    run_now: bool = True


class SentimentAccountUpdate(BaseModel):
    target: Optional[str] = None
    # None = leave this column alone; "" = explicitly clear the stock ticker (back to unlisted)
    ticker: Optional[str] = None
    aliases: Optional[list[str]] = None
    intent: Optional[str] = None
    keywords: Optional[list[str]] = None
    keyword_groups: Optional[list[KeywordGroup]] = None
    excludes: Optional[list[str]] = None
    media_allowlist: Optional[list[str]] = None
    notify_emails: Optional[list[EmailStr]] = None
    weixin_album_urls: Optional[list[str]] = None
    newsnow_sources: Optional[list[str]] = None
    run_hour: Optional[int] = Field(default=None, ge=0, le=23)
    active: Optional[bool] = None


class SentimentAccountOut(BaseModel):
    id: int
    user_id: int
    target: str
    ticker: str
    # Storage symbol = ticker or target. The frontend always uses it to fetch posts/briefs/today;
    # stop using ticker -- an account with an empty ticker would disable every fetch query in the workspace.
    symbol: str
    aliases: list[str]
    intent: Optional[str]
    keywords: list[str]
    keyword_groups: list[KeywordGroup] = Field(default_factory=list)
    excludes: list[str]
    media_allowlist: list[str]
    notify_emails: list[str]
    weixin_album_urls: list[str] = Field(default_factory=list)
    newsnow_sources: list[str] = Field(default_factory=list)
    run_hour: Optional[int] = None
    active: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    last_run_error: Optional[str]
    created_at: datetime

    @classmethod
    def from_orm_row(cls, row: SentimentAccountORM) -> "SentimentAccountOut":
        groups_raw = json.loads(row.keyword_groups_json or "[]")
        groups = [KeywordGroup.model_validate(g) for g in groups_raw]
        return cls(
            id=row.id, user_id=row.user_id,
            target=row.target, ticker=row.ticker,
            symbol=account_symbol(row),
            aliases=json.loads(row.aliases_json or "[]"),
            intent=row.intent,
            keywords=json.loads(row.keywords_json or "[]"),
            keyword_groups=groups,
            excludes=json.loads(row.excludes_json or "[]"),
            media_allowlist=json.loads(row.media_allowlist_json or "[]"),
            notify_emails=json.loads(row.notify_emails_json or "[]"),
            weixin_album_urls=json.loads(row.weixin_album_urls_json or "[]"),
            newsnow_sources=json.loads(row.newsnow_sources_json or "[]"),
            run_hour=row.run_hour,
            active=row.active,
            last_run_at=_as_utc(row.last_run_at),
            last_run_status=row.last_run_status,
            last_run_error=row.last_run_error,
            created_at=_as_utc(row.created_at),
        )


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Explicitly mark naive datetimes read from SQLite (actually UTC, since the pipeline writes with datetime.utcnow())
    as UTC tz-aware, so Pydantic serialization outputs ISO 8601 with a
    '+00:00' suffix and the frontend new Date() parses it correctly as UTC.

    Otherwise the frontend timeAgo() treats tz-less strings as local time (UTC+8), showing "8 hours ago"."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class RunStatusOut(BaseModel):
    status: Optional[str]
    last_run_at: Optional[datetime]
    error: Optional[str]


class KnowledgeOut(BaseModel):
    key: str
    body: str
    updated_at: datetime


class SentimentPlatformOut(BaseModel):
    code: str
    domain: str
    # category is the legacy single axis, no longer consumed by the frontend; still output for debugging / old client compatibility.
    category: str
    # Two orthogonal dimensions since 011 -- the UI renders filters from these two. Empty string means the backend has not run 011.
    media_type: str = ""
    industry: str = ""
    region: str
    name_zh: str
    name_en: str

    @classmethod
    def from_orm_row(cls, row: SentimentPlatformORM) -> "SentimentPlatformOut":
        return cls(
            code=row.code, domain=row.domain,
            category=row.category,
            media_type=row.media_type or "",
            industry=row.industry or "",
            region=row.region,
            name_zh=row.name_zh or "", name_en=row.name_en or "",
        )
