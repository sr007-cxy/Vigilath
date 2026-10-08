"""Sales Agent API request/response models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictStr

from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS


class ChatIn(BaseModel):
    message: str
    session_id: StrictStr | None = None
    new_session: bool = False
    page_context: dict[str, Any] | None = None


class SalesAgentUndoIn(BaseModel):
    session_id: StrictStr | None = None
    confirmed: bool = False
    checkpoint_id: int | None = None


class GenerateReportIn(BaseModel):
    url: str = ""
    brand_name: str = ""
    industry_terms: list[str] = Field(default_factory=list)
    brand_terms: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)


class ReportSummaryIn(BaseModel):
    source_report_table: str = ""
    source_report_id: str = ""


class CustomerProfileIn(BaseModel):
    source_report_table: str = ""
    source_report_id: str = ""
    service_scope: list[str] = Field(default_factory=list)
    service_capabilities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    company_size: str = ""
    buyer_roles: list[str] = Field(default_factory=list)
    exclude_rules: list[str] = Field(default_factory=list)
    typical_customers: list[str] = Field(default_factory=list)
    manual_profile: dict[str, Any] = Field(default_factory=dict)


class CompanySeed(BaseModel):
    name: str
    industry: str = ""
    region: str = ""
    website: str = ""
    employee_size: str = ""
    source: str = "manual"
    contact_name: str = ""
    position: str = ""
    email: str = ""
    phone: str = ""
    contact_form_url: str = ""
    contact_source: str = ""
    source_url: str = ""
    confidence: str = "medium"
    consent_status: str = "unknown"


class DiscoverCompaniesIn(BaseModel):
    profile_id: int | None = None
    campaign_id: int | None = None
    limit: int = DEFAULT_RECOMMENDATIONS
    seed_companies: list[CompanySeed] = Field(default_factory=list)


class RankCompaniesIn(BaseModel):
    profile_id: int | None = None
    campaign_id: int | None = None
    source_report_table: str = ""
    source_report_id: str = ""
    limit: int = DEFAULT_RECOMMENDATIONS


class RecommendCompaniesIn(BaseModel):
    profile_id: int | None = None
    seller_profile_id: int | None = None
    account_icp_id: int | None = None
    campaign_id: int | None = None
    limit: int = DEFAULT_RECOMMENDATIONS
    service_scope: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    company_size: str = ""
    typical_customers: list[str] = Field(default_factory=list)
    target_company_size: str = ""
    service_scope_override: list[str] = Field(default_factory=list)
    seed_companies: list[CompanySeed] = Field(default_factory=list)
    source_report_table: str = ""
    source_report_id: str = ""


class SalesAgentRecommendationProfileIn(BaseModel):
    """Append-write request for saving the “本次推荐画像” ("this run's recommendation profile") from chat."""

    name: str = ""
    seller_profile_id: int | None = None
    brand_ref_type: str = "none"
    brand_ref_key: str = ""
    brand_name_snapshot: str = ""
    brand_context: dict[str, Any] = Field(default_factory=dict)
    source: str = "ai_assisted"
    flow: dict[str, Any] = Field(default_factory=dict)
    session_id: StrictStr | None = None


class SalesAgentRecommendationProfileRenameIn(BaseModel):
    """Recommendation profile rename request; the server expresses it by INSERTing a new version, not UPDATEing the old one."""

    name: str


class SalesAgentRecommendationProfileUsageIn(BaseModel):
    """Recommendation profile usage ledger request; only INSERTs usage."""

    profile_version_id: int | None = None
    session_id: StrictStr | None = None
    campaign_id: int | None = None


class OutreachDraftIn(BaseModel):
    lead_id: int
    channel: str = "email"
    contact_id: int | None = None
    tone: str = "professional"
    subject: str = ""
    content: str = ""


class OutreachAutoDraftIn(BaseModel):
    lead_id: int
    channel: str = "email"
    contact_id: int | None = None
    tone: str = "professional"
    style_key: str = "leijun_pitch"
    variant_count: int = 1
    force_regenerate: bool = False


class OutreachConfirmIn(BaseModel):
    confirmed: bool = True
    note: str = ""


# ---------------------------------------------------------------- Scheduled sending (4.4)
#
# Time always uses one of two fields, **with no default**:
#   - `when_text`: a fragment of the user's own words (「明天上午十点」 "10am tomorrow"), parsed by the platform in Asia/Shanghai;
#   - `scheduled_at`: a concrete moment from the picker; without a timezone it's read as Beijing time.
# If both are empty no time was given, and the endpoint asks a follow-up rather than picking one for the user — scheduled and immediate sending are two different authorizations.


class SendScheduleCreateIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)
    when_text: str = ""
    scheduled_at: str = ""
    throttle_seconds: int = 30
    daily_cap: int = 200
    session_id: StrictStr | None = None
    campaign_id: int | None = None
    # Send by batch: don't pass lead_ids; pass batch numbers, resolved into lead_ids by the server.
    # The interpretation of 「这批」 ("this batch") is given neither to the model nor to the frontend — see `resolve_batch_lead_ids`.
    campaign_ids: list[int] = Field(default_factory=list)
    # True = schedule right after creating (draft -> armed). The batch dialog path uses this: the user already saw
    # the time and company count in the dialog; making them click a second confirmation just splits one authorization in two.
    confirm: bool = False


class SendSchedulePreviewIn(BaseModel):
    """Trial computation: with these batches and this time, how many companies can be sent and how many can't. **Read-only, not persisted.**"""

    campaign_ids: list[int] = Field(default_factory=list)
    lead_ids: list[int] = Field(default_factory=list)
    when_text: str = ""
    scheduled_at: str = ""
    session_id: StrictStr | None = None


