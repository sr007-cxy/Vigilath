"""Sales Agent service layer: basic utilities: exceptions, JSON/list helpers, size bucketing, copy snippets, compliance events."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session
from agents.sales_agent.constants import (
    COMPLIANCE_DECISIONS,
    DEFAULT_RECOMMENDATIONS,
    MAX_RECOMMENDATIONS,
    REPORT_TABLE_ALIASES,
    RISK_LEVELS,
)
from agents.sales_agent.models import SalesAgentComplianceLogORM


GEO_BACKEND_URL = os.environ.get("GEO_BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")
ALGORITHM = "HS256"


# Which side a send failure falls on. Scheduled sending's "fall through to the next mailbox of the same company" only applies to the recipient side:
# when the sender side is broken (wrong authorization code, cannot connect), changing recipients does nothing and would only burn through
# all of this company's mailboxes in one config error. So **when unsure, always count it as sender side** and do not fall through.
FAILURE_SIDE_SENDER = "sender"
FAILURE_SIDE_RECIPIENT = "recipient"


class SalesAgentError(ValueError):
    """Business error. `public=True` means the message is hard-coded and may be shown to the user verbatim.

    Default False: the outbound `_public_error_message` scrubs it against a word list, blocking messages that carry raw exceptions or
    whole vendor responses (`f"阿里云拒绝下发:{body['Message']}"` ("Aliyun refused delivery: ..."),
    `f"...context={json.dumps(context)}"`). The word list blocks by **surface wording**, so the config errors users most need to see,
    such as "Resend mailbox is missing API Key" or "Twilio channel is missing Account SID / Auth Token,
    please complete it on the SMS admin page", get swatted as leaks precisely because they name what to fix.

    Allow them through this switch rather than punching holes in the word list: one hole and every **raw** leak containing that word gets through too.
    Mark public only when the message is a literal with no external content.

    `failure_side` only matters for send errors; default `sender` (no fall-through). See the two constants above for values.

    `permanent` means the remote rejection is permanent (5xx); only `failure_side=recipient` with
    `permanent=True` writes the address to the suppression list (§3.3). Default False: when unsure,
    treat it as transient; retrying costs nothing, whereas permanently blacklisting a valid address that was merely "mailbox full" does.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        *,
        public: bool = False,
        failure_side: str = FAILURE_SIDE_SENDER,
        permanent: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.public = public
        self.failure_side = failure_side if failure_side in (FAILURE_SIDE_SENDER, FAILURE_SIDE_RECIPIENT) else FAILURE_SIDE_SENDER
        self.permanent = bool(permanent)


SAFE_RUNTIME_ERROR = "Sales Agent 本次处理失败,请稍后重试或换个问题。"
INTERNAL_ERROR_RE = re.compile(
    r"(model|provider|pydantic|openrouter|openai|anthropic|deepseek|qwen|moonshot|glm|siliconflow|api[_ -]?key|token|traceback|exception|source_report|detection_records|full_diagnosis|context=|模型|供应商|密钥|令牌)",
    re.IGNORECASE,
)


def public_error_text(error: object, fallback: str = SAFE_RUNTIME_ERROR) -> str:
    """Outbound scrubbing of error messages. `api.py`'s `_public_error_message()` now delegates here.

    Moved down into common because HTTP is no longer the only exit: scheduled sending writes the generation failure reason
    into `skip_reason` in the scheduler process, and that sentence also ends up in front of the user without ever going through api.py.
    **One word list, two exits**: with a copy in each place, newly added blocked words would only be added to one of them.
    """
    msg = str(error or "").strip()
    if not msg:
        return fallback
    # Everyone must pass the length check: even public messages should not dump a huge block on the user.
    if len(msg) > 240:
        return fallback
    if getattr(error, "public", False):
        return msg
    if INTERNAL_ERROR_RE.search(msg):
        return fallback
    return msg


# Positive cache of table existence (keyed by bind id). Only caches "exists", never "does not exist":
# created tables do not disappear, but missing tables get added by alembic, and caching False would hide the new table from the process forever.
_TABLE_EXISTS_CACHE: set[tuple[int, str]] = set()


def session_has_table(db: Session, table_name: str) -> bool:
    """Whether the table exists. Must query via the session's own connection, not `inspect(db.get_bind())`.

    Passing the Engine borrows another connection from the pool; on an in-memory SQLite DB that "other" connection is actually the same
    dbapi connection, and the ROLLBACK when the inspector finishes wipes out the session's uncommitted INSERTs too;
    the symptom is a record that was flushed and got an id, but commit raises StaleDataError
    (UPDATE matched 0 rows). Using `db.connection()` stays within the same transaction and also saves
    borrowing and returning a connection every time an event is persisted.
    """
    if db is None:
        return True
    try:
        conn = db.connection()
    except Exception:  # noqa: BLE001
        return True  # Do not block when no connection is available; let the real write report the error
    key = (id(conn.engine), table_name)
    if key in _TABLE_EXISTS_CACHE:
        return True
    if sa_inspect(conn).has_table(table_name):
        _TABLE_EXISTS_CACHE.add(key)
        return True
    return False


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _loads(raw: str | None, default: Any) -> Any:
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, type(default)) else default
    except Exception:  # noqa: BLE001
        return default


