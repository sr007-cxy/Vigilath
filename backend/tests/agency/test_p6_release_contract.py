"""P6 pre-release high-risk boundary and frontend contract guards."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from geo.api import publish_accounts
from geo.api.auth import is_staff
from geo.utils.error_handler import AppException


REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Strip // and /* */ comments before "this string must not appear anywhere" assertions.

    Gating is derived from the `roles:` arrays and `to:` paths; nothing written in comments grants access. Previously this did a full-text
    substring check, and a line in userNav.ts saying "ops-side /ops/users is still the same component" turned it red --
    red with zero information, and pushing later contributors to delete explanatory comments to make the test pass.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _publish_pool_guard():
    assert len(publish_accounts.router.dependencies) == 1
    return publish_accounts.router.dependencies[0].dependency


@pytest.mark.parametrize("role", ["admin", "maintainer"])
def test_publish_account_pool_allows_platform_operations_only(role: str):
    actor = SimpleNamespace(role=role, is_admin=role == "admin")
    assert asyncio.run(_publish_pool_guard()(current_user=actor)) is actor


@pytest.mark.parametrize("role", ["operator", "agency_admin", "agency_operator", "user"])
def test_publish_account_pool_rejects_non_operations_roles(role: str):
    with pytest.raises(AppException) as exc:
        asyncio.run(_publish_pool_guard()(current_user=SimpleNamespace(role=role)))
    assert exc.value.status_code == 403


def test_agency_roles_never_become_platform_staff_or_agent_roles():
    for role in ("agency_admin", "agency_operator"):
        assert is_staff(SimpleNamespace(role=role, is_admin=False)) is False
    agent_scope = _read("backend/agents/geo/scope.py")
    assert "agency_admin" not in agent_scope
    assert "agency_operator" not in agent_scope


def test_frontend_navigation_and_direct_routes_keep_sensitive_pages_closed():
    """The navigation source was split into opsNav + userNav on 2026-09-08 (workbenchNav.ts deleted); the contract itself is unchanged:
    ops pages are open only to ops roles, and agency roles can't enter a single ops route.

    Still asserting on text rather than parsing TS: these two files are both "menu visibility" and "direct route gating"
    (canAccessOpsPath / canAccessAppPath derive from them); one extra roles line equals granting access,
    so these literal lines are exactly what must be guarded.
    """
    ops = _read("frontend/src/constants/opsNav.ts")
    user = _read("frontend/src/constants/userNav.ts")

    # The publishing account pool (formerly /workbench/account-center) is still open only to admin / maintainer
    assert "const ADMIN_MAINT = ['admin', 'maintainer'] as const;" in ops
    pool = next(l for l in ops.splitlines() if "'/ops/publish-accounts'" in l)
    assert "roles: ADMIN_MAINT" in pool

    # Platform user management (formerly /workbench/users) is ops-side, open only to admin / operator
    assert "const OPS = ['admin', 'operator'] as const;" in ops
    users = next(l for l in ops.splitlines() if "'/ops/users'" in l)
    assert "roles: OPS" in users

    # No agency role may appear in the ops menu source -- gating is derived from this file,
    # and writing one in once opens the whole /ops prefix to agencies
    for role in ("agency_admin", "agency_operator"):
        assert role not in _code_only(ops)

    # Conversely: the customer-side menu must not mix in ops pages (agency_* do have roles in this file)
    for path in ("/ops/publish-accounts", "/ops/sales-agent", "/ops/users"):
        assert path not in _code_only(user)

    # Agencies get only one more item than regular customers, "Enterprise", and no ops roles
    agency = next(l for l in user.splitlines() if "const AGENCY =" in l)
    assert "'maintainer'" not in agency and "'operator'" not in agency


def test_frontend_role_split_does_not_load_all_users_for_agency_or_operator():
    page = _read("frontend/src/pages/Workbench/UserOrganizationManagement.tsx")
    assert "tab === 'accounts'\n          ? <AdminUsers embedded />" in page
    assert "role === 'agency_admin'\n            ? <AgencyAdminPanel tab={tab} />" in page
    assert "<PlatformAgencyPanel tab={tab} canManage={role === 'admin'} />" in page
    assert "adminUsersApi" not in page
    assert "agencyApi.members(token)" in page
    assert "agencyApi.options(token)" in page


def test_enterprise_agency_page_is_read_only_and_publish_drawer_is_scoped():
    agency_page = _read("frontend/src/pages/Account/AgencyTab.tsx")
    for mutation in ("grant(", "revokeGrant(", "assign(", "removeMember("):
        assert mutation not in agency_page
    drawer = _read("frontend/src/components/publish/PublishCenterDrawer.tsx")
    assert "allowBackendChannels={isStaff || isAgency}" in drawer


def test_topic_api_exposes_enterprise_owner_not_only_actor_context():
    model = _read("backend/geo/models/ai_telemetry.py")
    topic_out = model[model.index("class TopicOut"):model.index("class RunOut")]
    assert "user_id: int" in topic_out
    assert "user_id=r.user_id" in topic_out


def test_topic_review_detail_reuses_topic_owner_id_without_duplicate_kwargs():
    """Detail GET/PATCH both use TopicOut's user_id as the enterprise owner; must not pass it twice and cause a 500."""
    api = _read("backend/geo/api/admin_review.py")
    get_start = api.index("def get_topic_review_detail(")
    get_end = api.index(chr(10) * 2 + "class AdminPatchTopicPayload", get_start)
    patch_start = api.index("def admin_patch_topic(")
    patch_end = api.index(chr(10) * 2 + "def _send_review_email_safe", patch_start)
    assert "user_id=t.user_id" not in api[get_start:get_end]
    assert "user_id=t.user_id" not in api[patch_start:patch_end]

def test_topic_schedule_endpoint_uses_managed_enterprise_read_permission():
    api = _read("backend/geo/api/ai_telemetry.py")
    start = api.index("def get_topic_schedule(")
    end = api.index("@router.get", start)
    endpoint = api[start:end]
    assert "allow_admin_user=current_user" in endpoint
    assert "action=ACTION_READ" in endpoint


def test_agency_and_develop_migration_heads_are_merged():
    revision = _read(
        "backend/alembic/versions/"
        "c8f4b2a71d96_merge_agency_and_engine_hourly_heads.py"
    )
    assert 'revision = "c8f4b2a71d96"' in revision
    assert 'down_revision = ("c1f8b3d24e97", "b7a2d9e4c631")' in revision


def test_develop_dispatch_index_revision_is_compatible_with_full_history():
    revision = _read(
        "backend/alembic/versions/"
        "e9d3a1f7c584_dispatch_tasks_unique_run_engine_query.py"
    )
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in revision
    assert "op.drop_index" not in revision
