"""Sales Agent Pydantic AI tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.methods import usage_guardrail_check
from agents.sales_agent.runtime.payload import card_payload, model_view
from agents.sales_agent.time_parse import (
    parse_when_text,
    to_local,
    when_followup_text,
)
from agents.sales_agent.service import (
    SalesAgentError,
    agent_delivery_status,
    agent_recommended_companies,
    agent_send_schedule_status,
    append_account_icp_preferences,
    arm_send_schedule,
    brand_report_capable,
    cancel_send_schedule,
    create_customer_profile,
    list_send_schedules,
    plan_send_schedule,
    reschedule_send_schedule,
    discover_companies as svc_discover_companies,
    generate_outreach_draft,
    get_current_customer_profile as svc_get_current_customer_profile,
    get_outreach_context,
    get_report_summary,
    rank_companies as svc_rank_companies,
    recommend_companies_workflow as svc_recommend_companies_workflow,
    record_compliance_event as svc_record_compliance_event,
    resolve_active_brand_id,
    resolve_batch_lead_ids,
    start_detection_report,
    update_lead_status as svc_update_lead_status,
)


def _err(e: Exception) -> ModelRetry:
    return ModelRetry(str(e))


def _split_view(result: dict) -> ToolReturn:
    """Cards get the full data, the model gets a summary.

    The recommendation chain's return body is large; feeding it back into context as-is would
    exhaust the whole turn's wall clock (60s timeout in production on 2026-08-26). `metadata` does not go into the model input but stays on
    the ToolReturnPart, so `api._cards_from_agent_result` can still render full cards from it.
    """
    return ToolReturn(return_value=model_view(result), metadata=card_payload(result))


def _active_brand_id(deps: SalesAgentDeps) -> int | None:
    """Which brand this conversation is about: the one selected in the session -> the only active brand -> None.

    Model-side read/write tools must pass it. Without it the service layer always falls back to "the account's latest profile row",
    which in multi-brand accounts is always the most recently created one: a user asking "what are my target customer types"
    in brand A's session got B's (2026-08-26 user testing).
    """
    flow = deps.tool_state.get("lead_flow") if isinstance(getattr(deps, "tool_state", None), dict) else None
    return resolve_active_brand_id(deps.db, deps.user_id, flow if isinstance(flow, dict) else None)


def _remember_lead_flow(deps: SalesAgentDeps, **slots: Any) -> None:
    """Write the conditions the model confirmed this round back to lead_flow.

    Previously the answers the model collected only landed in RecommendationCampaign, and `tool_state["lead_flow"]` was always empty,
    so the next answer had "no step to match" in the deterministic flow and was handed back to the model by the read-only gate as a new question
    (production 2026-08-26: three rounds of answers all went through the model). With write-back the same session can hand off between the two paths.
    Persisted together by `save_message_history` when the session wraps up.
    """
    if not isinstance(getattr(deps, "tool_state", None), dict):
        return
    flow = deps.tool_state.setdefault("lead_flow", {})
    if not isinstance(flow, dict):
        return
    for key, value in slots.items():
        if isinstance(value, list):
            cleaned = [str(x).strip() for x in value if str(x).strip()]
            if cleaned:
                flow[key] = cleaned
        elif isinstance(value, int) and not isinstance(value, bool):
            if value > 0:
                flow[key] = value
        elif str(value or "").strip():
            flow[key] = str(value).strip()


async def generate_detection_report(
    ctx: RunContext[SalesAgentDeps],
    url: str = "",
    brand_name: str = "",
    industry_terms: list[str] | None = None,
    brand_terms: list[str] | None = None,
    competitors: list[str] | None = None,
) -> dict:
    """【动作·重】 Launch a GEO audit report for the target company's website via the platform's real one-click full diagnosis entry.

    Only call when the user explicitly asks to "audit/diagnose/generate a report". Ask first if there is no URL or brand name.
    "Generate a report" here means the platform audit tool generating a diagnostic report for the target company's website; the capability gate only checks whether the current user's own brand profile explicitly offers website audits, diagnostic reports, auditing or monitoring; do not open it up because the brand name or tags contain GEO/SEO/visibility/effect measurement, and do not judge by whether the recommended company itself has report capability.
    This tool does not fake audit results inside the Agent process; it only calls the main backend's real /api/check/full/generate.
    Lead reports only run the four site-side checks (lite audit), taking about 1-2 minutes; do not poll repeatedly this turn.
    Cross-engine visibility, competitor comparison, and reputation & sentiment monitoring are outside the lite audit scope and must not be mentioned in conclusions.
    """
    # Pass the current seller brand: the gate checks "whether the current user's own brand offers website audit/diagnostic reports",
    # not whether the recommended company has report generation. Without it multi-brand accounts fall back to the account-level relaxation:
    # brand A sells audits, so audits could be launched in brand B's session too.
    usage_guardrail_check(ctx.deps, "generate_detection_report", _active_brand_id(ctx.deps))
    try:
        return await start_detection_report(
            ctx.deps.db,
            ctx.deps.user_id,
            url=url,
            brand_name=brand_name,
            industry_terms=industry_terms,
            brand_terms=brand_terms,
            competitors=competitors,
        )
    except SalesAgentError as e:
        raise _err(e)


async def summarize_report(
    ctx: RunContext[SalesAgentDeps],
    source_report_table: str = "",
    source_report_id: str = "",
) -> dict:
    """Read the summary of the current user's real audit report.

    Without source_report_table/source_report_id, read the most recent report.
    Answers must be based only on real data in detection_records/full_diagnosis_reports; fabricating or changing scores is forbidden.
    When report_page is returned, the final reply must include it as a Markdown link in the chat body.
    """
    try:
        return get_report_summary(ctx.deps.db, ctx.deps.user_id, source_report_table, source_report_id)
    except SalesAgentError as e:
        raise _err(e)


async def build_customer_profile(
    ctx: RunContext[SalesAgentDeps],
    source_report_table: str = "",
    source_report_id: str = "",
    service_scope: list[str] | None = None,
    service_capabilities: list[str] | None = None,
    industries: list[str] | None = None,
    regions: list[str] | None = None,
    company_size: str = "",
    buyer_roles: list[str] | None = None,
    exclude_rules: list[str] | None = None,
    typical_customers: list[str] | None = None,
) -> dict:
    """【写】 Generate/save the SellerProfile seller service profile, compatible with legacy profile output.

    SellerProfile describes the current user's own company/team: professional capabilities (service_capabilities), the service being sold this time (service_scope), size, typical customers and default buyer personas.
    service_capabilities and service_scope must be passed separately; industries/regions may only serve as legacy API compatibility or target filter references for AccountICP/Campaign, not as "who the user is". Without a report, label it only as a manual/preliminary profile and never fabricate audit conclusions.
    After returning, the same recommendation flow must pass the returned id as recommend_companies.profile_id, and seller_profile.id/account_icp.id as seller_profile_id/account_icp_id respectively; do not drop the just-saved profile and recommend with empty params.
    """
    usage_guardrail_check(ctx.deps, "build_customer_profile")
    try:
        result = create_customer_profile(
            ctx.deps.db,
            ctx.deps.user_id,
            source_report_table=source_report_table,
            source_report_id=source_report_id,
            service_scope=service_scope,
            service_capabilities=service_capabilities,
            industries=industries,
            regions=regions,
            company_size=company_size,
            buyer_roles=buyer_roles,
            exclude_rules=exclude_rules,
            typical_customers=typical_customers,
        )
        # Record the profile ID just saved this round, as a same-turn/cross-turn fallback for later recommend_companies calls.
        # This is not "new recommendations default to the historical profile"; it only prevents the model from dropping the ID after the user confirmed this round's profile.
        seller_profile = result.get("seller_profile") if isinstance(result.get("seller_profile"), dict) else {}
        account_icp = result.get("account_icp") if isinstance(result.get("account_icp"), dict) else {}
        ctx.deps.tool_state["current_customer_profile"] = {
            "profile_id": result.get("profile_id") or result.get("id"),
            "seller_profile_id": result.get("seller_profile_id") or seller_profile.get("id"),
            "account_icp_id": result.get("account_icp_id") or account_icp.get("id"),
        }
        return result
    except SalesAgentError as e:
        raise _err(e)


async def get_current_customer_profile(
    ctx: RunContext[SalesAgentDeps],
    profile_id: int | None = None,
) -> dict:
    """【读】 Read the current logged-in user's latest or specified service profile.

    Must be called first when the user asks "my profile / current ICP / my target customers / who am I / my company / what services can I offer / service scope / company size / professional capabilities / typical customers".
    When answering, prefer the returned seller_profile.summary and explain in natural language what the user's company is like, how many people/how large it serves, its professional capabilities and which customer types it suits; AccountICP is the target company profile, BuyerPersona the buyer persona, and latest_campaign only the most recent recommendation filters.
    This tool only reads SellerProfile/AccountICP/BuyerPersona/legacy profile; it never creates or modifies profiles.
    """
    try:
        return svc_get_current_customer_profile(
            ctx.deps.db,
            ctx.deps.user_id,
            profile_id=profile_id,
            seller_profile_id=_active_brand_id(ctx.deps),
        )
    except SalesAgentError as e:
        raise _err(e)


async def discover_companies(
    ctx: RunContext[SalesAgentDeps],
    profile_id: int | None = None,
    campaign_id: int | None = None,
    limit: int = DEFAULT_RECOMMENDATIONS,
    seed_companies: list[dict[str, Any]] | None = None,
) -> ToolReturn:
    """【写】 Generate the candidate company pool.

    Prefer passing campaign_id, which finds real companies via web search or public directories by RecommendationCampaign.target_industries/target_regions; seed_companies, when given, are only persisted as a manual/authorized source.
    SellerProfile.service_scope is only used to explain the pitch angle and must not be treated as the target industry; company-level public emails, phones/hotlines and contact forms that can be scraped from company websites/public directories must be filled in.
    Bypassing restrictions of platforms like Qichacha/Tianyancha/Tungee is forbidden, as is scraping personal mobile numbers/personal emails/WeChat.
    """
    usage_guardrail_check(ctx.deps, "discover_companies")
    try:
        return _split_view(svc_discover_companies(ctx.deps.db, ctx.deps.user_id, profile_id=profile_id, limit=limit, seed_companies=seed_companies, campaign_id=campaign_id))
    except SalesAgentError as e:
        raise _err(e)


async def rank_companies(
    ctx: RunContext[SalesAgentDeps],
    profile_id: int | None = None,
    campaign_id: int | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
    limit: int = DEFAULT_RECOMMENDATIONS,
) -> ToolReturn:
    """【写】 Rank candidate companies and generate Top N recommended leads and LeadEvidence.

    Must filter and rank by RecommendationCampaign's target industries/service regions, then explain recommendation reasons with SellerProfile.service_scope; do not recommend generic-industry companies.
    Only output a "final recommendation" when the candidate company's website/name matches a real report's URL/brand; otherwise only output a "preliminary recommendation".
    Do not claim specific problems such as missing llms.txt or insufficient structured data for companies that were not audited.
    """
    usage_guardrail_check(ctx.deps, "rank_companies")
    try:
        return _split_view(svc_rank_companies(ctx.deps.db, ctx.deps.user_id, profile_id=profile_id, source_report_table=source_report_table, source_report_id=source_report_id, limit=limit, campaign_id=campaign_id))
    except SalesAgentError as e:
        raise _err(e)


async def recommend_companies(
    ctx: RunContext[SalesAgentDeps],
    profile_id: int | None = None,
    seller_profile_id: int | None = None,
    account_icp_id: int | None = None,
    campaign_id: int | None = None,
    limit: int = DEFAULT_RECOMMENDATIONS,
    service_scope: list[str] | None = None,
    industries: list[str] | None = None,
    regions: list[str] | None = None,
    company_size: str = "",
    typical_customers: list[str] | None = None,
    target_company_size: str = "",
    service_scope_override: list[str] | None = None,
    question_options: list[dict[str, Any]] | None = None,
    seed_companies: list[dict[str, Any]] | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
) -> ToolReturn:
    """【写】 Deterministic orchestration tool for recommending N companies.

    When the user says "recommend companies / leads / clients / customers / client list / customer list / Top N", this tool must be called first rather than explaining in chat; call it even if current information is insufficient, so the tool returns need_more_info / next_question / question_card for the frontend A2UI to render; use the default recommendation count when no number is given.
    For a new conversation/new recommendation request, if the current conversation has not explicitly confirmed the service profile, industries or regions, also call this tool first; it returns need_more_info / question_card to continue collecting info. These fields are written to RecommendationCampaign only after the user explicitly confirms; do not silently reuse service/industry/region from historical profiles in the DB.
    If build_customer_profile was just called in the same recommendation flow, you must pass profile_id=<the returned id>; if the result has seller_profile.id/account_icp.id, you must also pass seller_profile_id/account_icp_id. Do not pass only service_scope/industries/regions, otherwise the workflow treats it as missing the just-saved profile of the current conversation.
    SellerProfile/service_scope is what the user sells; AccountICP is the target companies likely to buy; BuyerPersona is the buyer persona; LeadEvidence explains why a specific company is recommended.
    This tool always runs: current filters -> RecommendationCampaign -> web search or manual/authorized seed persistence -> ranking with evidence chain.
    Returns requested_limit/actual_count/shortfall/search_available/missing_config/website_count; company details and tables are only sent to frontend cards, not back to the model. If status=need_more_info, ask the user naturally using next_question directly; do not show field names, function names, JSON or internal object names; ask at most 1 question per round when collecting info, and do not expand questions into several. question_card is only a UI hint for the frontend to render buttons/cards and must not be expanded into multiple questions in natural language.
    If candidate answers can be generated from the current conversation context, put 3-5 clickable answers into question_options in the format {"label":"候选答案","value":"实际提交文本","desc":"为什么适合"} (label: candidate answer, value: text actually submitted, desc: why it fits); these are candidate answers to the single current question, not multiple new questions.
    After a successful recommendation the company list is rendered by frontend cards; the body only gives a one-sentence overview; do not paste a company table in the reply or list companies one by one.
    When the user asks this round to "only recommend companies with websites", you must state truthfully that recommendations do not hard-filter by website (a website is only a ranking bonus), and say that website_count companies in this batch have one; do not stay silent.
    """
    usage_guardrail_check(ctx.deps, "recommend_companies")
    try:
        # Prefer the profile ID just saved in the current run/session, fixing profile=null when the model forgets to pass profile_id.
        current_profile = ctx.deps.tool_state.get("current_customer_profile") if isinstance(ctx.deps.tool_state, dict) else None
        if isinstance(current_profile, dict):
            profile_id = profile_id or current_profile.get("profile_id")
            seller_profile_id = seller_profile_id or current_profile.get("seller_profile_id")
            account_icp_id = account_icp_id or current_profile.get("account_icp_id")
        lead_flow = ctx.deps.tool_state.get("lead_flow") if isinstance(ctx.deps.tool_state, dict) else None
        if isinstance(lead_flow, dict):
            company_size = company_size or str(lead_flow.get("seller_team") or "")
            if not service_scope and isinstance(lead_flow.get("service_scope"), list):
                service_scope = lead_flow.get("service_scope")
            # Compatibility with historical sessions lacking a `service_scope` slot: only used as a fallback for this recommendation's context,
            # and must not be written back to `seller_capabilities`, otherwise the service direction would pollute the professional capability profile.
            if not service_scope and isinstance(lead_flow.get("seller_capabilities"), list):
                service_scope = lead_flow.get("seller_capabilities")
            if not typical_customers and isinstance(lead_flow.get("typical_customers"), list):
                typical_customers = lead_flow.get("typical_customers")
            industries = industries or lead_flow.get("target_industries") if isinstance(lead_flow.get("target_industries"), list) else industries
            regions = regions or lead_flow.get("target_regions") if isinstance(lead_flow.get("target_regions"), list) else regions
            target_company_size = target_company_size or str(lead_flow.get("target_company_size") or "")
        # The default candidate pool is read from sales_agent_companies / sales_agent_company_contacts;
        # seed_companies is kept only as the manual authorized-import channel explicitly passed via API/tools.
        # Conditions the model confirmed this round are written back to lead_flow so the deterministic flow can tell which card the next answer is for.
        _remember_lead_flow(
            ctx.deps,
            service_scope=service_scope,
            seller_team=company_size,
            typical_customers=typical_customers,
            target_company_size=target_company_size,
            target_industries=industries,
            target_regions=regions,
            requested_limit=limit,
        )
        result = svc_recommend_companies_workflow(
            ctx.deps.db,
            ctx.deps.user_id,
            profile_id=profile_id,
            limit=limit,
            service_scope=service_scope,
            industries=industries,
            regions=regions,
            company_size=company_size,
            typical_customers=typical_customers,
            seed_companies=seed_companies,
            source_report_table=source_report_table,
            source_report_id=source_report_id,
            seller_profile_id=seller_profile_id,
            account_icp_id=account_icp_id,
            campaign_id=campaign_id,
            target_company_size=target_company_size,
            service_scope_override=service_scope_override,
            question_options=question_options,
            session_id=ctx.deps.session_id,
        )
        return _split_view(result)
    except SalesAgentError as e:
        raise _err(e)


async def get_outreach_copy_context(
    ctx: RunContext[SalesAgentDeps],
    lead_id: int,
    channel: str = "email",
    contact_id: int | None = None,
) -> dict:
    """【读】 Read the context needed to generate outreach drafts, without saving a draft.

    Call this tool before generating outreach content. Copy may only use the returned user service profile, current target filters, recommended companies, compliant contact info and real report context;
    without a real report, do not write audit scores, grades or specific website problems.
    """
    try:
        return get_outreach_context(ctx.deps.db, ctx.deps.user_id, lead_id=lead_id, channel=channel, contact_id=contact_id)
    except SalesAgentError as e:
        raise _err(e)


async def generate_outreach_copy(
    ctx: RunContext[SalesAgentDeps],
    lead_id: int,
    channel: str = "email",
    contact_id: int | None = None,
    tone: str = "professional",
    subject: str = "",
    content: str = "",
    template_code: str = "",
    template_params: list[str] | None = None,
) -> dict:
    """【写】 Save an LLM-generated external outreach draft, but never send it.

    Before calling, subject/content must be generated from the user service profile, current target industries/regions, recommended companies, contacts and report context returned by get_outreach_copy_context.
    Do not let the backend apply a fixed industry template; content must not be empty. Copy must be in the current user's own voice; do not introduce Vigilath or write "this is Vigilath".
    Only email/sms/phone_script/contact_form/linkedin_script drafts are supported; all drafts have requires_approval=true.
    Do not append an AI-generated marker, draft approval note or unsubscribe note to email bodies; other channels must include an unsubscribe/do-not-contact note.
    Contacts of unknown source, unsubscribed or blacklisted are blocked; without a real report, do not write specific conclusions like audit scores, grades or missing llms.txt.

    **SMS has two modes, depending on which channel it goes through**:
    - Free text (international channels such as Twilio): write content as usual, no more than 320 characters; the backend appends “回复TD退订” ("reply TD to unsubscribe").
    - Registered templates (CN channels, Tencent Cloud/Aliyun): carriers only allow dispatching approved templates; free copy cannot be sent.
      In that case you must pass template_code (the template code under "registered templates" on the admin page) and template_params
      (variable values in template slot order, e.g. ["示例清洁", "62"], i.e. a company name like "Example Cleaning" and a number); content is only a preview for the user.
      If unsure which template to use or a variable is missing, state directly what is missing and where to configure it; do not make up variables.
    """
    usage_guardrail_check(ctx.deps, "generate_outreach_copy")
    metadata: dict = {}
    if channel == "sms" and str(template_code or "").strip():
        metadata = {
            "sms_content_mode": "template",
            "sms_template_code": str(template_code).strip(),
            "sms_template_params": [str(p) for p in (template_params or [])],
        }
    try:
        return generate_outreach_draft(
            ctx.deps.db,
            ctx.deps.user_id,
            lead_id=lead_id,
            channel=channel,
            contact_id=contact_id,
            tone=tone,
            subject=subject,
            content=content,
            metadata=metadata or None,
        )
    except SalesAgentError as e:
        raise _err(e)


async def remember_preference(
    ctx: RunContext[SalesAgentDeps],
    exclude: list[str] | None = None,
    signals: list[str] | None = None,
) -> dict:
    """【写·轻】 Record exclusions and demand signals the user mentioned, effective directly in the next recommendation.

    Call it when the user says "no education industry", "don't recommend ones without websites", "Yangtze River Delta only", "skip low-budget ones", passing exclusions in exclude;
    when the user describes "what kind of customers are worth recommending" (e.g. "ones with a blog", "hiring for SEO"), pass signals. Appends with dedup, never overwrites existing entries.
    **Record it as soon as it is said; do not wait for the user to ask you to "remember"**: otherwise it is forgotten next round and the user has to repeat the same thing.

    Industry / region / company name exclusions are filtered out directly during recommendation; other types are recorded for manual screening and scoring reference.
    """
    usage_guardrail_check(ctx.deps, "remember_preference")
    try:
        return append_account_icp_preferences(
            ctx.deps.db,
            ctx.deps.user_id,
            exclude=exclude,
            signals=signals,
            seller_profile_id=_active_brand_id(ctx.deps),
        )
    except SalesAgentError as e:
        raise _err(e)


async def get_recommended_companies(
    ctx: RunContext[SalesAgentDeps],
    company_name: str = "",
    industry: str = "",
    region: str = "",
    status: str = "",
    days: int = 0,
    limit: int = 20,
) -> dict:
    """【读】 Look up which companies were recommended before. Does not re-recommend; only reads history.

    Call it when the user asks "which companies were recommended before", "the batch you found last time", "was company X recommended", "which ones haven't been followed up".
    Only use recommend_companies to run a new batch; that one actually runs a filter.

    company_name / industry / region are all fuzzy matches; status takes a lead status; days limits to the last N days.
    Returns each company's match score, status, and whether it has a diagnostic report. When there are no records say truthfully "nothing recommended yet"; do not make things up.
    """
    usage_guardrail_check(ctx.deps, "get_recommended_companies")
    try:
        data = agent_recommended_companies(
            ctx.deps.db, ctx.deps.user_id,
            company_name=company_name, industry=industry, region=region,
            status=status, days=days, limit=limit,
        )
    except SalesAgentError as e:
        raise _err(e)
    # This history card is the same component as the recommendation card, also with a "generate report" button. In the recommendation chain
    # `report_capable` hangs off `seller_profile`; history queries do not filter by brand and lack that part,
    # so one is provided separately here; without it the frontend would fall back on "field missing" and show the button.
    data["report_capable"] = brand_report_capable(ctx.deps.db, ctx.deps.user_id, _active_brand_id(ctx.deps))
    return data


async def get_outreach_delivery_status(
    ctx: RunContext[SalesAgentDeps],
    company_name: str = "",
    lead_id: int = 0,
    status: str = "",
    days: int = 0,
    limit: int = 20,
    channel: str = "",
) -> dict:
    """【读】 Look up real outreach delivery status (**both email and SMS included**): whether sent, whether opened/clicked, whether the report was opened.

    Call it when the user asks "show delivery status", "was company X sent", "has anyone looked", "which ones failed"; do not answer from memory,
    and never say "no delivery records" or "we don't send emails/SMS on your behalf": after the user confirms each one, the platform really sends via the sender mailbox or SMS channel they configured.

    **When the user asks about a specific target (e.g. "has xxx@x.com been emailed", "has company X Co., Ltd. been emailed"), you must put that email/company name
    into company_name before querying**, and the answer must mention it by name: a query without filters returns the account-wide total,
    and answering a specific question with it gives the right number but the wrong answer. If 0 rows come back, say truthfully nothing has been sent to it yet.

    company_name fuzzy-matches company name, recipient email or mobile number; status can be sent / delivered / failed / rejected / sending;
    days limits to the last N days; channel set to email or sms shows only one channel, empty means both.
    Returns summary (sent/failed/opened/report visited/short-link clicks) + summary.by_channel (per-channel breakdown) and deliveries details,
    each detail row carrying a channel field.

    **Answers must state the channel split**, e.g. "12 sent today: 9 emails, 3 SMS"; saying only "sent 12 emails"
    makes users think the SMS did not go out. When there are no records say truthfully "nothing sent yet"; do not make things up.
    """
    usage_guardrail_check(ctx.deps, "get_outreach_delivery_status")
    try:
        role = str(getattr(ctx.deps, "user_role", "user") or "user")
        return agent_delivery_status(
            ctx.deps.db,
            ctx.deps.user_id,
            company_name=company_name,
            lead_id=lead_id or None,
            status=status,
            days=days,
            limit=limit,
            admin=role in {"admin", "operator"},
            channel=channel,
        )
    except SalesAgentError as e:
        raise _err(e)


async def update_lead_status(
    ctx: RunContext[SalesAgentDeps],
    lead_id: int,
    status: str,
    reason: str = "manual",
    note: str = "",
    next_followup_at: str = "",
) -> dict:
    """【写】 Update the lead status and write an activity record."""
    usage_guardrail_check(ctx.deps, "update_lead_status")
    try:
        return svc_update_lead_status(ctx.deps.db, ctx.deps.user_id, lead_id=lead_id, status=status, reason=reason, note=note, next_followup_at=next_followup_at)
    except SalesAgentError as e:
        raise _err(e)


async def record_compliance_event(
    ctx: RunContext[SalesAgentDeps],
    action: str,
    risk_level: str = "medium",
    decision: str = "blocked",
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> dict:
    """【写】 Record a Sales Agent compliance event.

    Call this tool to record blocked when the user asks to fake reports, bypass third-party restrictions, scrape private contacts, mass-send SMS/email, or evade unsubscribes.
    """
    return svc_record_compliance_event(ctx.deps.db, ctx.deps.user_id, action=action, risk_level=risk_level, decision=decision, reason=reason, detail=detail or {})


# ---------------------------------------------------------------- scheduled sending
#
# Five tools around one chain: schedule (draft) -> user confirms (armed) -> the scheduler process sends at the time.
# **The model never sends itself and cannot skip confirmation**: `draft -> armed` is the compliance authorization point (§4.1).


# Follow-up wording lives in `time_parse.when_followup_text`, shared by the chat tools and REST endpoints (4.4);
# see the comment there.


def _active_schedule_id(deps: SalesAgentDeps) -> int:
    """When the user says "cancel today's send plan" there is no id; find the most recent unfinished one.

    Only picks among draft/armed/running; done/cancelled/expired do not count, otherwise "stop sending"
    would cancel a plan that finished yesterday and reply "nothing left to cancel", and the user would think today's batch was stopped.
    """
    data = list_send_schedules(deps.db, deps.user_id, limit=20)
    for row in data.get("schedules") or []:
        if str((row or {}).get("status") or "") in ("draft", "armed", "running"):
            return int(row.get("id") or 0)
    return 0


async def schedule_outreach_send(
    ctx: RunContext[SalesAgentDeps],
    when_text: str,
    lead_ids: list[int] | None = None,
    campaign_id: int = 0,
    throttle_seconds: int = 30,
    daily_cap: int = 200,
) -> dict:
    """【写,但**不发信**】 Schedule a batch of recommended companies to be emailed one by one at a set time, returning a list preview for user confirmation.

    Call it when the user says "send this batch at 3 pm", "send at 9 tomorrow morning", "schedule sending", "send now".
    Pass **the fragment of the user's own words** as `when_text` (e.g. "下午三点" (3 pm), "明天上午十点" (10 am tomorrow), "现在就" (right now)); do not convert it to
    a date string yourself: the platform converts it in Asia/Shanghai, and your conversion can easily be off by a day.
    Pass the recommended company ids to send as `lead_ids`. **When unsure which batch the user's "this batch" refers to, pass `campaign_id`
    (the batch number) instead of picking lead_ids from the recommendation list yourself**: when a session has recommended two batches that list is flat,
    and what you pick is often a slice mixing both. The platform translates the batch number into the complete lead_ids. Passing neither means
    no company is selected, and it will be rejected.

    This step only builds the list; **the status stays "pending confirmation" and not a single email is sent**. The result includes: how many can be sent, per-item reasons
    for those that cannot, and the expected finish time of the whole batch. When replying to the user, cover all three:
      1. **Echo the full time for the user to verify** ("today 15:00 (Beijing time)"); do not just reply "OK";
      2. **State when the whole batch finishes**, not just the start time: "starts at 15:00, 12 emails 30 seconds apart,
         done around 15:06". Users hearing "send at 15:00" assume "done by 15:00"; 100 companies take 50 minutes;
      3. For those that cannot be sent, **give the reason for each company**; never say vaguely "some companies cannot be sent".
    Finally ask "confirm?", and after the user confirms call confirm_scheduled_send. **The confirmation step cannot be skipped.**

    When the time is not pinned down (e.g. "send later", "send to everyone with an email"), it returns `need_time=True` and a follow-up question;
    just ask that, and **do not pick a time yourself**. You must have a schedule_id before saying "scheduled".
    """
    usage_guardrail_check(ctx.deps, "schedule_outreach_send")
    when = parse_when_text(when_text)
    if when.needs_followup:
        return {"need_time": True, "reason": when.reason, "ask": when_followup_text(when)}
    ids = [int(x) for x in (lead_ids or [])]
    if not ids and campaign_id:
        # The model actually has no lead ids: the whitelist in `runtime/payload.py` strips leads/lead_id/
        # campaign_id, and it only sees summaries. Given a batch number, the server translates it into lead_ids,
        # sharing the same interpretation (`resolve_batch_lead_ids`) as the dialog path, so the two never compute separately.
        ids = resolve_batch_lead_ids(ctx.deps.db, ctx.deps.user_id, [int(campaign_id)])
    try:
        result = plan_send_schedule(
            ctx.deps.db,
            ctx.deps.user_id,
            ids,
            when.utc_at,
            throttle_seconds=throttle_seconds,
            daily_cap=daily_cap,
            session_id=getattr(ctx.deps, "session_id", None),
            campaign_id=campaign_id or None,
            user_text=when_text,
        )
    except SalesAgentError as e:
        raise _err(e)
    if when.utc_at and when.utc_at <= datetime.utcnow():
        # arm would hard-reject (§4.1 step 1b), but by then the user has already confirmed once. Ask here first,
        # so they are not told it cannot be sent only after confirming.
        result["time_warning"] = (
            f"{to_local(when.utc_at).strftime('%m月%d日 %H:%M')} 已经过去了。"
            "确认后也发不出去,换一个以后的时间吧。"
        )
    return result


async def confirm_scheduled_send(ctx: RunContext[SalesAgentDeps], schedule_id: int) -> dict:
    """【写】 After the user confirms, **formally schedule** the plan (pending confirmation -> scheduled). The platform sends one by one at the time.

    **Only call when the user explicitly confirms ("confirm", "OK", "send it like this")**; this step is the compliance authorization point:
    the user authorizes the scope "email these N companies". The list preview must have been shown to them before calling.

    On success say "**scheduled**", never "sent": not a single email has gone out at this point.
    The returned hint includes the batch finish time and notes like "a minute or two late if content cannot be prepared in time"; relay them as-is.
    """
    usage_guardrail_check(ctx.deps, "confirm_scheduled_send")
    try:
        return arm_send_schedule(ctx.deps.db, ctx.deps.user_id, int(schedule_id), actor="agent")
    except SalesAgentError as e:
        raise _err(e)


async def reschedule_send(ctx: RunContext[SalesAgentDeps], schedule_id: int, when_text: str) -> dict:
    """【写】 Change the send time of a plan that has not started. Plans already sending or finished cannot be changed; create a new one instead.

    Use it when the user says "move it to four", "postpone an hour"; **do not substitute "cancel and recreate"**: that would discard all already generated
    drafts, and the user's "move it to four" obviously does not mean "delete the 12 emails just written and rewrite them".
    Pass the original words fragment as `when_text` as well. After changing, echo the new time for the user to verify.
    """
    usage_guardrail_check(ctx.deps, "reschedule_send")
    when = parse_when_text(when_text)
    if when.needs_followup:
        return {"need_time": True, "reason": when.reason, "ask": when_followup_text(when, verb="改到")}
    try:
        return reschedule_send_schedule(ctx.deps.db, ctx.deps.user_id, int(schedule_id), when.utc_at)
    except SalesAgentError as e:
        raise _err(e)


async def cancel_scheduled_send(ctx: RunContext[SalesAgentDeps], schedule_id: int = 0, reason: str = "") -> dict:
    """【写】 Cancel a scheduled send plan. **Only stops what has not been sent; sent emails cannot be recalled.**

    Call it when the user says "cancel today's send plan", "stop sending", "hold off". If `schedule_id` is unknown pass 0,
    and the most recent unfinished plan is found automatically.

    The reply must state both numbers clearly: how many were already sent and how many were stopped. When cancelled mid-sending,
    **the current email finishes before stopping**, and this must be explained too: the user expecting "stop immediately" while one more goes out is an incident.
    """
    usage_guardrail_check(ctx.deps, "cancel_scheduled_send")
    target = int(schedule_id or 0) or _active_schedule_id(ctx.deps)
    if not target:
        return {"cancelled_items": 0, "hint": "你现在没有待发送的定时计划,没有需要取消的。"}
    try:
        return cancel_send_schedule(ctx.deps.db, ctx.deps.user_id, target, reason=reason)
    except SalesAgentError as e:
        raise _err(e)


async def get_send_schedule_status(ctx: RunContext[SalesAgentDeps], schedule_id: int = 0) -> dict:
    """【读】 Look up scheduled send plans: what time, whether sent, how many sent, how many failed.

    Call it when the user asks "has the 3 o'clock batch gone out", "what scheduled tasks do I have", "how far along is the scheduled send"; **do not answer from memory**.
    Returned times are already Beijing time; state them as-is. When there are no plans say truthfully "there are no scheduled send plans now"; do not make things up.
    """
    usage_guardrail_check(ctx.deps, "get_send_schedule_status")
    try:
        return agent_send_schedule_status(ctx.deps.db, ctx.deps.user_id, int(schedule_id or 0) or None)
    except SalesAgentError as e:
        raise _err(e)


TOOLS = [
    generate_detection_report,
    summarize_report,
    build_customer_profile,
    get_current_customer_profile,
    discover_companies,
    rank_companies,
    recommend_companies,
    get_outreach_copy_context,
    generate_outreach_copy,
    update_lead_status,
    get_outreach_delivery_status,
    get_recommended_companies,
    remember_preference,
    record_compliance_event,
    # Scheduled sending. Missing these lines shows up as "the tools are written but the model cannot see them": no error, the feature just does not exist.
    schedule_outreach_send,
    confirm_scheduled_send,
    reschedule_send,
    cancel_scheduled_send,
    get_send_schedule_status,
]
