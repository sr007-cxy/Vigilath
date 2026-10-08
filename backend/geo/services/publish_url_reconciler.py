"""Automatic probing of candidate publish URLs.

Only probes already-registered public candidate URLs; never calls platform backends or publish APIs.
On by default; in an emergency disable with GEO_PUBLISH_URL_RECONCILE_ENABLED=0.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.services.publish_task_service import mark_published

log = logging.getLogger(__name__)

MAX_PER_RUN = int(os.environ.get("PUBLISH_URL_RECONCILE_MAX_PER_RUN", "10"))
PUBLIC_PROBE_TIMEOUT = float(os.environ.get("PUBLISH_URL_PUBLIC_PROBE_TIMEOUT", "20"))
CANDIDATE_PROBE_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
)


def _meta(task: TopicDocPublishTaskORM) -> dict[str, Any]:
    try:
        value = json.loads(task.channel_meta_json or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _candidate_state(task: TopicDocPublishTaskORM) -> dict[str, Any]:
    state = _meta(task).get("url_verification")
    return state if isinstance(state, dict) else {}


def _candidate_submitted_at(task: TopicDocPublishTaskORM, state: dict[str, Any]) -> datetime:
    for raw in (state.get("submitted_at"), getattr(task, "submitted_at", None),
                getattr(task, "created_at", None)):
        if isinstance(raw, datetime):
            return raw
        if raw:
            try:
                return datetime.fromisoformat(str(raw))
            except ValueError:
                pass
    return datetime.utcnow()


def _candidate_next_at(task: TopicDocPublishTaskORM, state: dict[str, Any],
                       attempts: int) -> Optional[datetime]:
    if attempts >= len(CANDIDATE_PROBE_DELAYS):
        return None
    return _candidate_submitted_at(task, state) + CANDIDATE_PROBE_DELAYS[attempts]


def _candidate_due(task: TopicDocPublishTaskORM, now: datetime) -> bool:
    state = _candidate_state(task)
    if state.get("verification_method", "url") != "url":
        return False
    attempts = int(state.get("attempts", 0) or 0)
    scheduled = _candidate_next_at(task, state, attempts)
    if scheduled is None:
        return False
    raw = state.get("next_at")
    if not raw:
        return scheduled <= now
    try:
        return datetime.fromisoformat(str(raw)) <= now and scheduled <= now
    except ValueError:
        return scheduled <= now


def select_candidate_url_tasks(db: Session, now: datetime) -> list[TopicDocPublishTaskORM]:
    rows = (db.query(TopicDocPublishTaskORM)
              .filter(TopicDocPublishTaskORM.status == "url_submitted")
              .order_by(TopicDocPublishTaskORM.submitted_at.asc(),
                        TopicDocPublishTaskORM.id.asc())
              .limit(MAX_PER_RUN * 4).all())
    out = []
    for row in rows:
        state = _candidate_state(row)
        if state.get("candidate_url") and _candidate_due(row, now):
            out.append(row)
        if len(out) >= MAX_PER_RUN:
            break
    return out


def _is_zhihu_candidate(platform: str, url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    host = (parsed.hostname or "").lower()
    platform_key = (platform or "").strip().lower()
    return platform_key in {"zhihu", "知乎"} \
        and host in {"zhuanlan.zhihu.com", "www.zhihu.com"} \
        and bool(parsed.path.rstrip("/").startswith("/p/"))


def probe_candidate_url(platform: str, url: str) -> tuple[str, str]:
    """Publicly probe a candidate URL.

    Returns: valid / invalid / blocked. blocked is not failure; Zhihu 403 is treated as valid,
    because bot access to Zhihu columns is often blocked by anti-scraping, but the link shape itself can come from the publish receipt.
    """
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=PUBLIC_PROBE_TIMEOUT, follow_redirects=True,
                          headers=headers) as client:
            resp = client.get(url)
        status = resp.status_code
        if 200 <= status < 400:
            return "valid", f"HTTP {status}"
        if _is_zhihu_candidate(platform, url) and status == 403:
            return "valid", "知乎公开页返回 403，按发布候选 URL 确认有效"
        if status in (404, 410):
            return "invalid", f"HTTP {status}"
        if status in (401, 403, 429) or status >= 500:
            return "blocked", f"HTTP {status}"
        return "blocked", f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001
        return "blocked", f"{type(exc).__name__}: {exc}"


def _record_candidate_probe(task: TopicDocPublishTaskORM, now: datetime, *,
                            status: str, detail: str) -> int:
    meta = _meta(task)
    state = meta.get("url_verification") if isinstance(meta.get("url_verification"), dict) else {}
    attempts = int(state.get("attempts", 0) or 0) + 1
    next_at = _candidate_next_at(task, state, attempts)
    failure = f"第{attempts}次失败：{detail}"
    state.update({
        "status": status,
        "attempts": attempts,
        "last_at": now.isoformat(),
        "last_error": failure,
        "next_at": next_at.isoformat() if next_at else None,
    })
    meta["url_verification"] = state
    meta["candidate_url"] = state.get("candidate_url") or meta.get("candidate_url")
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
    task.last_error = failure
    return attempts


def reconcile_candidate_urls(db: Session, now: datetime, *, apply: bool) -> list[dict]:
    items: list[dict] = []
    for task in select_candidate_url_tasks(db, now):
        state = _candidate_state(task)
        url = str(state.get("candidate_url") or "").strip()
        item = {"task_id": task.id, "platform": task.platform, "candidate_url": url}
        if not apply:
            item["result"] = "candidate_due"
            items.append(item)
            continue
        verdict, detail = probe_candidate_url(task.platform, url)
        item["probe"] = verdict
        item["detail"] = detail
        if verdict == "valid":
            try:
                mark_published(db, task, url, proof={
                    "source": "candidate_url_probe",
                    "probe_detail": detail,
                    "candidate_source": state.get("source"),
                })
                item["result"] = "published"
            except ValueError as exc:
                task.status = "failed"
                task.last_error = f"推送失败：候选 URL 入账失败:{exc}"[:2000]
                item["result"] = "failed"
                item["error"] = str(exc)
            db.commit()
            items.append(item)
            continue
        attempts = _record_candidate_probe(task, now, status=verdict, detail=detail)
        item["attempts"] = attempts
        if verdict == "invalid" and attempts >= len(CANDIDATE_PROBE_DELAYS):
            item["result"] = "invalid_final"
            task.last_error = f"第{attempts}次失败：候选 URL 检测无效({detail})，链接继续展示，请人工确认"[:2000]
        elif verdict == "blocked" and attempts >= len(CANDIDATE_PROBE_DELAYS):
            item["result"] = "blocked_final"
            task.last_error = f"第{attempts}次失败：检测受阻({detail})，链接继续展示，请人工确认"[:2000]
        elif verdict == "blocked":
            item["result"] = "blocked"
        else:
            item["result"] = "retry"
        db.commit()
        items.append(item)
    return items


def run_once(db: Session, *, apply: bool = False) -> dict:
    now = datetime.utcnow()
    candidate_items = reconcile_candidate_urls(db, now, apply=apply)
    return {
        "mode": "apply" if apply else "dry-run",
        "candidate_items": candidate_items,
    }


def tick() -> None:
    if os.environ.get("GEO_PUBLISH_URL_RECONCILE_ENABLED", "1") != "1":
        return
    from geo.database import SessionLocal
    db = SessionLocal()
    try:
        report = run_once(db, apply=True)
        log.info("[publish-url-reconcile] %s", json.dumps(report, ensure_ascii=False))
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="发布候选 URL 公开探测")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from geo.cron_env import load_env_for_cron
    load_env_for_cron()
    from geo.database import SessionLocal
    db = SessionLocal()
    try:
        print(json.dumps(run_once(db, apply=args.apply), ensure_ascii=False, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
