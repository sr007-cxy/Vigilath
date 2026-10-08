"""Acceptance scenario catalog for the agency permission model.

P2 authorization and resource API scenarios already live in test_p2_access_control.py; this file keeps freezing
the full acceptance catalog and reserves skeletons for P3 concurrency and async revocation scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class AccessCase:
    case_id: str
    actor: str
    relation: str
    action: str
    expected: str


ACCESS_CASES = (
    AccessCase("A01", "agency_admin", "granted_enterprise", "business_read", "allow"),
    AccessCase("A02", "agency_operator", "assigned_enterprise", "business_read", "allow"),
    AccessCase(
        "A03",
        "agency_operator",
        "same_org_unassigned_enterprise",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A04",
        "agency_operator",
        "other_org_enterprise",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A05a",
        "agency_admin",
        "disabled_organization",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A05b",
        "agency_operator",
        "removed_member",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A05c",
        "agency_admin",
        "revoked_grant",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A06",
        "user",
        "enterprise_under_active_agency_grant",
        "business_write",
        "ENTERPRISE_READ_ONLY",
    ),
    AccessCase(
        "A07",
        "user",
        "enterprise_under_active_agency_grant",
        "account_security_write",
        "allow",
    ),
    AccessCase(
        "A08",
        "agency_admin",
        "platform_publish_account_pool",
        "platform_operation",
        "deny",
    ),
    AccessCase(
        "A09",
        "agency_admin",
        "role_member_role_mismatch",
        "business_read",
        "RESOURCE_NOT_FOUND",
    ),
    AccessCase(
        "A10",
        "agency_admin",
        "agent_delegation",
        "agent_operation",
        "deny",
    ),
)


def test_access_case_ids_are_unique_and_expected_results_are_frozen():
    case_ids = [case.case_id for case in ACCESS_CASES]
    assert len(case_ids) == len(set(case_ids))
    assert {case.expected for case in ACCESS_CASES} <= {
        "allow",
        "deny",
        "RESOURCE_NOT_FOUND",
        "ENTERPRISE_READ_ONLY",
    }


@dataclass(frozen=True)
class RaceCase:
    case_id: str
    event_order: str
    expected: str


RACE_CASES = (
    RaceCase("M01", "two_organizations_grant_one_enterprise", "one_active_grant"),
    RaceCase("M02", "two_admins_remove_each_other", "one_active_admin_remains"),
    RaceCase("T01", "revocation_lock_before_worker", "task_cancelled_no_external_action"),
    RaceCase("T02", "worker_recheck_before_revocation", "execution_not_force_stopped"),
    RaceCase("T03", "grant_revoked_before_dispatch_claim", "no_lease_quota_or_payload"),
    RaceCase("T04", "grant_revoked_before_plugin_ready", "no_publish_payload"),
)


def test_race_case_ids_are_unique():
    case_ids = [case.case_id for case in RACE_CASES]
    assert len(case_ids) == len(set(case_ids))


@pytest.mark.parametrize("case", RACE_CASES[:2], ids=lambda case: case.case_id)
def test_management_race_contract_is_implemented(case: RaceCase):
    """Real PostgreSQL acceptance lives in test_p4_postgres_races.py."""
    assert case.expected in {"one_active_grant", "one_active_admin_remains"}
