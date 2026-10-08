"""One-click diagnosis finished -> push the user a message with the report link.

Reports take several minutes and users will not sit on the polling page; if nobody tells them when it finishes, the report lies unread in the history list.
Delivery goes through three paths, all "best effort"; any failure does not affect the report, which is already ready:

  (1) agent_notifications table -- in-site notification. The frontend chat bubble polls unread count every 60s and shows a red dot;
     opening the chat window shows the body as markdown, so the link is written directly as a markdown link.
  (2) Service account **customer service message** -- domestic users almost all arrive via official account QR scan/authorization, so this is the most direct.
     Arbitrary text + link, no template, zero config. The only limit: it can only be sent to users who interacted with the official account within 48h
     -- the person running the report clicked in from the official account minutes ago, so naturally within the window; outside the window (45015) just log a line,
     and the user still has (1) back on the site. Product decided not to use template messages (2026-09-14).
  (3) IM connectors bound to the account (Feishu/WeCom) -- reuses agents.geo.alerts._push_im. It depends on
     the agent package set (pydantic-ai, etc.), which may not be installed in the main backend venv; skipped if import fails.

The link base uses WECHAT_H5_BASE_URL (the domain behind the CN official account), falling back to FRONTEND_URL only when unset --
on 2026-09-14 we found CN production's FRONTEND_URL was set to the test site domain; building links from it alone would send users to the test site.

Dedup: dedup_key = full_diag_done:{report_id}, each report is pushed only once (no duplicates on retry/rerun);
service account and IM follow the in-site notification's dedup -- if no new in-site row was inserted, neither of the other two is sent.
"""
from __future__ import annotations

import logging
from geo.database import settings

log = logging.getLogger(__name__)


def link_base() -> str:
    return (settings.WECHAT_H5_BASE_URL or settings.FRONTEND_URL or "").rstrip("/")


def report_url(report_id: int) -> str:
    return f"{link_base()}/diagnosis/{report_id}"


def notify_report_done(*, report_id: int, user_id: int, brand: str,
                       total: object, grade: object) -> None:
    """Called after the report is persisted. Never raises -- notification failures are only logged."""
    try:
        _notify(report_id=report_id, user_id=user_id, brand=brand, total=total, grade=grade)
    except Exception as e:  # noqa: BLE001
        log.warning("full diagnosis %d: completion notification delivery failed: %s", report_id, e)


def _notify(*, report_id: int, user_id: int, brand: str, total: object, grade: object) -> None:
    from geo.database import SessionLocal
    from geo.models.agent import AgentNotificationORM

    url = report_url(report_id)
    score = f"{total} 分 / {grade} 级" if total is not None else "已生成"
    title = f"「{brand}」一键全面诊断报告已生成"
    body = f"综合评分 {score}。[点击查看报告]({url})"
    dedup_key = f"full_diag_done:{report_id}"

    db = SessionLocal()
    try:
        exists = (db.query(AgentNotificationORM)
                    .filter(AgentNotificationORM.dedup_key == dedup_key).first())
        if exists is not None:
            return
        db.add(AgentNotificationORM(account_id=user_id, title=title, body=body,
                                    dedup_key=dedup_key))
        try:
            db.commit()
        except Exception:  # noqa: BLE001 -- unique key concurrency conflict: someone else already pushed
            db.rollback()
            return
        _send_wechat(db, user_id, report_id, brand=brand, score=score, title=title, url=url)
        _push_im_best_effort(db, user_id, f"{title}\n{body}", report_id)
    finally:
        db.close()


def _send_wechat(db, user_id: int, report_id: int, *, brand: str, score: str,
                 title: str, url: str) -> None:
    from geo.models.user import UserORM
    from geo.services import wechat_mp_service

    if not wechat_mp_service.is_configured():
        return
    openid = db.query(UserORM.wechat_openid).filter(UserORM.id == user_id).scalar()
    if not openid:
        return
    # Customer service message: zero config, receivable if interacted within 48h
    text = f"{title}\n综合评分 {score}。\n点击查看报告:{url}"
    try:
        res = wechat_mp_service.send_customer_text(openid, text)
    except Exception as e:  # noqa: BLE001
        log.warning("full diagnosis %d: customer service message send failed: %s", report_id, e)
        res = {"errcode": -1}
    code = res.get("errcode", 0)
    if code == 0:
        log.info("full diagnosis %d: service account customer service message sent", report_id)
        return
    if code == wechat_mp_service.ERR_OUT_OF_48H:
        # The user has not interacted with the official account within 48h -- normal; the in-site notification is still there
        log.info("full diagnosis %d: user outside the 48h customer service message window, service account message not sent", report_id)
    else:
        log.warning("full diagnosis %d: customer service message rejected errcode=%s errmsg=%s",
                    report_id, code, res.get("errmsg"))


def _push_im_best_effort(db, user_id: int, text: str, report_id: int) -> None:
    try:
        from agents.geo.alerts import _push_im
    except Exception as e:  # noqa: BLE001 -- the main backend venv does not have the agent dependencies installed
        log.info("full diagnosis %d: IM push unavailable (%s), in-site notification only", report_id, type(e).__name__)
        return
    try:
        import asyncio
        res = asyncio.run(_push_im(db, user_id, text))
        if res.get("sent") or res.get("failed"):
            log.info("full diagnosis %d: IM push sent=%s failed=%s",
                     report_id, res.get("sent"), res.get("failed"))
    except Exception as e:  # noqa: BLE001
        log.warning("full diagnosis %d: IM push failed: %s", report_id, e)
