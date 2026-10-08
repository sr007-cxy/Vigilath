"""Add frontend public_id to Sales Agent sessions

Revision ID: a6e9c2f8d431
Revises: f6a2d4c8b931
Create Date: 2026-08-12
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "a6e9c2f8d431"
down_revision: Union[str, None] = "f6a2d4c8b931"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "sales_agent_chat_sessions"
_INDEX = "ux_sales_agent_sessions_public_id"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _cols(name: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(name)}


def _indexes(name: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(name) if i.get("name")}


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    if "public_id" not in _cols(_TABLE):
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True, comment="前端公开会话 UUID"))

    table = sa.table(_TABLE, sa.column("id", sa.Integer()), sa.column("public_id", sa.String(length=36)))
    bind = op.get_bind()
    rows = bind.execute(sa.select(table.c.id).where((table.c.public_id.is_(None)) | (table.c.public_id == ""))).fetchall()
    for row in rows:
        bind.execute(table.update().where(table.c.id == row.id).values(public_id=str(uuid.uuid4())))

    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.alter_column("public_id", existing_type=sa.String(length=36), nullable=False)

    if _INDEX not in _indexes(_TABLE):
        op.create_index(_INDEX, _TABLE, ["public_id"], unique=True)


def downgrade() -> None:
    if not _has_table(_TABLE) or "public_id" not in _cols(_TABLE):
        return
    if _INDEX in _indexes(_TABLE):
        op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_column("public_id")
