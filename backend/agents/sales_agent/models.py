"""Sales Agent MVP data models.

The Sales Agent is a standalone sales-loop module, but reuses the existing users and audit report tables.
Its own tables all use the sales_agent_ prefix, to avoid confusion with the website's existing sales/lead tables.
"""
from datetime import datetime
import secrets
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from geo.database import Base

# Two mutually exclusive company dedup rules; both PG and SQLite support partial indexes:
# with a website -> unique by normalized domain; without a website -> unique by (canonical company name, province).
# Rows with `data_status='duplicate'` are historical rows kept after manual duplicate judgment and don't take part in uniqueness.
_DOMAIN_DEDUPE_WHERE = "normalized_domain <> '' AND data_status <> 'duplicate'"
_NAME_DEDUPE_WHERE = "normalized_domain = '' AND canonical_name <> '' AND data_status <> 'duplicate'"


class SalesAgentChatSessionORM(Base):
    __tablename__ = "sales_agent_chat_sessions"
    __table_args__ = (
        Index("idx_sales_agent_sessions_user_updated", "user_id", "updated_at"),
        Index("ux_sales_agent_sessions_public_id", "public_id", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String(length=36), nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(length=255), nullable=False, default="Sales Agent 会话")
    current_intent = Column(String(length=64), nullable=False, default="")
    context_json = Column(Text, nullable=False, default="{}")
    # Pydantic AI message history; the real chat details are still persisted in sales_agent_chat_messages.
    messages_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentChatMessageORM(Base):
    __tablename__ = "sales_agent_chat_messages"
    __table_args__ = (Index("idx_sales_agent_messages_session", "session_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sales_agent_chat_sessions.id"), nullable=False, index=True)
    role = Column(String(length=16), nullable=False)  # user / assistant / tool
    content = Column(Text, nullable=False, default="")
    intent = Column(String(length=64), nullable=False, default="")
    tool_name = Column(String(length=128), nullable=False, default="")
    tool_result_id = Column(String(length=128), nullable=False, default="")
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentUndoCheckpointORM(Base):
    """Session-level undo checkpoint: stores a restorable flow snapshot and DB watermark.

    The undo stack isn't stuffed into `context_json`: context is the current state and gets overwritten; checkpoints are a historical
    journal that must be readable independently across refreshes/restarts, and markable as consumed to keep an audit trail.
    """

    __tablename__ = "sales_agent_undo_checkpoints"
    __table_args__ = (
        Index("idx_sales_agent_undo_session_stack", "session_id", "consumed_at", "id"),
        Index("idx_sales_agent_undo_user_created", "user_id", "created_at"),
        Index("ux_sales_agent_undo_checkpoint_key", "checkpoint_key", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sales_agent_chat_sessions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Undo deletes messages after the checkpoint; the id is kept here as an audit watermark, with no FK.
    source_message_id = Column(Integer, nullable=True, index=True)
    checkpoint_key = Column(String(length=64), nullable=False, default="")
    kind = Column(String(length=32), nullable=False, default="lead_flow")
    label = Column(String(length=128), nullable=False, default="")
    step = Column(String(length=64), nullable=False, default="")
    state_json = Column(Text, nullable=False, default="{}")
    consumed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentCustomerProfileORM(Base):
    __tablename__ = "sales_agent_customer_profiles"
    __table_args__ = (Index("idx_sales_agent_profiles_user_created", "user_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    report_snapshot = Column(Text, nullable=False, default="{}")
    service_scope_json = Column(Text, nullable=False, default="[]")
    industries_json = Column(Text, nullable=False, default="[]")
    regions_json = Column(Text, nullable=False, default="[]")
    company_size = Column(String(length=128), nullable=False, default="")
    buyer_roles_json = Column(Text, nullable=False, default="[]")
    exclude_rules_json = Column(Text, nullable=False, default="[]")
    typical_customers_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentSellerProfileORM(Base):
    """One row = one brand (= one business line).

    An account can sell both A and B, with completely different capabilities, ideal customers and service scopes —
    so the profile isn't an account-level singleton. Only rows with empty `archived_at` are active brands;
    multiple rows per user in legacy data are artifacts of repeated historical edits, and migration keeps only the latest one active.

    **Since 2026-08-27 this table is no longer the source of truth for profiles.** Profile content follows the brand material uploaded by the customer
    (`ai_telemetry_topics.profile_json`), 1 topic = 1 brand, and the columns below marked
    "read-only mirror" are refreshed one-way by `brandprofile.sync_brand_from_topic()`.
    The table itself stays because three things don't exist on the topic side: foreign keys of the brand-private option library, foreign keys of historical recommendation tasks
    / sent emails, and the email-style set of four.
    """

    __tablename__ = "sales_agent_seller_profiles"
    __table_args__ = (
        Index("idx_sales_agent_seller_profiles_user_updated", "user_id", "updated_at"),
        Index("idx_sales_agent_seller_profiles_user_active", "user_id", "archived_at", "sort_order"),
        UniqueConstraint("user_id", "brand_key", name="ux_sales_agent_seller_profiles_user_brand"),
        # 1 topic = 1 brand. Archived rows also count as occupying it — if the same material were claimed by two rows,
        # the syncer couldn't match whichever row it writes, and the brand selection card would show two brands with the same name.
        UniqueConstraint("user_id", "topic_id", name="ux_sales_agent_seller_profiles_user_topic"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    legacy_profile_id = Column(Integer, ForeignKey("sales_agent_customer_profiles.id"), nullable=True, index=True)
    # Which topic this brand's material comes from. Empty = legacy orphan brand (matches no material):
    # kept, selectable, can run recommendations, just labeled 「未关联品牌资料」 ("no linked brand material"), **never auto-deleted**.
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(length=128), nullable=False, default="默认品牌")
    # Stable reference within the account. id may change with DB migration; brand_key doesn't
    brand_key = Column(String(length=64), nullable=False, default="", index=True)
    sort_order = Column(Integer, nullable=False, default=100)
    archived_at = Column(DateTime, nullable=True, index=True)
    # "Does this brand have report capability". Not a user-toggleable switch; derived from service capability keywords in the material
    # (see brandprofile.derive_report_capability), computed during sync. Without it no report button is shown —
    # for an account selling restaurant franchises, clicking 「生成诊断报告」 ("generate diagnosis report") is meaningless and would push emails onto that hard-coded fallback template.
    report_capable = Column(Boolean, nullable=False, default=False)
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    report_snapshot = Column(Text, nullable=False, default="{}")
    # ↓ Read-only mirror; the source of truth is ai_telemetry_topics.profile_json. Apart from the syncer, everything else only reads.
    service_scope_json = Column(Text, nullable=False, default="[]")
    service_capabilities_json = Column(Text, nullable=False, default="[]")
    company_size = Column(String(length=128), nullable=False, default="")
    typical_customers_json = Column(Text, nullable=False, default="[]")
    proof_points_json = Column(Text, nullable=False, default="[]")
    # ↑ End of the read-only mirror. Buyer roles / exclusion rules are maintained by the sales side itself; the material form has no counterparts.
    default_buyer_roles_json = Column(Text, nullable=False, default="[]")
    default_exclude_rules_json = Column(Text, nullable=False, default="[]")
    summary = Column(Text, nullable=False, default="")  # Also a mirror: assembled from the items above
    # The four brand-level email style columns (email_style_key / email_tone / email_prompt_extra /
    # email_signature) were removed on 2026-08-27 together with /sales-agent/admin — that page was their
    # only write entry, so without the page they'd always be empty. Letter wording now comes entirely from brand material
    # (`email_material`) plus the recipient perspective chosen at send time. Columns dropped by f2a8c4d61e05.
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentAccountICPORM(Base):
    """One row = one brand's **long-term** target customer configuration. Overwritten in place, no history.

    "Who to find this time" all belongs to `sales_agent_recommendation_campaigns` (one row per run);
    this table only holds "how this brand filters people long-term": exclusion rules, scoring criteria, signal definitions, target stages.
    Things re-chosen each round don't belong here — rebuilding the whole row to refresh one field would reset the
    configs in the same row that are **not** re-chosen each round back to column defaults (see the fallback comment in recommend.py).

    The three columns below are historical redundancy of same-named campaign fields, only a fallback when the campaign's copy is empty, and
    campaign industries/regions are required (`_profile_for_recommendation` returns 400 if missing),
    so in practice they're never reached (the only exception: the value 「待补充」 ("to be filled") is filtered to empty by `_campaign_industries`).
    They remain because deleting them needs a migration, and the repo currently has two heads:
        target_industries_json / target_regions_json / target_company_size

    `target_company_types_json` (ideal customers) is written in three places and read only by
    `serialize_account_icp` — stuffed into the API response, not rendered by the frontend. Filtering, sorting and email generation
    never touch it. So although it's re-chosen each round, it needs no per-run history and can stay here, overwritten in place.
    """

    __tablename__ = "sales_agent_account_icps"
    __table_args__ = (Index("idx_sales_agent_account_icps_user_updated", "user_id", "updated_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    seller_profile_id = Column(Integer, ForeignKey("sales_agent_seller_profiles.id"), nullable=True, index=True)
    legacy_profile_id = Column(Integer, ForeignKey("sales_agent_customer_profiles.id"), nullable=True, index=True)
    name = Column(String(length=128), nullable=False, default="默认目标公司 ICP")
    target_company_types_json = Column(Text, nullable=False, default="[]")
    target_industries_json = Column(Text, nullable=False, default="[]")
    target_regions_json = Column(Text, nullable=False, default="[]")
    target_company_size = Column(String(length=128), nullable=False, default="")
    target_stage_json = Column(Text, nullable=False, default="[]")
    observable_signals_json = Column(Text, nullable=False, default="[]")
    pain_signals_json = Column(Text, nullable=False, default="[]")
    budget_signals_json = Column(Text, nullable=False, default="[]")
    negative_icp_json = Column(Text, nullable=False, default="[]")
    scoring_rubric_json = Column(Text, nullable=False, default="{}")
    source = Column(String(length=64), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentBuyerPersonaORM(Base):
    __tablename__ = "sales_agent_buyer_personas"
    __table_args__ = (Index("idx_sales_agent_buyer_personas_user_role", "user_id", "role_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    seller_profile_id = Column(Integer, ForeignKey("sales_agent_seller_profiles.id"), nullable=True, index=True)
    account_icp_id = Column(Integer, ForeignKey("sales_agent_account_icps.id"), nullable=True, index=True)
    role_name = Column(String(length=128), nullable=False, default="")
    titles_json = Column(Text, nullable=False, default="[]")
    decision_role = Column(String(length=64), nullable=False, default="influencer")
    goals_json = Column(Text, nullable=False, default="[]")
    kpis_json = Column(Text, nullable=False, default="[]")
    pains_json = Column(Text, nullable=False, default="[]")
    objections_json = Column(Text, nullable=False, default="[]")
    messaging_angles_json = Column(Text, nullable=False, default="[]")
    proof_needed_json = Column(Text, nullable=False, default="[]")
    content_preferences_json = Column(Text, nullable=False, default="[]")
    priority = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentRecommendationCampaignORM(Base):
    __tablename__ = "sales_agent_recommendation_campaigns"
    __table_args__ = (Index("idx_sales_agent_campaigns_user_created", "user_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sales_agent_chat_sessions.id"), nullable=True, index=True)
    seller_profile_id = Column(Integer, ForeignKey("sales_agent_seller_profiles.id"), nullable=True, index=True)
    account_icp_id = Column(Integer, ForeignKey("sales_agent_account_icps.id"), nullable=True, index=True)
    legacy_profile_id = Column(Integer, ForeignKey("sales_agent_customer_profiles.id"), nullable=True, index=True)
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    request_text = Column(Text, nullable=False, default="")
    target_industries_json = Column(Text, nullable=False, default="[]")
    target_regions_json = Column(Text, nullable=False, default="[]")
    target_company_size = Column(String(length=128), nullable=False, default="")
    service_scope_override_json = Column(Text, nullable=False, default="[]")
    limit = Column(Integer, nullable=False, default=5)
    seed_companies_json = Column(Text, nullable=False, default="[]")
    contact_preference_json = Column(Text, nullable=False, default="[]")
    status = Column(String(length=32), nullable=False, default="collecting_requirements")
    search_available = Column(Boolean, nullable=False, default=True)
    requested_limit = Column(Integer, nullable=False, default=0)
    actual_count = Column(Integer, nullable=False, default=0)
    shortfall = Column(Integer, nullable=False, default=0)
    missing_config_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentRecommendationProfileVersionORM(Base):
    """The recommendation profile saved by Sales Agent chat: one per brand, updated in place.

    This isn't a topic/brand profile, nor the source of truth for `sales_agent_seller_profiles`, and it never writes back
    to `ai_telemetry_topics.profile_json`; it stores "what conditions this brand currently uses to find customers".

    **This is a state table, not a ledger table**: each `(user_id, brand_ref_type, brand_ref_key)` keeps only one row,
    with create, edit, rename and archive all done via in-place UPDATE. Immutable snapshots of historical filter conditions live in
    `sales_agent_recommendation_campaigns`(target_industries/target_regions/
    target_company_size) — re-sending old emails reads that snapshot, so overwriting in place here doesn't mix things up.

    Legacy leftovers (DDL to be cleaned up separately; deliberately untouched for now): `version` is always 1, `event_type` has degenerated into
    a "last operation" marker, and `ux_sales_agent_rec_profile_key_version` no longer has constraint meaning.
    Un-cleaned users' legacy data may still have multiple copies per brand or multiple versions per profile_key — the code locates
    and overwrites "the latest active profile", and old rows stay frozen in place without growing.
    """

    __tablename__ = "sales_agent_recommendation_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_key", "version", name="ux_sales_agent_rec_profile_key_version"),
        Index("idx_sales_agent_rec_profiles_user_key_version", "user_id", "profile_key", "version"),
        Index("idx_sales_agent_rec_profiles_user_created", "user_id", "created_at"),
        Index("idx_sales_agent_rec_profiles_user_seller_created", "user_id", "seller_profile_id", "created_at"),
        Index("idx_sales_agent_rec_profiles_user_brand_ref_created", "user_id", "brand_ref_type", "brand_ref_key", "created_at"),
        {"comment": "Sales Agent 聊天保存的推荐画像;一个品牌一行,原地更新,不反写品牌画像"},
    )

    id = Column(Integer, primary_key=True, comment="主键 ID")
    profile_key = Column(String(length=64), nullable=False, default=lambda: str(uuid.uuid4()), comment="推荐画像稳定 key,usage 流水按它关联;不是品牌 key")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="所属用户 ID")
    seller_profile_id = Column(Integer, ForeignKey("sales_agent_seller_profiles.id"), nullable=True, comment="关联的主题品牌镜像 ID;为空表示通用推荐画像或未来聊天品牌草稿")
    brand_ref_type = Column(String(length=32), nullable=False, default="none", comment="品牌来源类型:seller_profile/none/chat_brand_draft;与 brand_ref_key 一起是本表的归一维度")
    brand_ref_key = Column(String(length=128), nullable=False, default="", comment="品牌稳定引用 key,如 seller_profile:{id};upsert 按 (user_id, brand_ref_type, brand_ref_key) 定位唯一那份画像")
    brand_name_snapshot = Column(String(length=128), nullable=False, default="", comment="保存推荐画像时看到的品牌名快照;用于历史解释,不跟随品牌改名")
    brand_context_json = Column(Text, nullable=False, default="{}", comment="预留品牌上下文快照 JSON;未来聊天多品牌草稿可放基础资料")
    version = Column(Integer, nullable=False, default=1, comment="历史遗留:改成原地更新后恒为 1,不再递增;DDL 待清理")
    event_type = Column(String(length=32), nullable=False, default="created", comment="最后一次操作:created/revised/renamed/archived;archived 表示该画像已隐藏")
    name = Column(String(length=128), nullable=False, default="", comment="用户可见的推荐画像名称")
    source = Column(String(length=32), nullable=False, default="chat", comment="画像来源:chat/ai_assisted/manual")
    service_capabilities_json = Column(Text, nullable=False, default="[]", comment="本次推荐画像里的我方服务能力 JSON 快照")
    service_scope_json = Column(Text, nullable=False, default="[]", comment="本次推荐画像里的服务/产品方向 JSON 快照")
    company_size = Column(String(length=128), nullable=False, default="", comment="我方企业/团队规模快照")
    typical_customers_json = Column(Text, nullable=False, default="[]", comment="适合服务的客户类型 JSON 快照")
    target_industries_json = Column(Text, nullable=False, default="[]", comment="本次想找的目标行业 JSON 快照;选择画像后要回显")
    target_regions_json = Column(Text, nullable=False, default="[]", comment="本次想找的目标地区 JSON 快照;选择画像后要回显")
    target_company_size = Column(String(length=128), nullable=False, default="", comment="本次想找的目标企业规模快照;选择画像后要回显")
    business_hint_json = Column(Text, nullable=False, default="[]", comment="聊天里最小业务方向/服务产品提示 JSON 快照")
    summary = Column(Text, nullable=False, default="", comment="推荐画像摘要,用于选择卡说明;不作为品牌画像事实源")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="画像首次创建时间;原地更新不刷新它,最近使用时间看 usages.used_at")


class SalesAgentRecommendationProfileUsageORM(Base):
    """Recommendation profile usage ledger.

    Replaces UPDATEs of last_used_at: every selection or use for recommendation INSERTs a ledger row, easing recent-use sorting,
    and letting outreach locate which profile this recommendation used (`recommendation_profile_for_campaign`).

    After switching the recommendation profile to in-place updates, this ledger row **must still be inserted every round** — it's the only campaign -> profile index.
    """

    __tablename__ = "sales_agent_recommendation_profile_usages"
    __table_args__ = (
        Index("idx_sales_agent_rec_profile_usages_user_used", "user_id", "used_at"),
        Index("idx_sales_agent_rec_profile_usages_key_used", "profile_key", "used_at"),
        {"comment": "Sales Agent 推荐画像使用流水表;用 INSERT 记录最近使用,替代 last_used_at UPDATE"},
    )

    id = Column(Integer, primary_key=True, comment="主键 ID")
    profile_key = Column(String(length=64), nullable=False, comment="被使用的推荐画像稳定 key")
    profile_version_id = Column(Integer, ForeignKey("sales_agent_recommendation_profile_versions.id"), nullable=False, comment="被使用的推荐画像行 ID;该行会被后续轮次原地更新,历史筛选条件看 campaign 快照")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="所属用户 ID")
    session_id = Column(Integer, ForeignKey("sales_agent_chat_sessions.id"), nullable=True, comment="触发使用的 Sales Agent 会话 ID")
    campaign_id = Column(Integer, ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, comment="关联的推荐任务 ID;只选择未推荐时可为空")
    used_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="使用时间")


class SalesAgentLeadEvidenceORM(Base):
    __tablename__ = "sales_agent_lead_evidences"
    __table_args__ = (
        Index("idx_sales_agent_evidence_campaign", "campaign_id", "company_id"),
        Index("idx_sales_agent_evidence_lead", "lead_recommendation_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, index=True)
    lead_recommendation_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    evidence_type = Column(String(length=64), nullable=False, default="firmographic")
    source_type = Column(String(length=64), nullable=False, default="manual_seed")
    source_url = Column(String(length=512), nullable=False, default="")
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    evidence_text = Column(Text, nullable=False, default="")
    scoring_category = Column(String(length=64), nullable=False, default="")
    score_delta = Column(Integer, nullable=False, default=0)
    confidence = Column(String(length=16), nullable=False, default="medium")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentCompanySourceORM(Base):
    """Company data source registry. Few rows; it exists so all companies under a source can be down-weighted or disabled as a whole."""

    __tablename__ = "sales_agent_company_sources"
    __table_args__ = (Index("idx_sales_agent_company_sources_type", "source_type", "is_enabled"),)

    id = Column(Integer, primary_key=True, index=True)
    # manual_import / public_website / search_result / directory / detection_report / authorized_upload
    source_type = Column(String(length=64), nullable=False, default="manual_import")
    source_name = Column(String(length=255), nullable=False, default="")
    source_url = Column(String(length=512), nullable=False, default="")
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    trust_level = Column(String(length=16), nullable=False, default="medium")  # high / medium / low
    weight = Column(Integer, nullable=False, default=100)  # Recommendation scoring weight, for batch down-weighting
    is_enabled = Column(Boolean, nullable=False, default=True)
    terms_note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentCompanyImportBatchORM(Base):
    """One import / collection action. When the user asks "why only 8 recommended", the gap is explained from here."""

    __tablename__ = "sales_agent_company_import_batches"
    __table_args__ = (
        Index("idx_sales_agent_company_batches_status", "status", "created_at"),
        Index("idx_sales_agent_company_batches_user", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    source_id = Column(Integer, ForeignKey("sales_agent_company_sources.id"), nullable=True)
    # seed_import / manual_import / search_discovery / website_crawl / report_import
    batch_type = Column(String(length=32), nullable=False, default="manual_import")
    request_params_json = Column(Text, nullable=False, default="{}")
    status = Column(String(length=32), nullable=False, default="pending")
    requested_count = Column(Integer, nullable=False, default=0)
    discovered_count = Column(Integer, nullable=False, default=0)
    inserted_count = Column(Integer, nullable=False, default=0)
    updated_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    blocked_count = Column(Integer, nullable=False, default=0)  # Number blocked because the source was non-compliant
    failed_count = Column(Integer, nullable=False, default=0)
    error_summary = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SalesAgentCompanyORM(Base):
    __tablename__ = "sales_agent_companies"
    __table_args__ = (
        Index("idx_sales_agent_companies_name_region", "name", "region"),
        Index("idx_sales_agent_companies_status", "status"),
        # Industry/region filtering uses this, replacing the old ilike('%x%') full-table scan
        Index("idx_sales_agent_companies_filter", "industry_primary", "province", "data_status"),
        Index("idx_sales_agent_companies_domain", "normalized_domain"),
        Index("idx_sales_agent_companies_verified", "last_verified_at"),
        Index("ux_sales_agent_companies_domain", "normalized_domain", unique=True,
              postgresql_where=text(_DOMAIN_DEDUPE_WHERE), sqlite_where=text(_DOMAIN_DEDUPE_WHERE)),
        Index("ux_sales_agent_companies_name_province", "canonical_name", "province", unique=True,
              postgresql_where=text(_NAME_DEDUPE_WHERE), sqlite_where=text(_NAME_DEDUPE_WHERE)),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(length=255), nullable=False)
    industry = Column(String(length=128), nullable=False, default="")
    region = Column(String(length=128), nullable=False, default="")
    website = Column(String(length=512), nullable=False, default="")
    employee_size = Column(String(length=128), nullable=False, default="")
    # Business registry source fields (added by migration b6f1a3c9e274, persisted by company-registry-collector (formerly enterprise-data-service))
    credit_code = Column(String(length=32), nullable=False, default="")        # Unified social credit code
    registered_capital = Column(String(length=64), nullable=False, default="")  # Registered capital (raw string)
    established_on = Column(String(length=32), nullable=False, default="")       # Founding date YYYY-MM-DD
    address = Column(String(length=512), nullable=False, default="")            # Registered/company address
    source = Column(String(length=128), nullable=False, default="")
    status = Column(String(length=64), nullable=False, default="New")

    # --- Normalized columns: raw columns stay untouched; these are indexable forms split out of them ---
    canonical_name = Column(String(length=255), nullable=False, default="")  # Brackets and whitespace removed; dedup key when there's no website
    normalized_domain = Column(String(length=255), nullable=False, default="")  # Protocol/www removed; primary dedup key
    industry_primary = Column(String(length=64), nullable=False, default="")  # First segment of industry
    industry_tags_json = Column(Text, nullable=False, default="[]")  # All tags after splitting industry
    province = Column(String(length=64), nullable=False, default="")  # Split from region
    city = Column(String(length=64), nullable=False, default="")  # Split from region
    business_summary = Column(Text, nullable=False, default="")

    # --- Source and data quality ---
    source_url = Column(String(length=512), nullable=False, default="")
    source_id = Column(Integer, ForeignKey("sales_agent_company_sources.id"), nullable=True)
    import_batch_id = Column(Integer, ForeignKey("sales_agent_company_import_batches.id"), nullable=True)
    # active / needs_review / duplicate / invalid / restricted
    data_status = Column(String(length=32), nullable=False, default="active")
    merged_into_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True)  # After duplicate merging, points to the surviving row; no physical delete
    confidence = Column(String(length=16), nullable=False, default="medium")  # high / medium / low
    last_verified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentCompanyContactORM(Base):
    __tablename__ = "sales_agent_company_contacts"
    __table_args__ = (
        Index("idx_sales_agent_contacts_company", "company_id", "is_reachable"),
        # One row per email per company; phone-only / form-only contacts have empty email and don't take part
        Index("ux_sales_agent_contacts_company_email", "company_id", "email", unique=True,
              postgresql_where=text("email <> ''"), sqlite_where=text("email <> ''")),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=False, index=True)
    contact_name = Column(String(length=128), nullable=False, default="")
    position = Column(String(length=128), nullable=False, default="")
    email = Column(String(length=255), nullable=False, default="")
    phone = Column(String(length=64), nullable=False, default="")
    contact_form_url = Column(String(length=512), nullable=False, default="")
    source = Column(String(length=128), nullable=False, default="")
    source_url = Column(String(length=512), nullable=False, default="")
    source_id = Column(Integer, ForeignKey("sales_agent_company_sources.id"), nullable=True)
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_verified_at = Column(DateTime, nullable=True)  # Written back after bounce/re-verification
    bounce_count = Column(Integer, nullable=False, default=0)  # Reaching the threshold sets is_reachable=False automatically
    confidence = Column(String(length=16), nullable=False, default="medium")  # high / medium / low
    is_reachable = Column(Boolean, nullable=False, default=False)
    consent_status = Column(String(length=32), nullable=False, default="unknown")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentCompanySignalORM(Base):
    """Reusable company-level recommendation signals.

    Division of labor with `SalesAgentLeadEvidenceORM`: evidence is an **immutable snapshot of one recommendation**
    (bound to campaign / lead, for tracing afterwards why it was recommended); a signal is a **company-level fact**
    (bound to company, updatable, expirable, reused across campaigns). Without separating them evidence rows
    = campaigns × companies per run, growing linearly, and every new campaign would recompute everything.
    """

    __tablename__ = "sales_agent_company_signals"
    __table_args__ = (
        Index("idx_sales_agent_company_signals_active", "company_id", "signal_type", "is_active"),
        Index("ux_sales_agent_company_signals_key", "company_id", "signal_type", "signal_key", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=False, index=True)
    # industry_match / region_match / content_asset / seo_signal / geo_relevance / contact_available / growth_signal
    signal_type = Column(String(length=64), nullable=False, default="firmographic")
    signal_key = Column(String(length=128), nullable=False, default="")  # Idempotency key for same-type signals
    signal_text = Column(Text, nullable=False, default="")
    signal_value = Column(String(length=255), nullable=False, default="")  # Structured value, for filtering and sorting
    source_id = Column(Integer, ForeignKey("sales_agent_company_sources.id"), nullable=True)
    source_url = Column(String(length=512), nullable=False, default="")
    # Filled only when a real report exists; empty means specific GEO / SEO issues must not be cited in recommendation reasons
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    confidence = Column(String(length=16), nullable=False, default="medium")  # high / medium / low
    extracted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # Expired signals don't go into recommendation reasons
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SalesAgentLeadRecommendationORM(Base):
    __tablename__ = "sales_agent_lead_recommendations"
    __table_args__ = (
        Index("idx_sales_agent_leads_user_status", "user_id", "status"),
        Index("idx_sales_agent_leads_profile_rank", "profile_id", "rank"),
        Index("idx_sales_agent_leads_campaign_rank", "campaign_id", "rank"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=False, index=True)
    profile_id = Column(Integer, ForeignKey("sales_agent_customer_profiles.id"), nullable=True, index=True)
    campaign_id = Column(Integer, ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, index=True)
    source_report_table = Column(String(length=64), nullable=False, default="")
    source_report_id = Column(String(length=64), nullable=False, default="")
    report_snapshot = Column(Text, nullable=False, default="{}")
    score = Column(Integer, nullable=False, default=0)
    rank = Column(Integer, nullable=False, default=0)
    reason = Column(Text, nullable=False, default="")
    status = Column(String(length=32), nullable=False, default="New")
    status_reason = Column(String(length=64), nullable=False, default="")
    status_note = Column(Text, nullable=False, default="")
    status_updated_by = Column(String(length=128), nullable=False, default="")
    status_updated_at = Column(DateTime, nullable=True)
    next_followup_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentReportShareORM(Base):
    __tablename__ = "sales_agent_report_shares"
    __table_args__ = (
        Index("ux_sales_agent_report_shares_token", "public_token", unique=True),
        Index("idx_sales_agent_report_shares_lead", "lead_id", "status"),
        Index("idx_sales_agent_report_shares_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_token = Column(String(length=64), nullable=False, default=lambda: secrets.token_urlsafe(32))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=False, index=True)
    source_report_table = Column(String(length=64), nullable=False, default="full_diagnosis_reports")
    source_report_id = Column(String(length=64), nullable=False, default="")
    status = Column(String(length=32), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


class SalesAgentLeadActivityORM(Base):
    __tablename__ = "sales_agent_lead_activities"
    __table_args__ = (Index("idx_sales_agent_activities_lead", "lead_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, index=True)
    activity_type = Column(String(length=32), nullable=False, default="note")
    from_status = Column(String(length=32), nullable=False, default="")
    to_status = Column(String(length=32), nullable=False, default="")
    reason = Column(String(length=64), nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    actor = Column(String(length=128), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentOutreachLogORM(Base):
    __tablename__ = "sales_agent_outreach_logs"
    __table_args__ = (Index("idx_sales_agent_outreach_lead_status", "lead_id", "send_status"),)

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, index=True)
    channel = Column(String(length=32), nullable=False)  # email / sms / phone_script
    subject = Column(String(length=255), nullable=False, default="")
    content = Column(Text, nullable=False, default="")
    send_status = Column(String(length=32), nullable=False, default="draft")
    approved_by = Column(String(length=128), nullable=False, default="")
    metadata_json = Column(Text, nullable=False, default="{}")
    requires_approval = Column(Boolean, nullable=False, default=True)
    has_unsubscribe = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentEmailSettingORM(Base):
    __tablename__ = "sales_agent_email_settings"
    __table_args__ = (
        UniqueConstraint("scope", "user_id", name="ux_sales_agent_email_settings_scope_user"),
        Index("idx_sales_agent_email_settings_scope_user", "scope", "user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(length=16), nullable=False, default="user")  # global / user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    sending_enabled = Column(Boolean, nullable=False, default=True)
    tracking_enabled = Column(Boolean, nullable=False, default=True)
    default_account_id = Column(Integer, ForeignKey("sales_agent_email_accounts.id"), nullable=True)
    sender_selection_policy = Column(String(length=32), nullable=False, default="default_only")
    updated_by = Column(String(length=128), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentEmailAccountORM(Base):
    __tablename__ = "sales_agent_email_accounts"
    __table_args__ = (
        Index("idx_sales_agent_email_accounts_scope_user", "scope", "user_id", "enabled"),
        Index("idx_sales_agent_email_accounts_default", "scope", "user_id", "is_default"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(length=16), nullable=False, default="user")  # global / user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    display_name = Column(String(length=128), nullable=False, default="")
    provider_type = Column(String(length=32), nullable=False, default="smtp")  # smtp / api
    api_provider = Column(String(length=32), nullable=False, default="")  # resend / sendgrid / aliyun_dm / custom
    enabled = Column(Boolean, nullable=False, default=True)
    is_default = Column(Boolean, nullable=False, default=False)
    from_email = Column(String(length=255), nullable=False, default="")
    from_name = Column(String(length=128), nullable=False, default="")
    reply_to_email = Column(String(length=255), nullable=False, default="")
    smtp_host = Column(String(length=255), nullable=False, default="")
    smtp_port = Column(Integer, nullable=False, default=465)
    smtp_security = Column(String(length=16), nullable=False, default="ssl")  # ssl / starttls / none
    smtp_username = Column(String(length=255), nullable=False, default="")
    smtp_secret_encrypted = Column(Text, nullable=False, default="")
    api_key_encrypted = Column(Text, nullable=False, default="")
    api_base_url = Column(String(length=512), nullable=False, default="")
    provider_config_json = Column(Text, nullable=False, default="{}")
    last_tested_at = Column(DateTime, nullable=True)
    last_test_status = Column(String(length=32), nullable=False, default="")
    last_test_error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentEmailDeliveryORM(Base):
    __tablename__ = "sales_agent_email_deliveries"
    __table_args__ = (
        Index("idx_sales_agent_email_deliveries_user_status", "user_id", "status"),
        Index("idx_sales_agent_email_deliveries_company_created", "company_id", "created_at"),
        Index("idx_sales_agent_email_deliveries_draft", "outreach_log_id"),
        Index("ux_sales_agent_email_deliveries_tracking", "tracking_token", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("sales_agent_company_contacts.id"), nullable=True, index=True)
    outreach_log_id = Column(Integer, ForeignKey("sales_agent_outreach_logs.id"), nullable=True, index=True)
    email_account_id = Column(Integer, ForeignKey("sales_agent_email_accounts.id"), nullable=True, index=True)
    report_share_id = Column(Integer, ForeignKey("sales_agent_report_shares.id"), nullable=True, index=True)
    message_type = Column(String(length=32), nullable=False, default="general_outreach")
    provider_type = Column(String(length=32), nullable=False, default="")
    provider_message_id = Column(String(length=255), nullable=False, default="")
    from_email = Column(String(length=255), nullable=False, default="")
    from_name = Column(String(length=128), nullable=False, default="")
    reply_to_email = Column(String(length=255), nullable=False, default="")
    recipient_email = Column(String(length=255), nullable=False, default="")
    recipient_name = Column(String(length=128), nullable=False, default="")
    subject = Column(String(length=255), nullable=False, default="")
    content_preview = Column(Text, nullable=False, default="")
    status = Column(String(length=32), nullable=False, default="draft")
    failure_reason = Column(Text, nullable=False, default="")
    send_attempted_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    tracking_token = Column(String(length=96), nullable=False, default=lambda: secrets.token_urlsafe(32))
    first_opened_at = Column(DateTime, nullable=True)
    last_opened_at = Column(DateTime, nullable=True)
    open_count = Column(Integer, nullable=False, default=0)
    first_report_visit_at = Column(DateTime, nullable=True)
    last_report_visit_at = Column(DateTime, nullable=True)
    report_visit_count = Column(Integer, nullable=False, default=0)
    read_at = Column(DateTime, nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentEmailEventORM(Base):
    __tablename__ = "sales_agent_email_events"
    __table_args__ = (
        Index("idx_sales_agent_email_events_delivery", "delivery_id", "occurred_at"),
        Index("idx_sales_agent_email_events_user_type", "user_id", "event_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(Integer, ForeignKey("sales_agent_email_deliveries.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    event_type = Column(String(length=32), nullable=False, default="")
    metadata_json = Column(Text, nullable=False, default="{}")
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# SMS outreach. **Parallel** to the four email tables above, not reused:
# SMS has things email lacks (signature/registered templates/billed segments/country-code routing), and email has things SMS lacks
# (subject/HTML/tracking pixel). Cramming them into one table would make half the columns nullable + "this column only applies to one channel".
# The only shared parts are scope normalization, credential encryption and account selection strategy — those three live in service/channel_common.py,
# not in the table structure.
# ---------------------------------------------------------------------------


class SalesAgentSmsSettingORM(Base):
    __tablename__ = "sales_agent_sms_settings"
    __table_args__ = (
        UniqueConstraint("scope", "user_id", name="ux_sales_agent_sms_settings_scope_user"),
        Index("idx_sales_agent_sms_settings_scope_user", "scope", "user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(length=16), nullable=False, default="user")  # global / user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    sending_enabled = Column(Boolean, nullable=False, default=True)
    # SMS has no tracking pixel; only carrier receipts and short-link clicks are observable, hence receipt rather than tracking.
    receipt_enabled = Column(Boolean, nullable=False, default=True)
    default_account_id = Column(Integer, ForeignKey("sales_agent_sms_accounts.id"), nullable=True)
    sender_selection_policy = Column(String(length=32), nullable=False, default="default_only")
    # SMS is billed per message and complaints suspend the signature, so rate limiting lives in the table rather than with callers:
    # 0 means unlimited; >0 means anything over is rejected (with an explicit reason), never silently dropped.
    daily_send_cap = Column(Integer, nullable=False, default=0)
    per_contact_cooldown_days = Column(Integer, nullable=False, default=0)
    updated_by = Column(String(length=128), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentSmsAccountORM(Base):
    """An SMS sending channel (= one set of platform credentials + one signature).

    `content_mode` and `region_scope` are the two extra axes of this table compared with the email account table, and also the routing basis:
    CN channels (Tencent Cloud/Aliyun) can only send registered templates -> `template`;
    international channels (Twilio/AWS SNS) can send free text -> `freeform`.
    When selecting a channel, first filter `region_scope` by the recipient number's country code, then apply default/round_robin.
    """

    __tablename__ = "sales_agent_sms_accounts"
    __table_args__ = (
        Index("idx_sales_agent_sms_accounts_scope_user", "scope", "user_id", "enabled"),
        Index("idx_sales_agent_sms_accounts_default", "scope", "user_id", "is_default"),
        Index("idx_sales_agent_sms_accounts_region", "scope", "user_id", "region_scope", "enabled"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(length=16), nullable=False, default="user")  # global / user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    display_name = Column(String(length=128), nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    is_default = Column(Boolean, nullable=False, default=False)
    # tencent_cloud / aliyun / twilio / aws_sns / custom_http
    provider = Column(String(length=32), nullable=False, default="tencent_cloud")
    content_mode = Column(String(length=16), nullable=False, default="template")  # template / freeform
    region_scope = Column(String(length=16), nullable=False, default="cn")  # cn / intl / all
    sign_name = Column(String(length=128), nullable=False, default="")  # CN registered signature, e.g. "XX Tech"
    sdk_app_id = Column(String(length=64), nullable=False, default="")  # Tencent Cloud SmsSdkAppId / empty for Aliyun
    api_key_id_encrypted = Column(Text, nullable=False, default="")  # SecretId / AccessKeyId / Twilio SID
    api_secret_encrypted = Column(Text, nullable=False, default="")  # SecretKey / AuthToken
    api_base_url = Column(String(length=512), nullable=False, default="")
    from_number = Column(String(length=32), nullable=False, default="")  # Caller number for international channels; empty for CN
    provider_config_json = Column(Text, nullable=False, default="{}")  # Echoed to the frontend in plaintext; never put secrets here
    last_tested_at = Column(DateTime, nullable=True)
    last_test_status = Column(String(length=32), nullable=False, default="")
    last_test_error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentSmsTemplateORM(Base):
    """Templates registered with the platform. Email has no counterpart — CN SMS can only send approved templates + variable slots.

    This table is a local mirror of "what the platform approved": `template_code` is the template ID issued by the platform,
    `body` is only for display and slot validation; what's actually sent follows the platform-side content. Editing `body` doesn't edit the template.
    """

    __tablename__ = "sales_agent_sms_templates"
    __table_args__ = (
        UniqueConstraint("account_id", "template_code", name="ux_sales_agent_sms_templates_account_code"),
        Index("idx_sales_agent_sms_templates_account", "account_id", "enabled", "purpose"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("sales_agent_sms_accounts.id"), nullable=False, index=True)
    template_code = Column(String(length=64), nullable=False, default="")
    name = Column(String(length=128), nullable=False, default="")
    body = Column(Text, nullable=False, default="")  # Registered original text with {1}{2} placeholders
    param_slots_json = Column(Text, nullable=False, default="[]")  # [{index, label, max_len}]
    purpose = Column(String(length=32), nullable=False, default="outreach")  # outreach / followup / test
    approved_status = Column(String(length=32), nullable=False, default="pending")  # pending / approved / rejected
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentSmsDeliveryORM(Base):
    __tablename__ = "sales_agent_sms_deliveries"
    __table_args__ = (
        Index("idx_sales_agent_sms_deliveries_user_status", "user_id", "status"),
        Index("idx_sales_agent_sms_deliveries_company_created", "company_id", "created_at"),
        Index("idx_sales_agent_sms_deliveries_draft", "outreach_log_id"),
        # Rate limiting scans by "number + time"; without this index every send is a full table scan.
        Index("idx_sales_agent_sms_deliveries_phone_created", "user_id", "recipient_phone", "created_at"),
        # The receipt webhook looks up the local row by the platform message ID.
        Index("idx_sales_agent_sms_deliveries_provider_msg", "provider", "provider_message_id"),
        Index("ux_sales_agent_sms_deliveries_short_link", "short_link_token", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("sales_agent_company_contacts.id"), nullable=True, index=True)
    outreach_log_id = Column(Integer, ForeignKey("sales_agent_outreach_logs.id"), nullable=True, index=True)
    sms_account_id = Column(Integer, ForeignKey("sales_agent_sms_accounts.id"), nullable=True, index=True)
    report_share_id = Column(Integer, ForeignKey("sales_agent_report_shares.id"), nullable=True, index=True)
    message_type = Column(String(length=32), nullable=False, default="general_outreach")
    provider = Column(String(length=32), nullable=False, default="")
    provider_message_id = Column(String(length=255), nullable=False, default="")
    sign_name = Column(String(length=128), nullable=False, default="")
    template_code = Column(String(length=64), nullable=False, default="")  # Empty in freeform mode
    template_params_json = Column(Text, nullable=False, default="[]")
    recipient_phone = Column(String(length=32), nullable=False, default="")  # E.164, with the + sign
    recipient_country = Column(String(length=8), nullable=False, default="")  # ISO country code, e.g. CN / US
    recipient_name = Column(String(length=128), nullable=False, default="")
    content_preview = Column(Text, nullable=False, default="")  # Actual sent text (in template mode an approximation after rendering)
    segments = Column(Integer, nullable=False, default=0)  # Billed segments, backfilled from platform receipts
    # draft / sending / sent / delivered / failed / disabled / no_recipient / rejected
    status = Column(String(length=32), nullable=False, default="draft")
    failure_code = Column(String(length=64), nullable=False, default="")  # Platform error code, kept verbatim for reconciliation
    failure_reason = Column(Text, nullable=False, default="")
    send_attempted_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    short_link_token = Column(String(length=96), nullable=False, default=lambda: secrets.token_urlsafe(32))
    first_click_at = Column(DateTime, nullable=True)
    last_click_at = Column(DateTime, nullable=True)
    click_count = Column(Integer, nullable=False, default=0)
    reply_count = Column(Integer, nullable=False, default=0)
    opted_out_at = Column(DateTime, nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentSmsEventORM(Base):
    __tablename__ = "sales_agent_sms_events"
    __table_args__ = (
        Index("idx_sales_agent_sms_events_delivery", "delivery_id", "occurred_at"),
        Index("idx_sales_agent_sms_events_user_type", "user_id", "event_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(Integer, ForeignKey("sales_agent_sms_deliveries.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    # send_attempt / sent / delivered / failed / clicked / replied /
    # opted_out / send_disabled / no_recipient / rejected
    event_type = Column(String(length=32), nullable=False, default="")
    metadata_json = Column(Text, nullable=False, default="{}")
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentOptionCatalogORM(Base):
    """Option catalog for profile/filter cards.

    Rows with both `user_id` and `seller_profile_id` NULL are system presets (shared by all accounts);
    rows with both set are items the user wrote in cards, private per brand (= seller_profile row).
    These options used to be literals in `guidance.py`, and custom items written by users only went into that profile row,
    never becoming selectable options next time.
    """

    __tablename__ = "sales_agent_option_catalog"
    __table_args__ = (
        Index("idx_sales_agent_option_catalog_lookup", "kind", "user_id", "seller_profile_id", "sort_order"),
        UniqueConstraint("kind", "user_id", "seller_profile_id", "value", name="ux_sales_agent_option_catalog_value"),
    )

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(length=32), nullable=False, index=True)  # capability / typical_customer / industry / region / company_size
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    seller_profile_id = Column(Integer, ForeignKey("sales_agent_seller_profiles.id"), nullable=True, index=True)
    value = Column(String(length=120), nullable=False, default="")
    label = Column(String(length=120), nullable=False, default="")
    description = Column(String(length=255), nullable=False, default="")
    source = Column(String(length=16), nullable=False, default="system")  # system / custom
    sort_order = Column(Integer, nullable=False, default=100)
    # Only used by kind="company_size": size tiers carry numeric ranges, and `_bucket_for_employee_count`
    # and the scoring `_company_size_bucket_index` both depend on it; moving only the label would break these two functions.
    min_employees = Column(Integer, nullable=True)
    max_employees = Column(Integer, nullable=True)
    archived_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentComplianceLogORM(Base):
    __tablename__ = "sales_agent_compliance_logs"
    __table_args__ = (Index("idx_sales_agent_compliance_user_created", "user_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(length=128), nullable=False, default="")
    risk_level = Column(String(length=16), nullable=False, default="low")
    decision = Column(String(length=32), nullable=False, default="allowed")  # allowed / blocked / needs_approval
    reason = Column(Text, nullable=False, default="")
    detail_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Scheduled sending (计划-2026-09-11-定时发送邮件.md §3)
#
# Four new tables; not a single column of existing tables changes. Indexes are included at table creation — the executor scans the plan table every 60 seconds,
# and the detail table grows to millions of rows a year; missing these two indexes is what would really slow the DB down (§3.5).
# ---------------------------------------------------------------------------


class SalesAgentSendScheduleORM(Base):
    """An authorization to "automatically send this batch of emails when due".

    `draft -> armed` must be triggered by explicit user confirmation; this is the compliance authorization point: when sending manually one by one
    each click is itself a record, and only after switching to "one authorization for N emails" is separate evidence needed.
    """

    __tablename__ = "sales_agent_send_schedules"
    __table_args__ = (
        # The executor runs `status='armed' AND scheduled_at <= now()+5min` every 60 seconds,
        # the most frequent query of this feature. Without it, it's a full table scan every minute.
        Index("idx_sales_agent_send_schedules_due", "status", "scheduled_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, index=True)
    channel = Column(String(length=16), nullable=False, default="email")  # Only email for now; sms reserved
    # Resolved once at arm time and pinned; the whole batch uses it. Without pinning: platform-level senders are shared across users,
    # and under round_robin each email would switch accounts — the sender lock would have nothing to lock and the daily quota no aggregation key.
    email_account_id = Column(Integer, ForeignKey("sales_agent_email_accounts.id"), nullable=True, index=True)
    scheduled_at = Column(DateTime, nullable=False)  # UTC, consistent with other time fields in the DB
    # Phase one only supports Asia/Shanghai: day boundaries use `_bj_day_start_utc()`, and output is hard-coded to Beijing time,
    # so in phase one this column is only for the record. Changing its default doesn't mean multiple timezones are supported.
    timezone = Column(String(length=64), nullable=False, default="Asia/Shanghai")
    status = Column(String(length=16), nullable=False, default="draft")  # draft/armed/running/done/cancelled/expired
    throttle_seconds = Column(Integer, nullable=False, default=30)
    # This plan's own cap. The real daily gate aggregates by (email_account_id, send date), without user_id,
    # otherwise a user could send 600 emails by creating 3 plans.
    daily_cap = Column(Integer, nullable=False, default=200)
    send_window = Column(String(length=32), nullable=False, default="09:00-18:00")  # Interpreted in timezone
    created_by_session_id = Column(Integer, ForeignKey("sales_agent_chat_sessions.id"), nullable=True, index=True)
    metadata_json = Column(Text, nullable=False, default="{}")  # User's original words, filter conditions, stats snapshot
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentSendScheduleItemORM(Base):
    """One company in a plan = one row. Makes "which ones were sent, which skipped, and why" queryable.

    Also where idempotency and concurrent claiming land: the executor claims rows one by one with `with_for_update(skip_locked=True)`.
    """

    __tablename__ = "sales_agent_send_schedule_items"
    __table_args__ = (
        # Every read and write of this table carries schedule_id, and it's the only new table in this design that grows to millions of rows
        # (about 3.65 million a year). Without it, every status change is a full table scan.
        Index("idx_sales_agent_send_schedule_items_sched_status", "schedule_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("sales_agent_send_schedules.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("sales_agent_companies.id"), nullable=True, index=True)
    # Current recipient contact; after rolling over to another address, points to the one actually sent successfully
    contact_id = Column(Integer, ForeignKey("sales_agent_company_contacts.id"), nullable=True)
    outreach_log_id = Column(Integer, ForeignKey("sales_agent_outreach_logs.id"), nullable=True, index=True)
    # pending_draft means "this company is in the list, but the draft hasn't been generated yet". Invisible to users; in the drawer it shows
    # 「待发送」 ("pending send") just like pending — drafts can't finish generating within the plan-creation request (45-second cap per email
    # vs nginx's 60 seconds).
    status = Column(String(length=16), nullable=False, default="pending_draft")  # pending_draft/pending/sending/sent/failed/skipped/cancelled
    skip_reason = Column(String(length=255), nullable=False, default="")  # Required when skipped
    email_delivery_id = Column(Integer, ForeignKey("sales_agent_email_deliveries.id"), nullable=True)
    # Rollover trail: [{contact_id, email, delivery_id, result, reason}], one entry appended per address tried.
    # "The first address bounced, the second succeeded" must be explainable on the panel; keeping only the successful result isn't enough.
    attempted_json = Column(Text, nullable=False, default="[]")
    attempt_count = Column(Integer, nullable=False, default=0)  # Capped at MAX_RECIPIENT_FALLBACK
    claimed_at = Column(DateTime, nullable=True)  # Reclaimed to pending if no progress for over 5 minutes
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesAgentEmailSuppressionORM(Base):
    """Suppression list by **email address**. Checked right before each send; a hit means skipped.

    Can't rely on `consent_status` on the contact: that's per-lead, and the same address under another
    lead would bypass it. With manual per-email confirmation human eyes catch it; with automatic sending there are no human eyes.
    """

    __tablename__ = "sales_agent_email_suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="ux_sales_agent_email_suppressions_user_email"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(length=255), nullable=False)  # Full address, lowercase-normalized
    reason = Column(String(length=32), nullable=False, default="manual")  # unsubscribed/bounced_hard/complained/manual
    source = Column(String(length=64), nullable=False, default="")  # Unsubscribe link / bounce / manually added
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesAgentSchedulerHeartbeatORM(Base):
    """Scheduler process heartbeat, one row per process.

    A dead scheduler thread shows up as "nothing sent when due, no errors, no logs", the same shape as the `GEO_SCHEDULER_LEADER`
    pitfall — and this feature's selling point is "set it and forget it", so silent failure is the worst failure mode.
    Under L2 the scheduler is a single point; if it dies all plans stop, so this table isn't optional.

    **No indexes except instance_key**: this row is updated every 60 seconds, 520k times a year.
    As long as the updated columns have no index, PG uses HOT updates and reclaims dead tuples in-page; indexing
    `last_beat_at` would instead cause index bloat on every heartbeat (§3.5).
    """

    __tablename__ = "sales_agent_scheduler_heartbeats"
    __table_args__ = (
        Index("ux_sales_agent_scheduler_heartbeats_key", "instance_key", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    instance_key = Column(String(length=255), nullable=False)  # hostname:pid; after a process restart the pid changes and old rows expire naturally
    hostname = Column(String(length=255), nullable=False, default="")
    pid = Column(Integer, nullable=False, default=0)
    last_beat_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # UTC; not indexed, see the class docstring
    live_workers = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=False, default="")  # Exception summary of the last round: exceptions don't escape the thread, but must leave a trace
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