def _clean_list(items: list[Any] | None, limit: int = 20) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items or []:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= limit:
            break
    return out


_PROFILE_PRODUCT_SELF_DESC_RE: re.Pattern[str] = re.compile(r"[，,。；;\s]*[^，,。；;]{1,40}是(?:一款|一个|一种|一套|一项|一家).*$")


def _clean_profile_option_text(value: Any) -> str:
    """Clean product self-descriptions in profiles/profile candidates, without truncating by length.

    **Deliberately no length truncation** here: if the model writes long, show the whole entry. Hard cuts by length used to leave
    fragments like "...precise outreach capability, and" that users ticked into profiles and that flowed all the way into email bodies. Length is controlled
    by constraining model output in the prompt, not by cutting already generated content here.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return _PROFILE_PRODUCT_SELF_DESC_RE.sub("", text).strip(" \t\r\n-—:：;；,，。")


def _clean_profile_option_list(items: list[Any] | None, limit: int = 20) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items or []:
        text = _clean_profile_option_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


# System placeholder brand names. They carry zero information for the user; card titles and email senders must not treat them as a real brand:
# "Our profile · Default brand" only makes users think they missed some configuration.
PLACEHOLDER_BRAND_NAMES: frozenset[str] = frozenset({"默认品牌", "未命名品牌", "我的品牌"})


def usable_brand_name(value: Any) -> str:
    """Placeholder brand names are always treated as "not filled" and return an empty string."""
    text = str(value or "").strip()
    return "" if text in PLACEHOLDER_BRAND_NAMES else text


def _clamp_limit(limit: int | None, default: int = DEFAULT_RECOMMENDATIONS) -> int:
    try:
        n = int(limit or default)
    except Exception:  # noqa: BLE001
        n = default
    return max(1, min(MAX_RECOMMENDATIONS, n))


COMPANY_SIZE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1-10 名员工", 1, 10),
    ("11-100 名员工", 11, 100),
    ("101-500 名员工", 101, 500),
    ("501-1000 名员工", 501, 1000),
    ("1001+ 名员工", 1001, None),
)
COMPANY_SIZE_BUCKET_LABELS = tuple(label for label, _, _ in COMPANY_SIZE_BUCKETS)


def _bucket_for_employee_count(count: int) -> str:
    for label, low, high in COMPANY_SIZE_BUCKETS:
        if count >= low and (high is None or count <= high):
            return label
    return ""


def _normalize_company_size_bucket(value: str) -> str:
    """Normalize free-text company size into a unified bucket; return empty when unrecognized."""
    raw = str(value or "").strip()
    if not raw or "待补充" in raw:
        return ""
    text = raw.lower().replace(" ", "").replace(",", "").replace("，", "")
    # Exact match first, then substring: "1-10" is a substring of "11-100" / "501-1000"; if substring matching ran first,
    # those two official options would be swallowed as "1-10 名员工" ("1-10 employees") (fixed 2026-08-22).
    for label, _, _ in COMPANY_SIZE_BUCKETS:
        compact = label.lower().replace(" ", "")
        if text in {compact, compact.replace("名员工", ""), compact.replace("名员工", "人")}:
            return label
    for label, _, _ in COMPANY_SIZE_BUCKETS:
        compact = label.lower().replace(" ", "")
        if compact.replace("名员工", "") in text or compact.replace("名员工", "人") in text:
            return label
    if "中大型" in text:
        return "501-1000 名员工"
    if any(k in text for k in ("千人以上", "大型企业", "大企业", "集团型")):
        return "1001+ 名员工"
    if any(k in text for k in ("中小企业", "中小型", "中型企业")):
        return "11-100 名员工"
    if any(k in text for k in ("小微", "微型", "初创", "创业团队")):
        return "1-10 名员工"

    range_match = re.search(r"(\d+)\s*(?:-|~|～|—|–|到|至)\s*(\d+)", text)
    if range_match:
        low = int(range_match.group(1))
        high = int(range_match.group(2))
        if high < low:
            low, high = high, low
        return _bucket_for_employee_count(max(1, round((low + high) / 2)))

    nums = [int(x) for x in re.findall(r"\d+", text)]
    if not nums:
        return ""
    n = nums[0]
    if any(k in text for k in ("以上", "+", "超过", "大于", "不少于")):
        if n >= 1000:
            return "1001+ 名员工"
        return _bucket_for_employee_count(n + 1)
    if any(k in text for k in ("以下", "以内", "小于", "少于", "不超过")):
        return _bucket_for_employee_count(max(1, n))
    return _bucket_for_employee_count(n)


def _company_size_bucket_index(bucket: str) -> int | None:
    try:
        return COMPANY_SIZE_BUCKET_LABELS.index(bucket)
    except ValueError:
        return None


def _company_size_bucket_distance(a: str, b: str) -> int | None:
    ia = _company_size_bucket_index(_normalize_company_size_bucket(a) or a)
    ib = _company_size_bucket_index(_normalize_company_size_bucket(b) or b)
    if ia is None or ib is None:
        return None
    return abs(ia - ib)


def _preferred_target_size_buckets_for_seller(seller_size: str) -> list[str]:
    bucket = _normalize_company_size_bucket(seller_size)
    if not bucket:
        return []
    preferences = {
        "1-10 名员工": ["1-10 名员工", "11-100 名员工"],
        "11-100 名员工": ["11-100 名员工", "101-500 名员工"],
        "101-500 名员工": ["101-500 名员工", "501-1000 名员工", "11-100 名员工"],
        "501-1000 名员工": ["501-1000 名员工", "1001+ 名员工", "101-500 名员工"],
        "1001+ 名员工": ["1001+ 名员工", "501-1000 名员工"],
    }
    return preferences.get(bucket, [])


def _normalize_report_table(source_report_table: str) -> str:
    key = (source_report_table or "").strip()
    if not key:
        return ""
    table = REPORT_TABLE_ALIASES.get(key)
    if not table:
        raise SalesAgentError("不支持的报告来源。只能读取 detection_records 或 full_diagnosis_reports。", public=True)
    return table


def record_compliance_event(
    db: Session,
    user_id: int,
    action: str,
    risk_level: str = "low",
    decision: str = "allowed",
    reason: str = "",
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    risk = risk_level if risk_level in RISK_LEVELS else "medium"
    dec = decision if decision in COMPLIANCE_DECISIONS else "blocked"
    row = SalesAgentComplianceLogORM(
        user_id=user_id,
        action=(action or "")[:128],
        risk_level=risk,
        decision=dec,
        reason=reason or "",
        detail_json=_dumps(detail or {}),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return {"id": row.id, "action": row.action, "risk_level": row.risk_level, "decision": row.decision, "reason": row.reason}


def _profile_phrase(items: list[Any], fallback: str = "", limit: int = 2, skip_national: bool = False) -> str:
    values: list[str] = []
    for item in items or []:
        value = str(item or "").strip()
        if not value or "待补充" in value:
            continue
        if skip_national and value == "全国":
            continue
        if value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return "、".join(values) or fallback
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(str(value or "").strip()))


def _safe_email(value: str) -> str:
    text = str(value or "").strip()
    return text[:255]


# Criterion for "does this brand offer website audit/diagnostic reports". It lives at the lowest layer because two upper layers use it:
# `brandprofile` derives it from the profile's service fields, `profiles` derives it from capabilities an orphan brand filled in itself.
# Judging both with the same word list is correct; with a copy on each side, the same capability would get different answers on the two brand kinds.
#
# Only explicit audit/diagnostic/report action words count; domain terms like GEO/SEO/visibility/effect measurement are no longer treated as capabilities.
# Brands like GEO-TAG doing "effect measurement / ROI reconciliation" have no website audit feature; a name or tag containing GEO must not reveal the report button.
REPORT_CAPABILITY_KEYWORDS: tuple[str, ...] = (
    "网站检测", "官网检测", "站点检测", "网页检测", "网站诊断", "官网诊断", "站点诊断", "网页诊断",
    "检测报告", "诊断报告", "检测功能", "诊断功能", "报告功能",
    "检测", "诊断", "报告", "审计", "评测", "测评", "体检", "扫描", "监测", "监控",
    "audit", "report", "diagnos", "monitor", "scan", "assessment",
)
_REPORT_CAPABILITY_NEGATION_RE = re.compile(
    r"(?:不做|不提供|不支持|没有|无|非|无法|不能|不含|不是)[^。；;，,、\n]{0,12}"
    r"(?:检测|诊断|报告|审计|评测|测评|体检|扫描|监测|监控|audit|report|diagnos|monitor|scan|assessment)",
    re.IGNORECASE,
)


def texts_are_report_capable(texts: Iterable[Any]) -> bool:
    """Whether a set of service descriptions includes website audit/diagnostic reports. All empty -> False: when nothing is filled
    default to "cannot produce reports"; a false negative just hides a button, a false positive lets people click and get rejected by the backend."""
    blob = " ".join(str(item) for item in texts if str(item or "").strip()).casefold()
    if not blob.strip():
        return False
    positive_blob = _REPORT_CAPABILITY_NEGATION_RE.sub("", blob)
    return any(word in positive_blob for word in REPORT_CAPABILITY_KEYWORDS)
