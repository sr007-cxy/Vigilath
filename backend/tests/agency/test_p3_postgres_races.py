"""P3 PostgreSQL row-lock ordering concurrency acceptance; runs when P3_POSTGRES_URL is set."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from geo.models.organization import (
    OperationContextORM,
    OrganizationGrantORM,
    OrganizationMemberORM,
    OrganizationORM,
)
from geo.models.user import UserORM
from geo.services.access_control import revalidate_operation_context
from geo.utils.error_handler import AppException


# worker-center (formerly services/telemetry-service) lives in the separate V-networks repo since 2026-09-21.
# By default it's looked up at ../V-networks next to the main repo, or a checkout via V_NETWORKS_ROOT; if not found the whole file is skipped --
# these two cases verify "both repos lock rows of the same table in the same order", which can't be checked without that code.
TELEMETRY_ROOT = Path(
    os.environ.get("V_NETWORKS_ROOT")
    or Path(__file__).resolve().parents[4] / "V-networks"
) / "worker-center"
if not (TELEMETRY_ROOT / "app" / "storage.py").exists():
    pytest.skip(f"requires V-networks checkout (V_NETWORKS_ROOT), not found at {TELEMETRY_ROOT}",
                allow_module_level=True)
if str(TELEMETRY_ROOT) not in sys.path:
    sys.path.insert(0, str(TELEMETRY_ROOT))
from app.storage import revalidate_operation_context as worker_revalidate  # noqa: E402


DATABASE_URL = os.environ.get("P3_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires P3_POSTGRES_URL for PostgreSQL FOR UPDATE semantics",
)


def _seed(engine, *, offset: int) -> tuple[int, int]:
    base_id = 1_000_000_000 + int(time.time_ns() % 500_000_000)
    actor_id = base_id
    enterprise_id = base_id + 1
    organization_id = base_id + 2
    member_id = base_id + 3
    grant_id = base_id + 4
    context_id = base_id + 5
    with Session(engine) as db:
        db.add_all([
            UserORM(
                id=actor_id,
                email=f"p3-race-actor-{base_id}@example.com",
                password_hash="x",
                role="agency_admin",
                is_admin=False,
                is_active=True,
            ),
            UserORM(
                id=enterprise_id,
                email=f"p3-race-enterprise-{base_id}@example.com",
                password_hash="x",
                role="user",
                is_admin=False,
                is_active=True,
            ),
        ])
        db.commit()
        db.add(OrganizationORM(
            id=organization_id,
            organization_type="agency",
            name=f"P3 race {base_id}",
            created_by_user_id=actor_id,
        ))
        db.commit()
        db.add(OrganizationMemberORM(
            id=member_id,
            organization_id=organization_id,
            organization_type="agency",
            user_id=actor_id,
            member_role="admin",
            joined_by_user_id=actor_id,
        ))
        db.commit()
        db.add(OrganizationGrantORM(
            id=grant_id,
            organization_id=organization_id,
            organization_type="agency",
            target_user_id=enterprise_id,
            granted_by_user_id=actor_id,
        ))
        db.commit()
        db.add(OperationContextORM(
            id=context_id,
            trace_id=f"p3-race-{base_id}",
            actor_user_id=actor_id,
            enterprise_user_id=enterprise_id,
            authorization_mode="organization",
            action="publish.start",
            target_resource_type="publish_task",
            target_resource_id=str(offset),
            organization_id=organization_id,
            organization_member_id=member_id,
            organization_grant_id=grant_id,
        ))
        db.commit()
    return grant_id, context_id


def _engine():
    return create_engine(DATABASE_URL, future=True)


def test_t01_revocation_lock_first_makes_worker_recheck_fail():
    engine = _engine()
    grant_id, context_id = _seed(engine, offset=1)
    revoke_locked = threading.Event()
    outcome: list[str] = []

    def revoke():
        with Session(engine) as db:
            grant = (
                db.query(OrganizationGrantORM)
                .filter(OrganizationGrantORM.id == grant_id)
                .with_for_update()
                .one()
            )
            grant.status = "revoked"
            grant.revoked_by_user_id = grant.granted_by_user_id
            db.flush()
            revoke_locked.set()
            time.sleep(0.2)
            db.commit()

    def worker():
        assert revoke_locked.wait(timeout=3)
        with Session(engine) as db:
            try:
                revalidate_operation_context(db, context_id)
            except AppException:
                outcome.append("cancelled")
                db.rollback()
            else:
                outcome.append("started")
                db.commit()

    revoke_thread = threading.Thread(target=revoke)
    worker_thread = threading.Thread(target=worker)
    revoke_thread.start()
    worker_thread.start()
    revoke_thread.join(timeout=5)
    worker_thread.join(timeout=5)

    assert not revoke_thread.is_alive()
    assert not worker_thread.is_alive()
    assert outcome == ["cancelled"]
    engine.dispose()


def test_t02_worker_lock_first_enters_execution_before_revocation_commits():
    engine = _engine()
    grant_id, context_id = _seed(engine, offset=2)
    worker_locked = threading.Event()
    outcome: list[str] = []
    revoke_elapsed: list[float] = []

    def worker():
        with Session(engine) as db:
            revalidate_operation_context(db, context_id)
            outcome.append("started")
            worker_locked.set()
            time.sleep(0.2)
            db.commit()

    def revoke():
        assert worker_locked.wait(timeout=3)
        started_at = time.monotonic()
        with Session(engine) as db:
            db.execute(
                text(
                    "UPDATE organization_grants "
                    "SET status='revoked', revoked_by_user_id=granted_by_user_id "
                    "WHERE id=:grant_id"
                ),
                {"grant_id": grant_id},
            )
            db.commit()
        revoke_elapsed.append(time.monotonic() - started_at)

    worker_thread = threading.Thread(target=worker)
    revoke_thread = threading.Thread(target=revoke)
    worker_thread.start()
    revoke_thread.start()
    worker_thread.join(timeout=5)
    revoke_thread.join(timeout=5)

    assert not worker_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert outcome == ["started"]
    assert revoke_elapsed and revoke_elapsed[0] >= 0.15
    engine.dispose()


def test_telemetry_service_postgres_recheck_matches_backend_contract():
    engine = _engine()
    grant_id, context_id = _seed(engine, offset=3)
    with Session(engine) as db:
        assert worker_revalidate(db, context_id) is True
        db.commit()
        grant = db.get(OrganizationGrantORM, grant_id)
        grant.status = "revoked"
        grant.revoked_by_user_id = grant.granted_by_user_id
        db.commit()
        assert worker_revalidate(db, context_id) is False
        db.rollback()
    engine.dispose()
