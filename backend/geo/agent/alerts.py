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
import os
from datetime import datetime

from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM, AgentNotificationORM
from geo.models.sentiment import SentimentAccountORM

HIGH_RISK_THRESHOLD = int(os.environ.get("SENTIMENT_ALERT_THRESHOLD", "1"))
# 控制台地址(告警里给「查看舆情详情」链接用);各环境可用 env 覆盖,默认指向正式控制台。
APP_BASE_URL = (os.environ.get("AGENT_APP_BASE_URL") or "https://www.vigilath.com").rstrip("/")


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
        try:
            if c.platform == "feishu":
                # 飞书:推到机器人**所在的全部群**(不再只发 last_chat_id 那一个),
                # 再并上 last_chat_id 兜底(覆盖单聊/列群失败场景),去重后逐个发。
                from geo.agent.embed.im_feishu import _tenant_token, list_bot_chats, send_reply
                tok = await _tenant_token(c.app_id, c.app_secret)
                if not tok:
                    continue
                targets = set(await list_bot_chats(tok))
                if c.last_chat_id:
                    targets.add(c.last_chat_id)
                for cid in targets:
                    await send_reply(tok, cid, text)
            elif c.platform == "wecom":
                if not c.last_chat_id:
                    continue
                from geo.agent.embed.im_wecom import _send_markdown
                agentid = (json.loads(c.config_json or "{}") or {}).get("agentid")
                await _send_markdown(c.app_id, c.app_secret, agentid, c.last_chat_id, text)
            # 钉钉:回复靠短时 sessionWebhook(会过期),不支持事后主动推,跳过
        except Exception:  # noqa: BLE001
            pass


# 情感 / 风险英文标签 → 中文(告警清单展示用,不混英文)
_LABEL_ZH = {"bearish": "偏空", "看跌": "偏空", "利空": "偏空", "negative": "偏空",
             "bullish": "偏多", "看涨": "偏多", "neutral": "中性", "mixed": "复杂"}
_RISK_ZH = {"high": "高风险", "medium": "中风险", "low": "低风险"}


def _today_risk_posts(db, acc) -> list[dict]:
    """取该账号今日「中/高风险」帖子(与 sentinel KPI 同口径:今日入库 + 相关 + 新鲜),带标题/链接/情感/风险。"""
    from datetime import date, timedelta

    from sqlalchemy import text

    schema = f"tenant_{int(acc.id)}"
    today = date.today().isoformat()
    max_age = int(os.environ.get("SENTINEL_DISPLAY_MAX_AGE_DAYS", "3"))
    rec = (date.today() - timedelta(days=max_age - 1)).isoformat()
    sql = text(f"""
        SELECT p.title, p.url, a.sentiment_label, a.risk_level
        FROM {schema}.posts p JOIN {schema}.analyses a USING (source, post_id, symbol)
        WHERE p.symbol = :sym AND a.is_relevant = 1
          AND substr(p.ingested_at, 1, 10) = :today
          AND COALESCE(p.publish_time, p.ingested_at) >= :rec
          AND a.risk_level IN ('medium', 'high')
        ORDER BY CASE a.risk_level WHEN 'high' THEN 0 ELSE 1 END, p.publish_time DESC
        LIMIT 10
    """)
    try:
        rows = db.execute(sql, {"sym": acc.ticker, "today": today, "rec": rec}).fetchall()
    except Exception:  # noqa: BLE001 — schema 不存在/取数失败:返回空,让告警退化为仅计数
        return []
    return [{"title": (t or "(无标题)")[:60], "url": u or "",
             "label": _LABEL_ZH.get((lab or "").lower(), lab or ""),
             "risk": _RISK_ZH.get((rk or "").lower(), rk or "")} for t, u, lab, rk in rows]


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
            # 注:此处 high = kpi.high_risk,口径是 risk_level IN ('medium','high'),即「中+高风险」之和,
            # 故文案统一写「中高风险」,不夸大成「高风险」(见 sentinel service.py 的 high_risk 聚合)。
            title = "舆情风险提醒"
            # 直接列出中/高风险帖子 + 各自来源链接(列表而非表格:飞书 lark_md 列表里的链接可点,表格单元格不可点)
            posts = _today_risk_posts(db, acc)
            lines = [f"⚠️ {acc.target} 今日出现 {high} 条中高风险负面(共 {kpi.get('total_today', 0)} 条提及):"]
            for i, p in enumerate(posts, 1):
                tag = " / ".join(x for x in (p["label"], p["risk"]) if x)
                lines.append(f"{i}. [{p['title']}]({p['url']}) — {tag}" if p["url"] else f"{i}. {p['title']} — {tag}")
            if not posts:
                lines.append("(明细链接本次未取到,请到舆情页查看)")
            body = "\n".join(lines)
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
