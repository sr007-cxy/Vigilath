"""Login-free engine quick test — the public endpoint for S2 "AI mention quick test".

It produces **raw engine text**, not hit-rate numbers: the fact "the AI mentions your competitor, not you"
only stands when the engine's own words are shown; anyone can make up numbers.

Three things dictate its current shape:

1. **Asynchronous**. Engine measurements go through the batch-run center → account pool → browser/HTTP engines, taking minutes. Waiting synchronously would tie up
   workers and hit nginx timeouts, so a task_id is returned immediately and the client polls (the client hides
   the polling; users type one command and wait for the result, so it still feels synchronous).
2. **Quota is charged at start, but refunded if no engine answered**. Money is spent as soon as engine calls go out,
   so charging cannot wait for results; but "started" is not "used" — when the batch-run center is unreachable,
   industry terms cannot form questions, or every engine errors, the user gets nothing,
   and that attempt is refunded via `anon_quota.refund` (criterion: answered == 0, not status == failed).
   If even one engine really answered it counts as used, otherwise "retry on failure" would be a free unlimited channel.
3. **Anonymous**. There is no account to attribute to, so quota is limited by a device + egress IP dual key (see anon_quota).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from geo.database import SessionLocal
from geo.models.anon_usage import AnonQuickTaskORM
from geo.services import anon_quota
from geo.utils.error_handler import AppException

log = logging.getLogger(__name__)
router = APIRouter()

QUERY_COUNT = 3          # 3 category questions — the point is "whom the AI recommends", not a brand self-check
ENGINE_COUNT = 3         # 3 engines (free-tier definition in doc §1.3)
SNIPPET_CHARS = 400      # Length of each raw engine text returned
RUN_TIMEOUT = 420.0      # Max synchronous wait on the batch-run center; beyond it, mark failed so the task is not running forever


def _build_queries(brand: str, industry_terms: list[str]) -> list[str]:
    """3 category questions. Reuses the diagnosis templates, taking only category term types (search/qa/intent) —
    brand term types ask "how is XX", whose answers always contain the brand name and cannot show whether it was recommended."""
    from geo.services.full_diagnosis_queries import build_term_queries_template

    rows = build_term_queries_template(brand, industry_terms, [brand], None)
    # ★ This template returns the field name text, not query (KeyError on the first real production run, 2026-08-19).
    #   Use .get instead of subscripting: if upstream renames the field again, this degrades to the fallback template instead of breaking the whole feature.
    picked = [r.get("text") or "" for r in rows if r.get("scene") in ("search", "qa", "intent")]
    picked = [q for q in picked if q.strip()]
    if not picked:                                   # Fallback when templates cannot form questions (industry term too short / a full sentence)
        picked = [f"{t}推荐" for t in industry_terms if t.strip()]
    return picked[:QUERY_COUNT]


def _run_task(task_id: str, brand: str, industry_terms: list[str]) -> None:
    """Background thread: call the batch-run center for **raw responses**, judge hits, write results to the DB.

    ★ Does not reuse full_diagnosis's run_engine_probe: it only returns an aggregated hit matrix,
      **without raw engine text**, which is the whole value of S2. The raw rows of run-now are used directly here.

    **Never let exceptions escape the thread** — failures must persist a failed state too, otherwise the client polls until timeout
    and the user only sees "stuck".
    """
    import httpx

    from geo.services.full_diagnosis_competitive import TELEMETRY_SERVICE_URL, _engines
    from geo.services.telemetry_client import post_run_now

    db = SessionLocal()
    try:
        row = db.query(AnonQuickTaskORM).filter(AnonQuickTaskORM.task_id == task_id).first()
        if row is None:
            return
        shaped: dict | None = None
        try:
            queries = _build_queries(brand, industry_terms)
            if not queries:
                raise RuntimeError("行业词拼不出可问的品类问题")
            engines = _engines()[:ENGINE_COUNT]
            if not engines:
                raise RuntimeError("没有可用引擎")
            rows = post_run_now(
                TELEMETRY_SERVICE_URL,
                {"name": f"quick-mention:{brand}", "queries": queries, "engines": engines},
                timeout=RUN_TIMEOUT, label="quick-mention",
            )
            if not isinstance(rows, list):
                raise RuntimeError("跑批中心返回形状异常")
            shaped = _shape(rows, brand, queries)
            row.status = "done"
        except httpx.HTTPError as e:
            log.warning("[quick-check] task=%s batch-run center unreachable: %s", task_id, e)
            row.status = "failed"
            row.error = f"telemetry unreachable: {e}"[:512]
        except Exception as e:                       # noqa: BLE001
            log.warning("[quick-check] task=%s failed: %s", task_id, e)
            row.status = "failed"
            row.error = str(e)[:512]

        # The criterion is "no engine answered", not "status == failed": in a round that finished but where every engine
        # errored, status is done but answered is 0, and the user equally got nothing.
        answered = int((shaped or {}).get("answered") or 0)
        refunded = False
        if answered == 0:
            refunded = anon_quota.refund(db, "mention", row.device_id, row.ip)
            log.info("[quick-check] task=%s zero responses, quota refunded=%s", task_id, refunded)
        # The failed branch did not write result_json before — write one here too, so GET can say whether it was refunded,
        # without adding a column + a migration for one bool.
        payload = dict(shaped or {})
        payload["quota_refunded"] = refunded
        row.result_json = json.dumps(payload, ensure_ascii=False)
        row.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _shape(rows: list, brand: str, queries: list[str]) -> dict:
    """Compress raw responses into an "engine's own words first" shape.

    Deliberately **outputs no percentages** — a sample of 3 questions cannot support a rate, and giving one would be making it up.
    The output: for each question, what each engine said and whether it mentioned you.
    """
    low_brand = brand.strip().lower()
    answers, engines_used = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        engine = str(r.get("engine") or "")
        answer = str(r.get("answer") or "")
        err = r.get("error")
        if err:
            # Failed engines get a row as-is, not silently dropped — users must see when an engine is missing
            answers.append({"query": str(r.get("query") or ""), "engine": engine,
                            "failed": True, "reason": str(err)[:120]})
            continue
        engines_used.add(engine)
        cites = []
        for i, c in enumerate((r.get("citations") or [])[:5]):
            if isinstance(c, dict):
                cites.append({"url": (c.get("url") or "").strip(),
                              "title": c.get("title") or "",
                              "position": c.get("position") or (i + 1)})
        answers.append({
            "query": str(r.get("query") or ""),
            "engine": engine,
            "mentioned": bool(low_brand and low_brand in answer.lower()),
            "answer_excerpt": answer[:SNIPPET_CHARS],
            "citations": cites,
            "collected_at": r.get("created_at") or r.get("collected_at") or "",
        })
    hit = sum(1 for a in answers if a.get("mentioned"))
    total = sum(1 for a in answers if not a.get("failed"))
    return {
        "brand": brand,
        "queries": queries,
        "engines_used": sorted(engines_used),
        "mentioned_in": hit,          # A count, not a rate
        "answered": total,
        "answers": answers,
        "note": (
            "免登录快测。每条 `answer_excerpt` 是引擎原话截段,`collected_at` 是采集时间 —— "
            "**把原话念给用户,这是他最该看到的东西**。"
            "`mentioned_in`/`answered` 是计数,**不要换算成百分比**:3 条问题的样本量不支持算率。"
            "标了 `failed` 的引擎就是没跑成,如实说少了哪个,**不要补格子、不要估算**。"
            "要多引擎多问题的完整口径,那是需要授权的全面诊断。"
        ),
    }


class MentionIn(BaseModel):
    brand: str
    industry_terms: list[str] = []
    device_id: str = ""          # Persistent client-generated identifier, used with the egress IP for quota limiting


@router.post("/check/mention/anonymous", status_code=202)
async def start_mention_check(body: MentionIn, request: Request):
    """Start a quick test. **No login**, quota-limited by device + IP. Returns task_id; poll with GET."""
    brand = (body.brand or "").strip()
    if not brand:
        raise AppException(status_code=400, message="品牌名不能为空")
    terms = [t.strip() for t in (body.industry_terms or []) if t and t.strip()][:5]
    if not terms:
        raise AppException(status_code=400, message="至少给一个行业词(如「商用清洁机器人」),否则问不出品类问题")

    ip = anon_quota.client_ip(request)
    db = SessionLocal()
    try:
        try:
            st = anon_quota.consume(db, "mention", body.device_id, ip, note=brand[:64])
        except anon_quota.QuotaExceeded as e:
            raise AppException(status_code=429, message=e.message)

        task_id = uuid.uuid4().hex[:32]
        db.add(AnonQuickTaskORM(
            task_id=task_id, feature="mention", brand=brand[:128],
            device_id=(body.device_id or "")[:64], ip=ip,
            status="running", created_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    # Runs in a background thread; daemon so it does not block process exit. Quota limits keep concurrency tiny, so no queue is needed.
    threading.Thread(target=_run_task, args=(task_id, brand, terms), daemon=True).start()

    return {
        "task_id": task_id,
        "status": "running",
        "poll": f"/api/check/mention/{task_id}",
        "eta_seconds": 180,
        "quota": {k: st.get(k) for k in ("device_used", "device_limit", "ip_used", "ip_limit", "window_days")},
    }


@router.get("/check/mention/{task_id}")
async def get_mention_check(task_id: str):
    """Poll. status: running / done (with result) / failed (with error)."""
    db = SessionLocal()
    try:
        row = db.query(AnonQuickTaskORM).filter(AnonQuickTaskORM.task_id == task_id).first()
        if row is None:
            raise AppException(status_code=404, message="任务不存在或已过期")
        out = {"task_id": row.task_id, "status": row.status,
               "created_at": row.created_at.isoformat() if row.created_at else None}
        stored = json.loads(row.result_json) if row.result_json else {}
        refunded = bool(stored.pop("quota_refunded", False))
        if row.status == "done" and stored:
            out["result"] = stored
        elif row.status == "failed":
            # Raw engine-side errors are not passed through to anonymous callers; only one actionable sentence is given
            out["error"] = (
                "本次实测没跑出结果(引擎繁忙或暂时不可用),**额度已退回**,可以直接再试一次"
                if refunded else
                "本次实测没跑出结果(引擎繁忙或暂时不可用),请稍后再试"
            )
        out["quota_refunded"] = refunded
        return out
    finally:
        db.close()


@router.get("/check/mention-quota")
async def mention_quota(request: Request, device_id: str = ""):
    """Query only, no charge: how many attempts this device/egress IP has left. Clients ask before starting to avoid waiting for nothing."""
    db = SessionLocal()
    try:
        return anon_quota.check(db, "mention", device_id, anon_quota.client_ip(request))
    finally:
        db.close()
