"""P0 permission contract guard.

This file only verifies the security boundaries of existing code (checks against the P0 contract
document are not included, since that document is not part of the open-source release); it doesn't require the P1 ORM or P2
access_control to exist. Resource-level behavior scenarios are first expressed as skip skeletons in test_access_control_scenarios.py,
and converted into real tests item by item as each phase begins.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from geo.api.auth import is_staff


REPO_ROOT = Path(__file__).resolve().parents[3]
def test_agency_roles_are_not_platform_staff_before_scoped_access_exists():
    """P0/P1 must not open agency roles by widening is_staff."""
    for role in ("agency_admin", "agency_operator"):
        assert is_staff(SimpleNamespace(role=role, is_admin=False)) is False


def test_publish_router_uses_resource_scope_without_global_agency_whitelist():
    text = (
        REPO_ROOT / "backend" / "geo" / "api" / "publish_dispatch.py"
    ).read_text(encoding="utf-8")
    router_line = next(
        line for line in text.splitlines() if line.startswith("router = APIRouter(")
    )
    assert "agency_admin" not in router_line
    assert "agency_operator" not in router_line
    assert "dependencies=" not in router_line
    assert "current_user=Depends(get_current_user)" in text
    assert "require_resource_access(db, current_user, doc, ACTION_WRITE)" in text
    assert 'current_user=Depends(require_roles("admin", "operator"))' in text
    assert "service_router = APIRouter(" in text
    assert "require_publish_service_token" in text


def test_t03_dispatch_rechecks_before_lease_quota_and_payload():
    text = (
        REPO_ROOT / "backend" / "geo" / "api" / "publish_dispatch.py"
    ).read_text(encoding="utf-8")
    claim_block = text[text.index("def claim("):text.index("def _quota_ok(")]
    recheck = claim_block.index("revalidate_operation_context(db, context_id)")
    assert recheck < claim_block.index("acct.lease_worker_id = payload.worker_id")
    assert recheck < claim_block.index("acct.used_today += 1")
    assert recheck < claim_block.index('"title": rendered.title')


def test_t04_plugin_rechecks_before_returning_ready_payload():
    text = (
        REPO_ROOT / "backend" / "geo" / "api" / "aitoearn_channel.py"
    ).read_text(encoding="utf-8")
    block = text[
        text.index("async def plugin_publish_start("):
        text.index("def plugin_publish_complete(")
    ]
    assert block.index("revalidate_operation_context(db, operation.id)") \
        < block.rindex('"action": "ready"')
