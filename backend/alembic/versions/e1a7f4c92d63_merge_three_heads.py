"""merge three heads (a4c7e1d93f52 + c2f6a9d13e70 + d7c3f9b18e40)

Revision ID: e1a7f4c92d63
Revises: a4c7e1d93f52, c2f6a9d13e70, d7c3f9b18e40
Create Date: 2026-08-20 16:00:00.000000

Pure merge, no DDL at all.

Why it must be merged: prod's geo.service runs `alembic upgrade head` (singular) in ExecStartPre,
and once the repo grew multiple heads this fails with "Multiple head revisions are present" -> ExecStartPre fails
-> the whole backend cannot start (this already capsized once on 2026-08-20). Merging the three heads into one gives `upgrade head` a unique solution.

The DB side is already at these three heads, so this migration is a no-op on existing DBs and only converges the version number to one.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision: str = "e1a7f4c92d63"
down_revision: Union[str, Sequence[str], None] = ("a4c7e1d93f52", "c2f6a9d13e70", "d7c3f9b18e40")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
