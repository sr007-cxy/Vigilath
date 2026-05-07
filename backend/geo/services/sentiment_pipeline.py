"""舆情 pipeline 编排 — 单账号的 monitor → analyze → brief 全流程.

被 sentiment_scheduler(定时任务)和 /run-now 手动触发共用.
状态机更新 + 失败兜底 + 邮件推送都在这里.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.sentiment import (
    KeywordGroup, SentimentAccountORM, SentimentKnowledgeORM, SentimentRunLogORM,
    flatten_keyword_groups,
)
from geo.services import sentinel_client
from geo.services.sentinel_client import SentinelError

log = logging.getLogger(__name__)


def _load_knowledge(db: Session, account_id: int) -> dict[str, str]:
    rows = db.query(SentimentKnowledgeORM).filter_by(account_id=account_id).all()
    return {r.key: r.body for r in rows if r.body}


def _mark_running(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM):
    acc.last_run_status = "running"
    log_row.status = "running"
    db.commit()


def _mark_success(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM, stats: dict):
    now = datetime.utcnow()
    stats_json = json.dumps(stats, ensure_ascii=False, default=str)
    acc.last_run_at = now
    acc.last_run_status = "success"
    acc.last_run_error = None
    acc.last_run_stats = stats_json
    log_row.ended_at = now
    log_row.status = "success"
    log_row.stats_json = stats_json
    if log_row.started_at:
        log_row.duration_s = int((now - log_row.started_at).total_seconds())
    db.commit()


def _mark_failed(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM, err: str):
    now = datetime.utcnow()
    truncated = err[:500] if err else ""
    acc.last_run_status = "failed"
    acc.last_run_error = truncated
    log_row.ended_at = now
    log_row.status = "failed"
    log_row.error = truncated
    if log_row.started_at:
        log_row.duration_s = int((now - log_row.started_at).total_seconds())
    db.commit()


def run_pipeline_for_account(account_id: int, trigger: str = "manual") -> dict:
    """同步执行 monitor → analyze → brief.

    在 BackgroundTasks 或 APScheduler thread 里调用,内部已经处理状态机.
    返回 {status, stats}.
    """
    db: Session = SessionLocal()
    try:
        acc = db.get(SentimentAccountORM, account_id)
        if not acc:
            log.warning("run_pipeline: account %s not found", account_id)
            return {"status": "failed", "error": "account not found"}

        if not acc.active:
            log.info("run_pipeline: account %s is inactive, skip", account_id)
            return {"status": "skipped", "reason": "inactive"}

        log_row = SentimentRunLogORM(
            account_id=account_id, trigger=trigger,
            started_at=datetime.utcnow(), status="running",
        )
        db.add(log_row)
        _mark_running(db, acc, log_row)

        knowledge = _load_knowledge(db, account_id)
        aliases = json.loads(acc.aliases_json or "[]")
        # 优先用结构化分组展平的关键词;为空才回退扁平字段.
        groups_raw = json.loads(acc.keyword_groups_json or "[]")
        if groups_raw:
            keywords = flatten_keyword_groups(
                [KeywordGroup.model_validate(g) for g in groups_raw]
            )
        else:
            keywords = json.loads(acc.keywords_json or "[]")
        excludes = json.loads(acc.excludes_json or "[]")

        stats: dict = {}
        try:
            log.info("run_pipeline[%s]: monitor begin", account_id)
            r1 = sentinel_client.run_monitor(
                account_id=account_id,
                target=acc.target, ticker=acc.ticker,
                intent=acc.intent, aliases=aliases,
                keywords=keywords, excludes=excludes,
            )
            stats["monitor"] = r1

            log.info("run_pipeline[%s]: analyze begin", account_id)
            r2 = sentinel_client.run_analyze(account_id=account_id, ticker=acc.ticker)
            stats["analyze"] = r2

            log.info("run_pipeline[%s]: brief begin", account_id)
            r3 = sentinel_client.run_brief(account_id=account_id, ticker=acc.ticker)
            stats["brief"] = {
                "date": r3.get("date"),
                "path": r3.get("path"),
            }

            # 邮件推送(notify_emails 非空时)
            emails = json.loads(acc.notify_emails_json or "[]")
            if emails:
                _push_brief_email(acc, r3.get("body", ""), emails)

            _mark_success(db, acc, log_row, stats)
            log.info("run_pipeline[%s]: done", account_id)
            return {"status": "success", "stats": stats}
        except SentinelError as e:
            log.warning("run_pipeline[%s]: sentinel failed: %s (%s)", account_id, e, e.category)
            _mark_failed(db, acc, log_row, str(e))
            return {"status": "failed", "error": str(e), "category": e.category}
        except Exception as e:
            log.exception("run_pipeline[%s]: crashed: %s", account_id, e)
            _mark_failed(db, acc, log_row, str(e))
            return {"status": "failed", "error": str(e), "category": "system"}
    finally:
        db.close()


def _push_brief_email(acc: SentimentAccountORM, body_md: str, recipients: list[str]) -> None:
    """简报邮件推送 — 用 GEO 现有 Resend 通道.
    MVP 简单实现:把 Markdown 直接装进邮件 plaintext;P1 可加 HTML 渲染.
    """
    try:
        from geo.services.email_service import send_email  # type: ignore
    except ImportError:
        log.warning("email_service not available, skipping brief push")
        return

    subject = f"[舆情简报] {acc.target} · {datetime.utcnow().strftime('%Y-%m-%d')}"
    for to in recipients:
        try:
            send_email(to=to, subject=subject, body=body_md)
        except Exception as e:  # noqa: BLE001
            log.warning("brief email to %s failed: %s", to, e)
