"""主动触达:舆情高风险扫描 + 通知投递(Web 通知表 + IM 最佳努力回推)。

触发信号:sentinel get_today 的 kpi.high_risk > 阈值(默认 1)。
去重:同账号同一天只推一次(dedup_key=sent:{account_id}:{date})。
投递:① 写 agent_notifications(Web 聊天窗打开时拉未读);② 若账号有 IM 连接器 + 最近会话,回推飞书/企微。

跑法(cron,prod 无 scheduler leader → 走 crontab):
    /opt/geo/agent-venv/bin/python -m geo.agent.alerts
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM, AgentNotificationORM
from geo.models.sentiment import SentimentAccountORM

HIGH_RISK_THRESHOLD = int(__import__("os").environ.get("SENTIMENT_ALERT_THRESHOLD", "1"))


def _insert_notification(db, account_id: int, title: str, body: str, dedup_key: str) -> bool:
    """插入通知;dedup_key 已存在则跳过。返回是否新插入。"""
    exists = db.query(AgentNotificationORM).filter(AgentNotificationORM.dedup_key == dedup_key).first()
    if exists:
        return False
    db.add(AgentNotificationORM(account_id=account_id, title=title, body=body, dedup_key=dedup_key))
    try:
        db.commit()
        return True
    except Exception:  # noqa: BLE001 — 唯一键并发冲突等
        db.rollback()
        return False


async def _push_im(db, account_id: int, text: str) -> None:
    """最佳努力把通知回推到账号绑定的 IM(飞书/企微;需有最近会话)。失败静默。"""
    conns = (db.query(AgentIMConnectorORM)
             .filter(AgentIMConnectorORM.account_id == account_id, AgentIMConnectorORM.enabled == 1).all())
    for c in conns:
        if not c.last_chat_id:
            continue
        try:
            if c.platform == "feishu":
                from geo.agent.embed.im_feishu import _tenant_token, send_reply
                tok = await _tenant_token(c.app_id, c.app_secret)
                if tok:
                    await send_reply(tok, c.last_chat_id, text)
            elif c.platform == "wecom":
                from geo.agent.embed.im_wecom import _send_markdown
                agentid = (json.loads(c.config_json or "{}") or {}).get("agentid")
                await _send_markdown(c.app_id, c.app_secret, agentid, c.last_chat_id, text)
            # 钉钉:回复靠短时 sessionWebhook(会过期),不支持事后主动推,跳过
        except Exception:  # noqa: BLE001
            pass


async def scan_and_deliver() -> dict:
    db = SessionLocal()
    delivered = 0
    scanned = 0
    try:
        from geo.services import sentinel_client

        accounts = db.query(SentimentAccountORM).filter(SentimentAccountORM.active.is_(True)).all()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for acc in accounts:
            scanned += 1
            try:
                data = await asyncio.to_thread(sentinel_client.get_today, acc.id, acc.ticker, 1)
            except Exception:  # noqa: BLE001 — 单账号取数失败不阻断其余
                continue
            kpi = (data or {}).get("kpi") or {}
            high = int(kpi.get("high_risk") or 0)
            if high < HIGH_RISK_THRESHOLD:
                continue
            title = "舆情高风险提醒"
            body = (f"⚠️ {acc.target} 今日出现 {high} 条高风险负面"
                    f"(共 {kpi.get('total_today', 0)} 条提及)。建议尽快查看舆情详情并处置。")
            if _insert_notification(db, acc.user_id, title, body, f"sent:{acc.user_id}:{today}"):
                delivered += 1
                await _push_im(db, acc.user_id, f"**{title}**\n{body}")
        return {"scanned": scanned, "delivered": delivered}
    finally:
        db.close()


def deliver_manual(account_id: int, title: str, body: str, dedup_key: str | None = None) -> bool:
    """供其它事件(非舆情)主动投递通知。dedup_key 不传则用时间戳(每次都推)。"""
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
