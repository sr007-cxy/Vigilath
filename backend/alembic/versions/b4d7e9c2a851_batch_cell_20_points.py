"""batch_cell 4 -> 20 credits (¥0.2 per cell)

Revision ID: b4d7e9c2a851
Revises: f8b3d5c91a20
Create Date: 2026-08-25

"One cell" = one monitoring question x one engine, run once (metric definition in services/batch_billing.py).

Why a new migration instead of changing f8b3d5c91a20: that one has already run in production, and changing an applied migration would make
the file disagree with what actually happened in the DB; this repo was just bitten once by "migration files lagging behind appdb".
Price changes go through migrations anyway (f8b3d5c91a20 itself did exactly that), so appending one is most straightforward.

★ Knock-on effect: full diagnosis charges on the spot by "actual query count x engine count x batch_cell"
  (services/full_diagnosis_generator.py::_charge_full_diagnosis),
  so a diagnosis with 40 queries x 5 platforms goes from 800 credits (¥8) to 4000 credits (¥40).
  When the balance is insufficient it only skips the heavy engine stage and still delivers site-side results; the order never fails entirely.
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d7e9c2a851'
down_revision: Union[str, None] = 'f8b3d5c91a20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = 20
_OLD = 4


def _set(conn, cents: int) -> None:
    now = datetime.utcnow()
    res = conn.execute(
        sa.text("UPDATE balance_prices SET price_cents = :p, enabled = 1, "
                "updated_at = :now WHERE key = 'batch_cell'"),
        {"p": cents, "now": now},
    )
    if res.rowcount == 0:
        conn.execute(
            sa.text("INSERT INTO balance_prices (key, price_cents, enabled, updated_at) "
                    "VALUES ('batch_cell', :p, 1, :now)"),
            {"p": cents, "now": now},
        )


def upgrade() -> None:
    _set(op.get_bind(), _NEW)


def downgrade() -> None:
    _set(op.get_bind(), _OLD)
