"""Add worker_uid / session_id to ai_telemetry_responses: attribute answers by account

Debugging Qwen refusals/empty answers (2026-09-10) kept getting stuck at the same point: there was no link between the response table and the account pool table.
browser-service does not include the account id when reporting results; the account only lives in its in-process check-out slot and is popped
as soon as check-in happens, appearing only in logs; when the center persists, it only has the task's claimed_by (machine). So "which account
is refusing" could only be found by matching log timestamps, and the DB went no finer than "which machine".

Both columns are nullable, with only one write point, dispatch /result (the worker returns them with results; old workers not sending them means
NULL); answers run directly in the runner process with a local-file login session are also NULL. **No foreign keys**: when an account is deleted
(quarantine cleanup is routine) historical answers should not change with it; attribution is only a weak reference for debugging. session_id is indexed:
aggregating failure types by account is its only use, and it is a new query on a hot table.

Revision ID: e2b7c4d91a56
Revises: c4f7a2e91b58
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7c4d91a56"
down_revision: Union[str, None] = "c4f7a2e91b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "ai_telemetry_responses"


def _cols(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(_T)}


def _indexes(bind) -> set:
    return {i["name"] for i in sa.inspect(bind).get_indexes(_T)}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _cols(bind)
    # Idempotent: appdb is one DB shared by test host / prod, run once, but both sides' code must start (same as c4f7a2e91b58).
    if "worker_uid" not in cols:
        op.add_column(_T, sa.Column("worker_uid", sa.String(), nullable=True))
    if "session_id" not in cols:
        op.add_column(_T, sa.Column("session_id", sa.Integer(), nullable=True))
    if "ix_ai_telemetry_responses_session_id" not in _indexes(bind):
        op.create_index("ix_ai_telemetry_responses_session_id", _T, ["session_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "ix_ai_telemetry_responses_session_id" in _indexes(bind):
        op.drop_index("ix_ai_telemetry_responses_session_id", table_name=_T)
    cols = _cols(bind)
    if "session_id" in cols:
        op.drop_column(_T, "session_id")
    if "worker_uid" in cols:
        op.drop_column(_T, "worker_uid")
