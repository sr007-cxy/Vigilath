"""Add business registration source fields to sales_agent_companies (for persisting collected company data)

Adds 4 nullable (server_default="") fields to sales_agent_companies, holding business registration fields collected by
company-registry-collector (formerly enterprise-data-service) that the original slim table had nowhere to put:
    credit_code        unified social credit code (for dedup/alignment)
    registered_capital registered capital (raw string, e.g. "100万人民币" (1 million RMB))
    established_on      establishment date (string "YYYY-MM-DD", strings like the table's other fields)
    address            registered/detailed company address

Excludes band (capital bracket): a collection slicing artifact, derivable from registered_capital, not stored in the business table.

Revision ID: b6f1a3c9e274
Revises: a2d7f5c81e94, ac23de45fa67
Create Date: 2026-08-27

⚠️ Before applying, be sure to check the current head with `alembic heads`. At drafting time the repo had 2 unmerged heads:
    a2d7f5c81e94 (add_users_created_at)
    ac23de45fa67 (add_publish_account_warmup_daily_cap)
This migration uses a tuple down_revision to **hang off both heads at once**, merging the dual heads + adding columns in one step, leaving a single head after applying.
If the heads have changed by then, change the down_revision below to the revisions actually output by `alembic heads`.

Idempotent: each column is probed for existence first (_columns), safe to run repeatedly.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b6f1a3c9e274"
# Tuple = merge the two existing heads; if there is only one head, change it to that head's string
down_revision: Union[str, Sequence[str], None] = ("a2d7f5c81e94", "ac23de45fa67")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "sales_agent_companies"

_NEW_COLUMNS = (
    ("credit_code", sa.String(length=32)),
    ("registered_capital", sa.String(length=64)),
    ("established_on", sa.String(length=32)),
    ("address", sa.String(length=512)),
)


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    existing = _columns(_TABLE)
    for name, coltype in _NEW_COLUMNS:
        if name not in existing:
            op.add_column(
                _TABLE,
                sa.Column(name, coltype, nullable=False, server_default=""),
            )
    # Plain index on unified social credit code (for later dedup/alignment; not unique, nulls allowed)
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE) if i.get("name")}
    if "idx_sales_agent_companies_credit_code" not in idx and "credit_code" in _columns(_TABLE):
        op.create_index("idx_sales_agent_companies_credit_code", _TABLE, ["credit_code"])


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE) if i.get("name")}
    if "idx_sales_agent_companies_credit_code" in idx:
        op.drop_index("idx_sales_agent_companies_credit_code", table_name=_TABLE)
    existing = _columns(_TABLE)
    for name, _ in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
