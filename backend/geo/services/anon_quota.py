"""Anonymous rate limit -- the single gate for login-free capabilities (dual key: device + egress IP).

Why it exists: each login-free engine probe is a real, paid account pool call. The web uses the `geo_anon_id`
cookie for bucketing, but CLI / agent have no cookie, and one for loop could drain it.

Division of labor between the two keys:

- **device_id** -- random string generated on the client's first run and written to `~/.vigilath/config`.
  It can be cleared, so it is only "natural bucketing for normal users", not a security boundary.
- **egress IP** -- cannot be cleared, the real hard constraint. But a company shares one egress IP,
  so the IP quota must be significantly higher than the device quota, otherwise a whole office gets blocked.

Exceeding either key rejects. The window is a rolling N days, not a calendar month -- a calendar month would let someone registering at month end
consume two periods of quota within a few days.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.models.anon_usage import AnonUsageORM

# Quota per login-free capability. Zero-engine-cost ones (on-site audit, SEO, industry rankings) are not in this table at all --
# they are unlimited; do not add gates to them just for "consistency".
LIMITS: dict[str, dict] = {
    # Engine probe quick test: 3 engines x 3 questions, about ¥2.25 per run
    "mention": {"device": 2, "ip": 10, "window_days": 30},
    # Competitor matchup: 1 competitor x 3 engines
    "rival": {"device": 2, "ip": 10, "window_days": 30},
    # The four advanced audits that **actually cost money** (need OpenRouter / OpenAI / Perplexity keys).
    # AEO and crawler tests are zero cost and not in this table -- gating zero-cost capabilities is just asking for trouble.
    "adv_authority": {"device": 3, "ip": 15, "window_days": 30},
    "adv_citation": {"device": 3, "ip": 15, "window_days": 30},
    "adv_entity": {"device": 3, "ip": 15, "window_days": 30},
    "adv_compare": {"device": 2, "ip": 10, "window_days": 30},   # Compares 2-5 sites at once, more expensive
    # These two ask a batch of questions across multiple engines; per-run cost is clearly higher, so quotas are tighter
    "adv_visibility": {"device": 2, "ip": 8, "window_days": 30},
    "adv_competitive": {"device": 2, "ip": 8, "window_days": 30},
}


class QuotaExceeded(Exception):
    """Quota exceeded. message is shown directly to the user; scope says which key was hit."""

    def __init__(self, message: str, scope: str, retry_after_days: int):
        self.message = message
        self.scope = scope                      # device | ip
        self.retry_after_days = retry_after_days
        super().__init__(message)


def _count(db: Session, feature: str, field, value: str, since: datetime) -> int:
    if not value:
        return 0
    return (
        db.query(func.count(AnonUsageORM.id))
        .filter(AnonUsageORM.feature == feature, field == value, AnonUsageORM.used_at >= since)
        .scalar()
        or 0
    )


def check(db: Session, feature: str, device_id: str, ip: str) -> dict:
    """Check without consuming. Returns {allowed, device_used, device_limit, ip_used, ip_limit, window_days}."""
    cfg = LIMITS.get(feature)
    if cfg is None:
        # No quota configured = unlimited (zero-cost capabilities like on-site audit take this path)
        return {"allowed": True, "unlimited": True}

    since = datetime.utcnow() - timedelta(days=cfg["window_days"])
    dev_used = _count(db, feature, AnonUsageORM.device_id, device_id, since)
    ip_used = _count(db, feature, AnonUsageORM.ip, ip, since)
    return {
        "allowed": dev_used < cfg["device"] and ip_used < cfg["ip"],
        "unlimited": False,
        "device_used": dev_used,
        "device_limit": cfg["device"],
        "ip_used": ip_used,
        "ip_limit": cfg["ip"],
        "window_days": cfg["window_days"],
    }


def consume(db: Session, feature: str, device_id: str, ip: str, note: str = "") -> dict:
    """Check + consume in one step. Raises QuotaExceeded when over the limit (with a message that can be read to the user as-is).

    ★ Consumed at **submission**, not after success -- once an engine call is sent the money is spent; we cannot wait for the result.
      But "submitted" is not "used": when the batch run center is unreachable, no askable question can be built from industry terms,
      or every engine errors out, not a single engine actually answered and the user got nothing.
      The caller explicitly calls `refund()` for those failures, see its docstring.
    """
    st = check(db, feature, device_id, ip)
    if st.get("unlimited"):
        return st
    if not st["allowed"]:
        cfg = LIMITS[feature]
        if st["device_used"] >= st["device_limit"]:
            raise QuotaExceeded(
                f"这台设备的检测次数用完了({st['device_limit']} 次 / {cfg['window_days']} 天)。"
                f"**检测本身不收费** —— 限次只为防止被当免费接口刷;"
                f"注册 Vigilath 账号后额度更高,或者等窗口滚过去。",
                scope="device", retry_after_days=cfg["window_days"],
            )
        raise QuotaExceeded(
            f"当前网络出口的免费次数用完了({st['ip_limit']} 次 / {cfg['window_days']} 天)。"
            f"如果你在公司网络里,可能是同事先用掉了 —— 注册账号后按账号独立计额。",
            scope="ip", retry_after_days=cfg["window_days"],
        )

    db.add(AnonUsageORM(
        feature=feature,
        device_id=(device_id or "")[:64],
        ip=(ip or "")[:64],
        note=(note or "")[:255],
        used_at=datetime.utcnow(),
    ))
    db.commit()
    st["device_used"] += 1
    st["ip_used"] += 1
    return st


def refund(db: Session, feature: str, device_id: str, ip: str) -> bool:
    """Refund one quota unit -- **only call when not a single engine answered this round**.

    The refund criterion is not "task failed" but "the user got nothing": batch run center unreachable,
    no askable question could be built, malformed response shape, or it ran but every engine errored (answered == 0).
    If even one engine really answered, this run counts as used -- the money was really spent; refunding would open an API abuse hole.

    Implemented by deleting the **most recent** matching row in the window (not marking it void): this table is only for counting;
    an extra status column would need a filter in `_count`, and every reader would have to remember to add it.
    Deleting the row keeps counts correct naturally. Returns False if no row found (double refund, or the quota row rolled out of the window).
    """
    cfg = LIMITS.get(feature)
    if cfg is None:
        return False                        # Unlimited capabilities were never charged
    since = datetime.utcnow() - timedelta(days=cfg["window_days"])
    q = db.query(AnonUsageORM).filter(
        AnonUsageORM.feature == feature, AnonUsageORM.used_at >= since,
    )
    # Same keys as consume: device and IP each provide half the criterion; only when both match is it the same call
    q = q.filter(AnonUsageORM.device_id == (device_id or "")[:64],
                 AnonUsageORM.ip == (ip or "")[:64])
    row = q.order_by(AnonUsageORM.used_at.desc(), AnonUsageORM.id.desc()).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def client_ip(request) -> str:
    """Egress IP. nginx is in front, so the first segment of X-Forwarded-For must be read, otherwise everyone is 127.0.0.1."""
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (fwd or (request.client.host if request.client else ""))[:64]