class SendScheduleConfirmIn(BaseModel):
    confirmed: bool = True


class SendSchedulePatchIn(BaseModel):
    """Change time / drop items. Both can be done in one request, or each separately."""

    when_text: str = ""
    scheduled_at: str = ""
    drop_item_ids: list[int] = Field(default_factory=list)


class SalesAgentEmailSettingsIn(BaseModel):
    sending_enabled: bool = True
    tracking_enabled: bool = True
    default_account_id: int | None = None
    sender_selection_policy: str = "default_only"


class SalesAgentEmailAccountIn(BaseModel):
    display_name: str = ""
    provider_type: str = "smtp"
    api_provider: str = ""
    preset_key: str = ""
    enabled: bool = True
    is_default: bool = False
    from_email: str = ""
    from_name: str = ""
    reply_to_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    smtp_username: str = ""
    smtp_secret: str = ""
    api_key: str = ""
    api_base_url: str = ""
    provider_config: dict[str, Any] = Field(default_factory=dict)


class SalesAgentEmailAccountPatchIn(BaseModel):
    display_name: str | None = None
    provider_type: str | None = None
    api_provider: str | None = None
    preset_key: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to_email: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = None
    smtp_username: str | None = None
    smtp_secret: str | None = None
    api_key: str | None = None
    api_base_url: str | None = None
    provider_config: dict[str, Any] | None = None


class SalesAgentEmailTestIn(BaseModel):
    to_email: str = ""
    subject: str = ""
    content: str = ""


class SalesAgentSmsSettingsIn(BaseModel):
    sending_enabled: bool = True
    receipt_enabled: bool = True
    default_account_id: int | None = None
    sender_selection_policy: str = "default_only"
    daily_send_cap: int = 0
    per_contact_cooldown_days: int = 0


class SalesAgentSmsAccountIn(BaseModel):
    display_name: str = ""
    provider: str = "tencent_cloud"
    # When empty, derived from the provider's preset (CN -> template/cn, international -> freeform/intl).
    content_mode: str = ""
    region_scope: str = ""
    enabled: bool = True
    is_default: bool = False
    sign_name: str = ""
    sdk_app_id: str = ""
    api_key_id: str = ""
    api_secret: str = ""
    api_base_url: str = ""
    from_number: str = ""
    provider_config: dict[str, Any] = Field(default_factory=dict)


class SalesAgentSmsAccountPatchIn(BaseModel):
    display_name: str | None = None
    provider: str | None = None
    content_mode: str | None = None
    region_scope: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    sign_name: str | None = None
    sdk_app_id: str | None = None
    # Empty secret = unchanged. The frontend echoes has_* booleans rather than ciphertext, so "not filled" must mean "keep as-is".
    api_key_id: str | None = None
    api_secret: str | None = None
    api_base_url: str | None = None
    from_number: str | None = None
    provider_config: dict[str, Any] | None = None


class SalesAgentSmsTemplateIn(BaseModel):
    template_code: str = ""
    name: str = ""
    body: str = ""
    param_slots: list[dict[str, Any]] = Field(default_factory=list)
    purpose: str = "outreach"
    approved_status: str = "pending"
    enabled: bool = True


class SalesAgentSmsTestIn(BaseModel):
    to_phone: str = ""
    # Free-text channels use content; template channels use template_code + template_params (in slot order).
    # If both are empty, the service reports an explicit error rather than silently picking a template to send here.
    content: str = ""
    template_code: str = ""
    template_params: list[str] = Field(default_factory=list)


class SalesAgentSmsTemplatePatchIn(BaseModel):
    template_code: str | None = None
    name: str | None = None
    body: str | None = None
    param_slots: list[dict[str, Any]] | None = None
    purpose: str | None = None
    approved_status: str | None = None
    enabled: bool | None = None


class LeadStatusIn(BaseModel):
    status: str
    reason: str = "manual"
    note: str = ""
    next_followup_at: str = ""
    actor: str = ""


class ComplianceLogIn(BaseModel):
    action: str
    risk_level: str = "medium"
    decision: str = "blocked"
    reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class SalesAgentOptionIn(BaseModel):
    """Add a custom option on the hidden admin page /sales-agent/admin."""

    kind: str
    value: str
    seller_profile_id: int | None = None
    label: str = ""
    description: str = ""


class SalesAgentOptionPatchIn(BaseModel):
    """Change a custom option's display name / description.

    There's no `value` — it's the key of the extraction word list and the literal stored in historical profiles and recommendation tasks;
    changing it would point old data at a nonexistent option. Absent fields mean "unchanged".
    """

    label: str | None = None
    description: str | None = None


# The brand create/update inputs (`SalesAgentBrandIn` / `SalesAgentBrandPatchIn`) were removed on 2026-08-27
# together with /sales-agent/admin — brands are synced from brand material, with no manual create/update entry.
