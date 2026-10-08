"""Drop the four brand-level email-setting columns: /sales-agent/admin was removed and they have no write path

email_style_key / email_tone / email_prompt_extra / email_signature on `sales_agent_seller_profiles`
were for the hidden admin page `/sales-agent/admin`:
letting users configure per business line a recipient perspective, a tone word, an extra prompt snippet and a signature.

That page was deleted on 2026-08-27 (brands are now mirrors of `ai_telemetry_topics.profile_json`,
created/refreshed automatically by `sync_brand_from_topic()`, and the sales side no longer has a manual add/edit entry).
`PATCH /sales-agent/brands` was the **only** write path for these four columns; with that route removed too they
are forever the empty strings from row creation, and every read takes the fallback branch with empty values.

Email wording now comes entirely from the brand profile: the main material is `email_material` (core_message /
brand_slogan / service_features / target_scenarios / contacts ...), and the recipient perspective
is still passed as `style_key` from the frontend dropdown when sending; that setting is not persisted and is unaffected.

**This is lossy**: signatures and extra prompts that existing accounts wrote on the admin page disappear with the columns.
downgrade can only add empty columns back; the content cannot be recovered. To keep a record, first
`SELECT id, user_id, name, email_style_key, email_tone, email_prompt_extra,
email_signature FROM sales_agent_seller_profiles WHERE email_prompt_extra <> ''
OR email_signature <> '' OR email_style_key <> '' OR email_tone <> '';`
export a copy, then upgrade.

Revision ID: f2a8c4d61e05
Revises: b3e6c9a1d472
Create Date: 2026-08-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a8c4d61e05"
down_revision = "b3e6c9a1d472"
branch_labels = None
depends_on = None

TABLE = "sales_agent_seller_profiles"
COLUMNS = ("email_style_key", "email_tone", "email_prompt_extra", "email_signature")


def _existing_columns() -> set[str]:
    """Reflect before acting: these four columns were added by d5f1a7b93c48, and that migration itself was written as "skip if present",
    so we cannot assume every DB really has them."""
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(TABLE)}


def upgrade() -> None:
    present = _existing_columns()
    targets = [name for name in COLUMNS if name in present]
    if not targets:
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for name in targets:
            batch_op.drop_column(name)


def downgrade() -> None:
    """Add the empty columns back. The content cannot possibly be restored; see the module header."""
    present = _existing_columns()
    if not present:
        return
    specs = (
        ("email_style_key", sa.String(length=32)),
        ("email_tone", sa.String(length=32)),
        ("email_prompt_extra", sa.Text()),
        ("email_signature", sa.Text()),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for name, type_ in specs:
            if name in present:
                continue
            batch_op.add_column(
                sa.Column(name, type_, nullable=False, server_default=""),
            )
