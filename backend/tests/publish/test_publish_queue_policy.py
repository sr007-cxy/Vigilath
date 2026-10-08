from datetime import datetime, timedelta
from types import SimpleNamespace

from geo.api.publish_accounts import effective_cap, platform_daily_cap
from geo.api.publish_dispatch import account_queueable
from geo.models.publish_accounts import CHANNEL_SERVER_POOL, PublishAccountRegisterIn


def _account(**overrides):
    values = {
        "channel": CHANNEL_SERVER_POOL,
        "status": "active",
        "expires_at": datetime.utcnow() + timedelta(days=1),
        "daily_cap": None,
        "platform": "_test_unlimited",
        "warmup_until": None,
        "unschedulable_until": None,
        "rate_limited_until": None,
        "lease_until": None,
        "warmup_daily_cap": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_temporary_execution_blocks_do_not_block_queueing():
    now = datetime.utcnow()
    for field in ("lease_until", "rate_limited_until", "unschedulable_until"):
        account = _account(**{field: now + timedelta(hours=1)})
        assert account_queueable(account, now) == (True, None)


def test_hard_account_failures_block_queueing():
    now = datetime.utcnow()
    for status in ("pending_auth", "quarantined", "unschedulable", "retired"):
        ok, reason = account_queueable(_account(status=status), now)
        assert not ok and status in reason
    ok, reason = account_queueable(_account(expires_at=now - timedelta(seconds=1)), now)
    assert not ok and "过期" in reason


def test_publish_accounts_default_to_unlimited_but_allow_override():
    assert PublishAccountRegisterIn.model_fields["daily_cap"].default == 0
    assert PublishAccountRegisterIn.model_fields["warmup_daily_cap"].default == 0
    assert platform_daily_cap("_test_unlimited") == 0
    assert effective_cap(_account(daily_cap=7)) == 7
