"""Add meta_json to ai_telemetry_responses: persist engine-side collection parameters/state with the answer

First used for the Kimi integration (2026-09-18): the thinking switch is configurable, so cross-period report comparisons must first group by "the thinking setting actually in effect",
never mixing samples with different parameters; web search force:true only "allows" rather than forces search, so reports must distinguish
"zero citations" from "did not search" (search_triggered); inline <REF> citations in the body are stripped into inline_refs,
the raw material for fragment-level provenance. None of this had a home: the response table only has answer / citations / error / reasoning.

One nullable JSON text column. Write points: telemetry-service's save_response (two places in dispatch /result +
the runner push path), returned with results by browser-service; old workers / non-kimi engines -> NULL.
run-now previews are not persisted. ADD COLUMN NULL is a metadata operation on PG, with no table lock or rewrite.

Revision ID: d8e4a2c71f95
Revises: e2b7c4d91a56
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e4a2c71f95"
down_revision: Union[str, None] = "e2b7c4d91a56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "ai_telemetry_responses"


def _cols(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(_T)}


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent: appdb is one DB shared across environments, run once, but all code must start (same as e2b7c4d91a56).
    if "meta_json" not in _cols(bind):
        op.add_column(_T, sa.Column("meta_json", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "meta_json" in _cols(bind):
        op.drop_column(_T, "meta_json")
