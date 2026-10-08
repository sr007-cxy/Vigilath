"""Compliance audit center P1 -- in-flight monitoring periodic sweep + re-test loop + timeout escalation.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md chapter 6.

Architecture: backend periodic sweep (one round every 10 minutes), telemetry-service untouched:
  - Lexicon layer (deterministic): run compliance_scanner.scan_text on new answers after the watermark;
    hit -> banned_wording alert (level mapped per term ban->3 evid->2 warn->1);
  - Semantic layer (LLM, conservative rollout): violation classification on answers with hit=True (brand mentioned),
    capped per round by GEO_COMPLIANCE_LLM_PER_SWEEP (default 30); when the budget runs out the watermark stops at
    the last fully processed answer; detections are always status=open pending manual confirmation, never auto-ticketed;
  - Re-test: pending_retest tickets find the latest post-remediation answer by (topic, query, engine),
    and compare normalized violation excerpts to see if they remain; N consecutive clean rounds (default 2) auto-close the ticket;
  - Timeout: past the deadline set overdue; email is sent only once.

Entry points:
  - scheduler job: content_scheduler.attach() mounts sweep_tick() every */10
  - CLI (via crontab when prod has no scheduler leader):
      python -m geo.services.compliance_monitor [--backfill N]

Idempotency: watermark + unique alerts.dedup_key + suppression on same (topic,query,engine,type),
safe to run twice.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import AiTelemetryResponseORM, AiTelemetryTopicORM, metric_brand_terms
from geo.models.compliance import (
    ComplianceAlertORM, ComplianceLexiconORM, ComplianceMonitorStateORM,
    ComplianceTicketORM,
)
from geo.models.user import UserORM
from geo.services import compliance_audit, compliance_scanner
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

WATERMARK_KEY = "response_watermark"
SWEEP_BATCH = int(os.environ.get("GEO_COMPLIANCE_SWEEP_BATCH", "500"))
LLM_PER_SWEEP = int(os.environ.get("GEO_COMPLIANCE_LLM_PER_SWEEP", "30"))
RETEST_CLEAN_ROUNDS = int(os.environ.get("GEO_COMPLIANCE_RETEST_CLEAN_ROUNDS", "2"))

# Remediation deadline (hours): L3 is the strictest
DEADLINE_HOURS = {
    3: int(os.environ.get("GEO_COMPLIANCE_DEADLINE_L3_HOURS", "24")),
    2: int(os.environ.get("GEO_COMPLIANCE_DEADLINE_L2_HOURS", "72")),
    1: int(os.environ.get("GEO_COMPLIANCE_DEADLINE_L1_HOURS", "168")),
}

# Violation type -> default level (design doc 6.1)
ALERT_TYPE_LEVELS = {
    "fabricated_credential": 3,
    "fake_case": 2,
    "disparage_competitor": 2,
    "appearance_anxiety": 2,
    "unauthorized_channel": 1,
    "category_redline": 2,
}
_HIT_LEVEL_TO_ALERT = {"ban": 3, "evid": 2, "warn": 1}

ALERT_TYPE_LABELS_ZH = {
    "fabricated_credential": "幻觉 · 编造资质 / 认证 / 数据",
    "fake_case": "幻觉 · 虚假用户案例",
    "disparage_competitor": "贬损竞品类文案",
    "appearance_anxiety": "容貌 / 身材焦虑文案",
    "unauthorized_channel": "非授权渠道信息干扰",
    "category_redline": "类目语义红线",
    "banned_wording": "违禁表述(词库命中)",
}

ACTION_LABELS_ZH = {"limit_fix": "预警限时修改", "takedown": "内容下架", "suspend": "暂停投放"}


# -- Watermark ------------------------------------------------------------------

def _get_watermark(db: Session) -> Optional[int]:
    row = db.get(ComplianceMonitorStateORM, WATERMARK_KEY)
    if row is None:
        return None
    try:
        return int(row.value)
    except (TypeError, ValueError):
        return None


def _set_watermark(db: Session, value: int) -> None:
    row = db.get(ComplianceMonitorStateORM, WATERMARK_KEY)
    if row is None:
        db.add(ComplianceMonitorStateORM(key=WATERMARK_KEY, value=str(value)))
    else:
        row.value = str(value)


def _excluded_topics() -> set[int]:
    """Exclude demo topics (following the report center demo protection precedent); env comma-separated ids."""
    raw = os.environ.get("GEO_COMPLIANCE_SWEEP_EXCLUDE_TOPICS", "")
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# -- Alert creation (double dedup safeguard) ------------------------------------------------

def _pick_quote(answer: str, offsets: list[list[int]], cap: int = 120) -> str:
    """Use the sentence containing the first hit as the violation excerpt (re-test comparison key)."""
    if not offsets:
        return (answer or "")[:cap]
    start = offsets[0][0]
    seg_start = max((answer.rfind(p, 0, start) for p in "。!?;\n"), default=-1) + 1
    seg_end = len(answer)
    for p in "。!?;\n":
        idx = answer.find(p, start)
        if idx != -1:
            seg_end = min(seg_end, idx + 1)
    return answer[seg_start:seg_end][:cap]


def _create_alert(
    db: Session,
    *,
    resp: AiTelemetryResponseORM,
    topic: AiTelemetryTopicORM,
    alert_type: str,
    level: int,
    detected_by: str,
    quote: str,
    reason: str,
    confidence: Optional[float] = None,
    extra_evidence: Optional[dict] = None,
) -> Optional[ComplianceAlertORM]:
    """Create an alert; suppressed if dedup_key matches or an active alert already exists for the same (topic,query,engine,type)."""
    dedup_key = f"r{resp.id}:{alert_type}"
    if db.query(ComplianceAlertORM.id).filter(
            ComplianceAlertORM.dedup_key == dedup_key).first():
        return None

    existing = (
        db.query(ComplianceAlertORM)
          .filter(ComplianceAlertORM.topic_id == topic.id)
          .filter(ComplianceAlertORM.query == (resp.query or ""))
          .filter(ComplianceAlertORM.engine == (resp.engine or ""))
          .filter(ComplianceAlertORM.alert_type == alert_type)
          .filter(ComplianceAlertORM.status.in_(("open", "confirmed")))
          .first()
    )
    if existing is not None:
        try:
            ev = json.loads(existing.evidence_json or "{}")
        except Exception:  # noqa: BLE001
            ev = {}
        rec = ev.setdefault("recurrences", [])
        if not any(r.get("response_id") == resp.id for r in rec if isinstance(r, dict)):
            rec.append({"response_id": resp.id, "at": datetime.utcnow().isoformat()})
            existing.evidence_json = json.dumps(ev, ensure_ascii=False)
            compliance_audit.record(
                db, topic_id=topic.id, event_type="alert", object_type="alert",
                object_id=existing.id, actor_type="system", actor_name="事中监测",
                detail={"action": "recurrence", "response_id": resp.id,
                        "alert_type": alert_type},
            )
        return None

    evidence = {
        "source_url": resp.source_url or "",
        "answer_excerpt": (resp.answer or "")[:500],
        **(extra_evidence or {}),
    }
    alert = ComplianceAlertORM(
        topic_id=topic.id, response_id=resp.id, run_id=resp.run_id,
        engine=resp.engine or "", query=resp.query or "",
        alert_type=alert_type, level=level, detected_by=detected_by,
        confidence=confidence, quote=quote[:1000], reason=reason[:1000],
        channel=resp.engine or "",
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        status="open", dedup_key=dedup_key,
    )
    db.add(alert)
    db.flush()
    compliance_audit.record(
        db, topic_id=topic.id, event_type="alert", object_type="alert",
        object_id=alert.id, actor_type="system", actor_name="事中监测",
        detail={"alert_type": alert_type, "level": level, "detected_by": detected_by,
                "response_id": resp.id, "quote": quote[:200]},
    )
    return alert


# -- Semantic layer: LLM violation classification -------------------------------------------

_CLASSIFY_SYSTEM = (
    "你是品牌内容合规审核员。判断一段 AI 引擎生成的回答中,针对指定品牌是否存在"
    "下列违规内容。只报确有依据的项,宁缺毋滥。"
)


def _classify_answer(
    answer: str, brand: str, aliases: list[str], rules_texts: list[str],
    provider: str, api_key: str,
) -> list[dict]:
    """LLM violation classification, returns [{type, quote, reason, confidence}]; raises on failure, handled by the caller."""
    from geo.services.content_generator import (
        DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEFAULT_TIMEOUT,
        OPENROUTER_DEEPSEEK_MODEL, OPENROUTER_URL,
    )

    rules_block = ""
    if rules_texts:
        rules_block = "\n\n该品牌所属类目的语义红线(命中报 category_redline):\n" + \
            "\n".join(t.strip() for t in rules_texts if t.strip())[:1500]

    user_prompt = f"""品牌:{brand}(别名:{"、".join(aliases) if aliases else "无"})

