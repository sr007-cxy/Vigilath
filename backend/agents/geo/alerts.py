"""Proactive outreach: high-risk sentiment scan + notification delivery (web notification table + best-effort IM push-back).

Trigger signal: sentinel get_today kpi.high_risk > threshold (default 1).
Dedup: push at most once per account per day (dedup_key=sent:{account_id}:{date}).
Delivery: ① write agent_notifications (pulled as unread when the web chat window opens); ② if the account has an IM connector + a recent session, push back to Feishu/WeCom.

How to run (cron; prod has no scheduler leader -> use crontab):
    /opt/geo/agent-venv/bin/python -m agents.geo.alerts
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM, AgentNotificationORM
from geo.models.sentiment import SentimentAccountORM

log = logging.getLogger(__name__)

HIGH_RISK_THRESHOLD = int(os.environ.get("SENTIMENT_ALERT_THRESHOLD", "1"))
# Console URL (for the 「查看舆情详情」 ("view sentiment details") link in alerts); overridable per environment via env, defaults to the production console.
APP_BASE_URL = (os.environ.get("AGENT_APP_BASE_URL") or "https://www.vigilath.com").rstrip("/")


def _insert_notification(db, account_id: int, title: str, body: str, dedup_key: str) -> bool:
    """Insert a notification; skip if dedup_key already exists. Returns whether it was newly inserted."""
    exists = db.query(AgentNotificationORM).filter(AgentNotificationORM.dedup_key == dedup_key).first()
    if exists:
        return False
    db.add(AgentNotificationORM(account_id=account_id, title=title, body=body, dedup_key=dedup_key))
    try:
        db.commit()
        return True
    except Exception:  # noqa: BLE001 — unique-key race conflicts, etc.
        db.rollback()
        return False


async def _push_im(db, account_id: int, text: str) -> dict:
    """Push a notification back to the IM bound to the account (Feishu/WeCom). Returns {"sent":n,"failed":n,"errors":[...]}.

    Safeguard: every target's send result is counted + failures are logged explicitly, never swallowed (otherwise "nothing pushed today" can't be debugged).
    """
    conns = (db.query(AgentIMConnectorORM)
             .filter(AgentIMConnectorORM.account_id == account_id, AgentIMConnectorORM.enabled == 1).all())
    sent = 0
    errors: list[str] = []
    for c in conns:
        try:
            if c.platform == "feishu":
                from agents.geo.embed.im_feishu import (
                    _tenant_token, list_bot_chats, send_reply, send_webhook)
                # Unified push config: connector config_json.push_targets = [{type,name,enabled,...}]
                cfg = json.loads(c.config_json or "{}") or {}
                push_targets = [t for t in (cfg.get("push_targets") or [])
                                if t.get("enabled", True)]
                if push_targets:
                    tok = None
                    for t in push_targets:
                        typ, name = t.get("type"), t.get("name") or t.get("chat_id") or t.get("url")
                        try:
                            if typ == "webhook" and t.get("url"):
                                ok = await send_webhook(t["url"], text, t.get("secret", ""))
                                if ok:
                                    sent += 1
                                else:
                                    errors.append(f"webhook失败:{name}")
                            elif typ == "bot" and t.get("chat_id"):
                                if tok is None:
                                    tok = await _tenant_token(c.app_id, c.app_secret)
                                if not tok:
                                    errors.append(f"取token失败:{name}"); continue
                                await send_reply(tok, t["chat_id"], text)
                                sent += 1
                        except Exception as te:  # noqa: BLE001
                            errors.append(f"{typ}:{name}:{str(te)[:40]}")
                    continue
                # Fallback (push_targets not configured): push to all groups the bot is in + last_chat_id
                tok = await _tenant_token(c.app_id, c.app_secret)
                if not tok:
                    errors.append(f"取token失败(connector {c.id})"); continue
                targets = set(await list_bot_chats(tok))
                if c.last_chat_id:
                    targets.add(c.last_chat_id)
                for cid in targets:
                    await send_reply(tok, cid, text)
                    sent += 1
            elif c.platform == "wecom":
                if not c.last_chat_id:
                    continue
                from agents.geo.embed.im_wecom import _send_markdown
                agentid = (json.loads(c.config_json or "{}") or {}).get("agentid")
                await _send_markdown(c.app_id, c.app_secret, agentid, c.last_chat_id, text)
                sent += 1
            # DingTalk: replies rely on a short-lived sessionWebhook (expires); no proactive push afterwards, skip
        except Exception as e:  # noqa: BLE001 — log connector-level exceptions too, never silently
            errors.append(f"connector{c.id}:{str(e)[:60]}")
            log.warning("[_push_im] connector %s push error: %s", c.id, e)
    return {"sent": sent, "failed": len(errors), "errors": errors}


# Sentiment / risk English labels -> Chinese (for the alert list display, no mixed English)
_LABEL_ZH = {"bearish": "偏空", "看跌": "偏空", "利空": "偏空", "negative": "偏空",
             "bullish": "偏多", "看涨": "偏多", "neutral": "中性", "mixed": "复杂"}
_RISK_ZH = {"high": "高风险", "medium": "中风险", "low": "低风险"}


def _today_published_risk(db, acc):
    """**Only same-day publications** (publish_time = today) of relevant posts; returns (medium/high-risk list [max 10], total relevant published today).

    Key: filter by publish_time being today, not ingestion time — old articles (2016/2021) re-crawled today don't count.
    """
    from datetime import date

    from sqlalchemy import text

    schema = f"tenant_{int(acc.id)}"
    today = date.today().isoformat()
    base = (f"FROM {schema}.posts p JOIN {schema}.analyses a "
            f"  ON p.source = a.source AND p.post_id = a.post_id "
            f"WHERE p.symbol = :sym AND a.is_relevant = 1 "
            f"  AND substr(CAST(p.publish_time AS text), 1, 10) = :today")
    try:
        total = db.execute(text(f"SELECT count(*) {base}"), {"sym": acc.ticker, "today": today}).scalar() or 0
        rows = db.execute(text(
            f"SELECT p.title, p.url, a.sentiment_label, a.risk_level, p.publish_time {base} "
            f"  AND a.risk_level IN ('medium', 'high') ORDER BY p.publish_time DESC NULLS LAST LIMIT 10"),
            {"sym": acc.ticker, "today": today}).fetchall()
    except Exception:  # noqa: BLE001 — schema missing / fetch failed: don't trigger
        return [], 0
    posts = [{"title": (t or "(无标题)")[:60], "url": u or "",
              "label": _LABEL_ZH.get((lab or "").lower(), lab or ""),
              "risk": _RISK_ZH.get((rk or "").lower(), rk or ""),
              "time": str(pt)[:10] if pt else ""} for t, u, lab, rk, pt in rows]
    return posts, int(total)


async def scan_and_deliver() -> dict:
    db = SessionLocal()
    delivered = 0
    scanned = 0
    try:
        accounts = db.query(SentimentAccountORM).filter(SentimentAccountORM.active.is_(True)).all()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for acc in accounts:
            scanned += 1
            # Only medium/high-risk posts **published today** (old articles re-crawled today don't trigger); push only when risk count >= threshold
            posts, total = _today_published_risk(db, acc)
            if len(posts) < HIGH_RISK_THRESHOLD:
                continue
            title = "舆情风险提醒"
            # Shown as a list (not a table): links in Feishu lark_md lists are clickable, table cells are not
            lines = [f"⚠️ {acc.target} 今日新增 {len(posts)} 条中高风险负面(当日共 {total} 条相关):"]
            for i, p in enumerate(posts, 1):
                meta = " / ".join(x for x in (p["label"], p["risk"], p["time"]) if x)
                head = f"[{p['title']}]({p['url']})" if p["url"] else p["title"]
                lines.append(f"{i}. {head} — {meta}")
            body = "\n".join(lines)
            if _insert_notification(db, acc.user_id, title, body, f"sent:{acc.user_id}:{today}"):
                delivered += 1
                await _push_im(db, acc.user_id, f"**{title}**\n{body}")
        return {"scanned": scanned, "delivered": delivered}
    finally:
        db.close()


def deliver_manual(account_id: int, title: str, body: str, dedup_key: str | None = None) -> bool:
    """For other (non-sentiment) events to deliver notifications proactively. If dedup_key is not given, a timestamp is used (push every time)."""
    db = SessionLocal()
    try:
        key = dedup_key or f"manual:{account_id}:{datetime.utcnow().timestamp()}"
        ok = _insert_notification(db, account_id, title, body, key)
        if ok:
            asyncio.run(_push_im(db, account_id, f"**{title}**\n{body}"))
        return ok
    finally:
        db.close()


if __name__ == "__main__":
    print(asyncio.run(scan_and_deliver()))
