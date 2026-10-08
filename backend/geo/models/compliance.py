"""Compliance audit center — P0 foundation data model.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md (v2 "one gate, three stages").
Four tables:

  - compliance_lexicons        lexicons (library level, including category semantic red lines rules_text)
  - compliance_lexicon_terms   entries (three levels ban/warn/evid + suggested replacement)
  - compliance_scan_results    result snapshot of each scan (lexicon versions / hit details / verdict)
  - compliance_audit_logs      append-only audit log (shared foundation for all gates; UPDATE/DELETE forbidden)

The topic-level compliance profile (ymyl_tier / qualifications / disclaimer templates) is stored in ai_telemetry_topics.compliance_profile_json,
no separate table — see chapter 4 of the design doc.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
)

from geo.database import Base


class ComplianceLexiconORM(Base):
    """Lexicon (library level).

    Libraries with tier=generic are always enabled for all categories; others are enabled by matching the topic's industry tier.
    rules_text holds category semantic red lines that cannot be expressed as entries (inducing minors / fabricated admission rates …),
    serving as checkpoints for manual first review + prompt injection on the generation side + derivation of in-process LLM classification dimensions.
    """
    __tablename__ = "compliance_lexicons"
    __table_args__ = (Index("idx_comp_lex_code", "code", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(length=48), nullable=False)
    name = Column(String(length=128), nullable=False)
    # generic | beauty | medical | finance | food | health_food | education | realty | maternal
    tier = Column(String(length=24), nullable=False, default="generic")
    rules_text = Column(Text, nullable=False, default="")
    version = Column(String(length=16), nullable=False, default="2026.07")
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ComplianceLexiconTermORM(Base):
    __tablename__ = "compliance_lexicon_terms"
    __table_args__ = (Index("idx_comp_term_lexicon", "lexicon_id"),)

    id = Column(Integer, primary_key=True, index=True)
    lexicon_id = Column(Integer, ForeignKey("compliance_lexicons.id", ondelete="CASCADE"), nullable=False)
    term = Column(String(length=64), nullable=False)
    level = Column(String(length=8), nullable=False)        # ban / warn / evid
    # Categories aligned with the self-check list: superlatives / absolute quantification / price scarcity marketing / authority endorsement / medical efficacy / time-limited promises …
    category = Column(String(length=32), nullable=False, default="")
    # Suggested replacement copy; empty = no substitute, rejection only annotates (e.g. 「根治」 ("radical cure") is deleted outright for non-medical categories)
    fix = Column(String(length=128), nullable=False, default="")
    aliases_json = Column(Text, nullable=False, default="[]")  # list[str] variant spellings
    active = Column(Boolean, nullable=False, default=True)
    # Candidate pool (design doc 5.7 one-way feedback): pending does not participate in scanning; admin adopts → active,
    # rejects → rejected (kept to prevent repeated recommendations). External entries never go straight to ban (decision record 1).
    status = Column(String(length=12), nullable=False, default="active")  # active/pending/rejected
    source = Column(String(length=24), nullable=False, default="seed")    # seed/manual/external/platform_rules
    origin_note = Column(String(length=256), nullable=False, default="")  # Recommendation source context (external check riskTips etc.)


class ComplianceScanResultORM(Base):
    """One row per scan — multiple scans of the same doc (generation / submission / outbound rescan) each keep a snapshot row."""
    __tablename__ = "compliance_scan_results"
    __table_args__ = (
        Index("idx_comp_scan_doc", "doc_id"),
        Index("idx_comp_scan_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    doc_id = Column(Integer, ForeignKey("topic_generated_docs.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(Integer, nullable=False)
    # Trigger hook: generated / submit_review / publish_auto / publish_manual / manual
    hook = Column(String(length=24), nullable=False, default="manual")
    # {code: version} used in this scan — an audit requirement, tracing the lexicon versions at the time
    lexicon_versions_json = Column(Text, nullable=False, default="{}")
    # Hit details [{term, level, category, fix, sources:[lexicon_code], offsets:[[start,end]]}]
    hits_json = Column(Text, nullable=False, default="[]")
    ban_count = Column(Integer, nullable=False, default=0)
    warn_count = Column(Integer, nullable=False, default=0)
    evid_count = Column(Integer, nullable=False, default=0)
    # Missing mandatory labels [{label, hint}]
    missing_disclaimers_json = Column(Text, nullable=False, default="[]")
    verdict = Column(String(length=24), nullable=False, default="pass")  # pass / returned / evidence_required
    # External soft signals (design doc 5.7, internal first then external): {service, labels, risk_level, risk_words,
    # request_ids, status: ok/degraded/skipped, elapsed_ms} — only reference for manual first review, not part of the verdict
    external_json = Column(Text, nullable=False, default="{}")
    scanned_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ComplianceAlertORM(Base):
    """In-process monitoring alerts (P1) — produced by compliance_monitor's periodic scans of AI answers.

    Alerts with detected_by=llm always have status=open pending manual confirmation (conservative rollout, design doc 11.2);
    confirm opens a remediation ticket (compliance_tickets), dismiss is the false-positive terminal state.
    Double dedup: dedup_key is unique (same response, same type), and the business layer additionally suppresses duplicate alerts by
    (topic, query, engine, alert_type), only appending recurrences.
    """
    __tablename__ = "compliance_alerts"
    __table_args__ = (
        Index("idx_comp_alert_topic_status", "topic_id", "status"),
        Index("idx_comp_alert_response", "response_id"),
        Index("idx_comp_alert_dedup", "dedup_key", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, nullable=False, index=True)
    response_id = Column(Integer, nullable=True)     # Answer-level alerts; may be null for P2 off-site sources
    run_id = Column(Integer, nullable=True)
    # (engine, query) redundant snapshot from the response — retest match key + channel display
    engine = Column(String(length=32), nullable=False, default="")
    query = Column(Text, nullable=False, default="")
    # fabricated_credential / fake_case / disparage_competitor / appearance_anxiety
    # / unauthorized_channel / category_redline / banned_wording
    alert_type = Column(String(length=32), nullable=False)
    level = Column(Integer, nullable=False, default=1)          # 1 / 2 / 3
    detected_by = Column(String(length=16), nullable=False, default="lexicon")  # lexicon/llm/manual
    confidence = Column(Float, nullable=True)                   # Self-reported by the LLM; NULL for lexicon
    quote = Column(Text, nullable=False, default="")            # Violation excerpt — retest comparison key
    reason = Column(Text, nullable=False, default="")
    channel = Column(String(length=64), nullable=False, default="")
    # {source_url, answer_excerpt, scan_hits|llm_raw, lexicon_versions, recurrences:[{response_id, at}]}
    evidence_json = Column(Text, nullable=False, default="{}")
    status = Column(String(length=16), nullable=False, default="open")  # open/confirmed/dismissed/closed
    # No FK (tickets.alert_id already has a reverse FK; setting both would create a circular dependency); backfilled after confirm
    ticket_id = Column(Integer, nullable=True)
    dedup_key = Column(String(length=64), nullable=False)       # r{response_id}:{alert_type}
    handled_by_id = Column(Integer, nullable=True)
    handled_by_name = Column(String(length=64), nullable=False, default="")
    found_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)


class ComplianceTicketORM(Base):
    """Remediation ticket (loop carrier) — opened in the same transaction when an alert is confirmed.

    State machine: open → in_progress → pending_retest → (2 consecutive clean retests) closed;
    past deadline → overdue; still present on retest → back to in_progress with clean_streak reset to zero.
    """
    __tablename__ = "compliance_tickets"
    __table_args__ = (
        Index("idx_comp_ticket_topic", "topic_id"),
        Index("idx_comp_ticket_no", "ticket_no", unique=True),
        Index("idx_comp_ticket_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticket_no = Column(String(length=16), nullable=False)       # CZ-MMDD-NN
    alert_id = Column(Integer, ForeignKey("compliance_alerts.id", ondelete="CASCADE"),
                      nullable=False, unique=True)
    topic_id = Column(Integer, nullable=False)
    action = Column(String(length=16), nullable=False)          # limit_fix / takedown / suspend
    level = Column(Integer, nullable=False, default=1)
    deadline_at = Column(DateTime, nullable=False)
    assignee_id = Column(Integer, nullable=True)
    assignee_name = Column(String(length=64), nullable=False, default="")
    status = Column(String(length=16), nullable=False, default="open")
    # open / in_progress / pending_retest / closed / overdue
    resolution = Column(Text, nullable=False, default="")
    retest_json = Column(Text, nullable=False, default="[]")
    # [{at, run_id, response_id, still_present, method}]
    clean_streak = Column(Integer, nullable=False, default=0)
    overdue_notified = Column(Boolean, nullable=False, default=False)
    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_action_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)


class ComplianceMonitorStateORM(Base):
    """Tiny KV state for in-process monitoring (cursors like response_watermark)."""
    __tablename__ = "compliance_monitor_state"

    key = Column(String(length=48), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ComplianceAuditLogORM(Base):
    """Append-only audit log — shared foundation for all gates. Business code only INSERTs; UPDATE/DELETE forbidden.

    event_type:admission | qualification_expiry | scan | block | release | review
                | claim_verify | lexicon_upgrade | alert | ticket_open | ticket_action
                | retest | ticket_close | export
    """
    __tablename__ = "compliance_audit_logs"
    __table_args__ = (
        Index("idx_comp_audit_topic", "topic_id"),
        Index("idx_comp_audit_created", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String(length=32), nullable=False)
    object_type = Column(String(length=16), nullable=False, default="")   # topic/doc/response/alert/ticket/lexicon
    object_id = Column(Integer, nullable=True)
    actor_type = Column(String(length=16), nullable=False, default="system")  # system/user/admin/external
    actor_id = Column(Integer, nullable=True)
    actor_name = Column(String(length=64), nullable=False, default="")
    detail_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AbuseReportORM(Base):
    """Report/complaint tickets — item 13 of the algorithm filing correction list (the "Interim Measures for the Management of Generative AI Services"
    Article 15 requires providing the public with a reporting channel and handling reports promptly).

    Plan in docs/09-软著与专利/备案补正/02-方案与代办台账.md §4.6.

    **Anonymous submission must be allowed**: reporters are not necessarily our users, and a login wall would make this entry useless.
    So reporter_user_id is nullable, and contact_email + report_no let reporters come back to check progress.

    State machine (four-step loop, mapping one-to-one to the list's required "report → accept → handle → respond"):
        received (accepted, on submission) → processing (in progress) → resolved (handled)
                                                        └→ rejected (not accepted)
    Both terminal states must write reply (the response to the reporter) — without a response the loop is not closed,
    so both the resolve / reject endpoints require a non-empty reply.

    target_trace_no is the interface with the content-labeling suite: reporters can directly enter the number
    we stamp on content (VGL-… text / VGI-… image), and the back office jumps in one click to the provenance page to locate who generated it
    and when — this chain is the complete evidence for "traceable + actionable".
    """
    __tablename__ = "abuse_reports"
    __table_args__ = (
        Index("idx_abuse_report_no", "report_no", unique=True),
        Index("idx_abuse_status", "status"),
        Index("idx_abuse_created", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Ticket number VGR-<entity code>-<yyyymmdd>-<id six digits>, used in the receipt email and the lookup page
    report_no = Column(String(length=48), nullable=False)

    # content_illegal illegal content / infringement infringement / privacy personal information
    # / model model issue / experience user experience / other other
    category = Column(String(length=24), nullable=False, default="other")
    target_url = Column(String(length=1024), nullable=False, default="")
    target_trace_no = Column(String(length=48), nullable=False, default="")
    description = Column(Text, nullable=False, default="")

    contact_name = Column(String(length=64), nullable=False, default="")
    contact_email = Column(String(length=128), nullable=False, default="")
    contact_phone = Column(String(length=32), nullable=False, default="")
    # Single attachment (screenshot / PDF evidence), disk path relative to data/abuse_reports/; empty without attachment
    attachment_path = Column(String(length=512), nullable=False, default="")
    attachment_name = Column(String(length=255), nullable=False, default="")

    reporter_user_id = Column(Integer, nullable=True)   # Only when submitted while logged in
    client_ip = Column(String(length=64), nullable=False, default="")

    status = Column(String(length=16), nullable=False, default="received")
    # Promised handling deadline: 7 calendar days from acceptance (this is stated publicly on the page and in the receipt email)
    due_at = Column(DateTime, nullable=True)
    handling = Column(Text, nullable=False, default="")   # Internal record of handling measures
    reply = Column(Text, nullable=False, default="")      # Response to the reporter (required in terminal states)
    handler_id = Column(Integer, nullable=True)
    handler_name = Column(String(length=64), nullable=False, default="")
    accepted_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