违规类型定义:
- fabricated_credential:编造资质 / 认证 / 检测数据(查无出处的认证、夸大倍数的数据)
- fake_case:虚构用户案例 / 编造用户故事
- disparage_competitor:贬损竞品(踩别的品牌抬该品牌)
- appearance_anxiety:制造容貌 / 身材焦虑、贬低原生条件
- unauthorized_channel:引导去非授权渠道 / 代购 / 所谓同源低价渠道
- category_redline:命中下方类目语义红线{rules_block}

待审回答(截断):
{answer[:3000]}

输出严格 JSON:{{"violations": [{{"type": "上述类型码", "quote": "违规原文摘录(50字内)", "reason": "判定理由(30字内)", "confidence": 0到1}}]}}
没有违规输出 {{"violations": []}}。"""

    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        model = DEEPSEEK_MODEL
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        url = OPENROUTER_URL
        model = OPENROUTER_DEEPSEEK_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com", "X-Title": "GEO Compliance Monitor",
        }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    r = post_and_record(url, payload, headers, provider=provider,
                        feature="compliance.monitor", timeout=DEFAULT_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    content = r.json()["choices"][0]["message"]["content"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {"violations": []}
    out = []
    for v in data.get("violations") or []:
        if not isinstance(v, dict):
            continue
        vtype = str(v.get("type") or "")
        if vtype not in ALERT_TYPE_LEVELS:
            continue
        out.append({
            "type": vtype,
            "quote": str(v.get("quote") or "")[:200],
            "reason": str(v.get("reason") or "")[:200],
            "confidence": float(v.get("confidence") or 0.5),
        })
    return out


def _rules_texts_for_topic(db: Session, industry: str) -> list[str]:
    tier = compliance_scanner.tier_of(industry or "")
    rows = (
        db.query(ComplianceLexiconORM.rules_text)
          .filter(ComplianceLexiconORM.enabled.is_(True))
          .filter(ComplianceLexiconORM.tier.in_(["generic", tier]))
          .all()
    )
    return [r[0] for r in rows if r[0]]


# -- Main flow 1: sweep -------------------------------------------------------

def sweep(db: Session) -> dict:
    """Scan new answers after the watermark: lexicon layer in full, semantic layer capped. Returns stats."""
    watermark = _get_watermark(db)
    if watermark is None:
        # First launch: watermark = current max id, do not backfill existing data (avoids an alert storm)
        from sqlalchemy import func
        watermark = db.query(func.max(AiTelemetryResponseORM.id)).scalar() or 0
        _set_watermark(db, watermark)
        db.commit()
        log.info("[compliance-monitor] watermark initialized to %d (no backfill)", watermark)
        return {"processed": 0, "alerts": 0, "llm_used": 0, "watermark": watermark}

    excluded = _excluded_topics()
    rows = (
        db.query(AiTelemetryResponseORM, AiTelemetryTopicORM)
          .join(AiTelemetryTopicORM,
                AiTelemetryTopicORM.id == AiTelemetryResponseORM.topic_id)
          .filter(AiTelemetryResponseORM.id > watermark)
          .filter(AiTelemetryResponseORM.answer != "")
          .order_by(AiTelemetryResponseORM.id.asc())
          .limit(SWEEP_BATCH)
          .all()
    )
    if not rows:
        return {"processed": 0, "alerts": 0, "llm_used": 0, "watermark": watermark}

    try:
        from geo.services.content_generator import _resolve_provider
        provider, _model, api_key = _resolve_provider()
    except Exception:  # noqa: BLE001
        provider, api_key = None, ""
    if not provider:
        log.info("[compliance-monitor] no LLM key — semantic layer skipped this sweep")

    rules_cache: dict[int, list[str]] = {}
    n_alerts, llm_used, last_done = 0, 0, watermark

    for resp, topic in rows:
        if topic.id in excluded:
            last_done = resp.id
            continue

        # Lexicon layer (deterministic, full)
        try:
            result = compliance_scanner.scan_text(db, resp.answer or "", topic.industry or "")
            hits = result["hits"]
            if hits:
                # Take the strictest hit (by mapped value, ban->3 > evid->2 > warn->1)
                strictest = max(hits, key=lambda h: _HIT_LEVEL_TO_ALERT.get(h["level"], 0))
                level = _HIT_LEVEL_TO_ALERT.get(strictest["level"], 1)
                terms = "、".join(h["term"] for h in hits[:8])
                if _create_alert(
                    db, resp=resp, topic=topic,
                    alert_type="banned_wording", level=level, detected_by="lexicon",
                    quote=_pick_quote(resp.answer or "", strictest["offsets"]),
                    reason=f"词库命中:{terms}",
                    extra_evidence={"scan_hits": hits,
                                    "lexicon_versions": result["lexicon_versions"]},
                ):
                    n_alerts += 1
        except Exception:  # noqa: BLE001
            log.exception("[compliance-monitor] lexicon scan failed response=%s", resp.id)

        # Semantic layer (capped; budget exhausted -> watermark stays at the previous one, this one is rescanned next round)
        if resp.hit and provider:
            if llm_used >= LLM_PER_SWEEP:
                break
            # Aliases + product names from the materials (since 2026-09-22 product names also count as brand mentions, same metric definition as detect_hit)
            aliases = [t for t in metric_brand_terms(topic) if t != (topic.target or "").strip()]
            if topic.id not in rules_cache:
                rules_cache[topic.id] = _rules_texts_for_topic(db, topic.industry or "")
            try:
                detections = _classify_answer(
                    resp.answer or "", topic.target or topic.name or "",
                    aliases, rules_cache[topic.id], provider, api_key)
                llm_used += 1
                for det in detections:
                    if _create_alert(
                        db, resp=resp, topic=topic,
                        alert_type=det["type"],
                        level=ALERT_TYPE_LEVELS[det["type"]],
                        detected_by="llm",
                        quote=det["quote"] or _pick_quote(resp.answer or "", []),
                        reason=det["reason"], confidence=det["confidence"],
                        extra_evidence={"llm_raw": det},
                    ):
                        n_alerts += 1
            except Exception:  # noqa: BLE001
                llm_used += 1   # Failures count against the budget too, so bad samples cannot stall it
                log.exception("[compliance-monitor] llm classify failed response=%s", resp.id)

        last_done = resp.id

    _set_watermark(db, last_done)
    db.commit()
    log.info("[compliance-monitor] sweep: processed→%d, alerts=%d, llm=%d",
             last_done, n_alerts, llm_used)
    return {"processed": len(rows), "alerts": n_alerts, "llm_used": llm_used,
            "watermark": last_done}


# -- Main flow 2: re-test loop ------------------------------------------------------

def _quote_key(quote: str) -> str:
    """Use the longest clause in the excerpt (<= 60 chars) as the comparison key, reducing false judgments from punctuation/context differences."""
    parts = [p.strip() for p in re.split(r"[。!?;,\n]", quote or "") if p.strip()]
    key = max(parts, key=len) if parts else (quote or "")
    return key[:60]


def run_retests(db: Session) -> int:
    """pending_retest tickets: compare against the latest post-remediation answer; N consecutive clean rounds auto-close."""
    tickets = (
        db.query(ComplianceTicketORM)
          .filter(ComplianceTicketORM.status == "pending_retest")
          .all()
    )
    n_done = 0
    for t in tickets:
        alert = db.get(ComplianceAlertORM, t.alert_id)
        if alert is None:
            continue
        since = t.last_action_at or t.opened_at
        try:
            retests = json.loads(t.retest_json or "[]")
        except Exception:  # noqa: BLE001
            retests = []
        tested_ids = {r.get("response_id") for r in retests if isinstance(r, dict)}

        resp = (
            db.query(AiTelemetryResponseORM)
              .filter(AiTelemetryResponseORM.topic_id == t.topic_id)
              .filter(AiTelemetryResponseORM.query == alert.query)
              .filter(AiTelemetryResponseORM.engine == alert.engine)
              .filter(AiTelemetryResponseORM.created_at > since)
              .filter(AiTelemetryResponseORM.answer != "")
              .order_by(AiTelemetryResponseORM.id.desc())
              .first()
        )
        if resp is None or resp.id in tested_ids or resp.id == alert.response_id:
            continue   # No new round of answers yet; wait for the next sweep

        key = compliance_scanner._normalize(_quote_key(alert.quote))
        still = bool(key) and key in compliance_scanner._normalize(resp.answer or "")
        retests.append({
            "at": datetime.utcnow().isoformat(), "run_id": resp.run_id,
            "response_id": resp.id, "still_present": still, "method": "quote_match",
        })
        t.retest_json = json.dumps(retests, ensure_ascii=False)
        compliance_audit.record(
            db, topic_id=t.topic_id, event_type="retest", object_type="ticket",
            object_id=t.id, actor_type="system", actor_name="复测",
            detail={"response_id": resp.id, "still_present": still,
                    "clean_streak": t.clean_streak + (0 if still else 1)},
        )
        if still:
            t.clean_streak = 0
            t.status = "in_progress"
            _notify(db, t.topic_id, "retest_failed", ticket=t, alert=alert)
        else:
            t.clean_streak += 1
            if t.clean_streak >= RETEST_CLEAN_ROUNDS:
                t.status = "closed"
                t.closed_at = datetime.utcnow()
                alert.status = "closed"
                alert.closed_at = t.closed_at
                compliance_audit.record(
                    db, topic_id=t.topic_id, event_type="ticket_close",
                    object_type="ticket", object_id=t.id,
                    actor_type="system", actor_name="复测",
                    detail={"ticket_no": t.ticket_no,
                            "clean_rounds": t.clean_streak, "auto": True},
                )
                _notify(db, t.topic_id, "closed", ticket=t, alert=alert)
        n_done += 1
    if n_done:
        db.commit()
    return n_done


# -- Main flow 3: timeout escalation ------------------------------------------------------

def mark_overdue(db: Session) -> int:
    now = datetime.utcnow()
    tickets = (
        db.query(ComplianceTicketORM)
          .filter(ComplianceTicketORM.status.in_(("open", "in_progress", "pending_retest")))
          .filter(ComplianceTicketORM.deadline_at < now)
          .all()
    )
    for t in tickets:
        t.status = "overdue"
        compliance_audit.record(
            db, topic_id=t.topic_id, event_type="ticket_action", object_type="ticket",
            object_id=t.id, actor_type="system", actor_name="超时监测",
            detail={"ticket_no": t.ticket_no, "action": "overdue",
                    "deadline_at": t.deadline_at.isoformat()},
        )
        if not t.overdue_notified:
            t.overdue_notified = True
            alert = db.get(ComplianceAlertORM, t.alert_id)
            _notify(db, t.topic_id, "overdue", ticket=t, alert=alert)
    if tickets:
        db.commit()
    return len(tickets)


# -- Email notification (failures only logged) ------------------------------------------------

def _notify(db: Session, topic_id: int, kind: str, *, ticket, alert) -> None:
    try:
        from geo.services.email_service import email_service

        topic = db.get(AiTelemetryTopicORM, topic_id)
        owner = db.get(UserORM, topic.user_id) if topic else None
        recipients = []
        if owner and owner.email:
            recipients.append(owner.email)
        ops = os.environ.get("COMPLIANCE_NOTIFY_EMAIL", "") or email_service.sales_notify_email
        if ops and ops not in recipients:
            recipients.append(ops)
        brand = (topic.target or topic.name) if topic else ""
        for to in recipients:
            if kind == "overdue":
                email_service.send_compliance_ticket_email(
                    to=to, kind="overdue", brand=brand, ticket_no=ticket.ticket_no,
                    action_label=ACTION_LABELS_ZH.get(ticket.action, ticket.action),
                    deadline_at=ticket.deadline_at, quote=alert.quote if alert else "")
            elif kind == "retest_failed":
                email_service.send_compliance_ticket_email(
                    to=to, kind="retest_failed", brand=brand, ticket_no=ticket.ticket_no,
                    action_label=ACTION_LABELS_ZH.get(ticket.action, ticket.action),
                    deadline_at=ticket.deadline_at, quote=alert.quote if alert else "")
            elif kind == "closed":
                email_service.send_compliance_ticket_email(
                    to=to, kind="closed", brand=brand, ticket_no=ticket.ticket_no,
                    action_label=ACTION_LABELS_ZH.get(ticket.action, ticket.action),
                    deadline_at=ticket.deadline_at, quote=alert.quote if alert else "")
            elif kind == "opened":
                email_service.send_compliance_ticket_email(
                    to=to, kind="opened", brand=brand, ticket_no=ticket.ticket_no,
                    action_label=ACTION_LABELS_ZH.get(ticket.action, ticket.action),
                    deadline_at=ticket.deadline_at, quote=alert.quote if alert else "")
    except Exception:  # noqa: BLE001
        log.exception("[compliance-monitor] notify %s failed ticket=%s", kind,
                      getattr(ticket, "id", None))


def notify_ticket_opened(ticket_id: int) -> None:
    """Ticket-opened notification -- called by the API confirm endpoint as a BackgroundTask.

    Opens its own session: the request-scoped session is closed after the response returns and cannot be carried into a background task.
    """
    db: Session = SessionLocal()
    try:
        ticket = db.get(ComplianceTicketORM, ticket_id)
        if ticket is None:
            return
        alert = db.get(ComplianceAlertORM, ticket.alert_id)
        _notify(db, ticket.topic_id, "opened", ticket=ticket, alert=alert)
    except Exception:  # noqa: BLE001
        log.exception("[compliance-monitor] notify_ticket_opened failed ticket=%s", ticket_id)
    finally:
        db.close()


# -- tick entry point (shared by the scheduler job and CLI) --------------------------------

def sweep_tick() -> None:
    db: Session = SessionLocal()
    try:
        try:
            sweep(db)
        except Exception:  # noqa: BLE001
            log.exception("[compliance-monitor] sweep failed")
            db.rollback()
        try:
            run_retests(db)
        except Exception:  # noqa: BLE001
            log.exception("[compliance-monitor] retest failed")
            db.rollback()
        try:
            mark_overdue(db)
        except Exception:  # noqa: BLE001
            log.exception("[compliance-monitor] overdue check failed")
            db.rollback()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="合规事中监测 sweep(crontab/手动入口)")
    parser.add_argument("--backfill", type=int, default=0,
                        help="把水位线回拨 N 条后再扫(回扫存量)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    if args.backfill > 0:
        db = SessionLocal()
        try:
            wm = _get_watermark(db)
            if wm is not None:
                _set_watermark(db, max(0, wm - args.backfill))
                db.commit()
                log.info("[compliance-monitor] watermark rewound by %d", args.backfill)
        finally:
            db.close()
    sweep_tick()


if __name__ == "__main__":
    main()
