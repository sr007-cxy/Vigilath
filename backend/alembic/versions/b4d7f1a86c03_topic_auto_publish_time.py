"""Topic-level publish time: auto_publish_time

Revision ID: b4d7f1a86c03
Revises: f2c8a91d64b3
Create Date: 2026-09-11

Publish time previously only had the global env `GEO_AUTO_PUBLISH_HOUR` (default 11), which users could not change, and it was asymmetric with
"generation time": auto_generate_time is configurable per topic, publish time was not.

With the column added, the due time of date-only rows (YYYY-MM-DD) is computed from the value configured on the topic. **Row level still takes precedence**:
rows with publish_date written as YYYY-MM-DDTHH:MM still publish at their time; this column only governs rows without a specific time.
Three-level priority = row-level time > topic auto_publish_time > global env fallback.

Default "11:00" matches the current GEO_AUTO_PUBLISH_HOUR=11 exactly: not a single existing topic's publish time changes.

server_default: appdb is shared across environments (prod and test host use the same table), so old rows must have a value immediately;
we cannot leave NULL for `String nullable=False` to blow up on read; and old code does not know this column,
so leaving NULL would only make whichever side deploys first error out.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4d7f1a86c03"
down_revision: Union[str, None] = "f2c8a91d64b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_telemetry_topics"
_COL = "auto_publish_time"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Idempotent: in some environments this table was created by create_all (models already declare this column)
    if not _has_column(_TABLE, _COL):
        op.add_column(_TABLE, sa.Column(
            _COL, sa.String(length=8),
            nullable=False, server_default="11:00"))


def downgrade() -> None:
    if _has_column(_TABLE, _COL):
        op.drop_column(_TABLE, _COL)
