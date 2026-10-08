"""Multi-brand: one account can have multiple business lines

One `sales_agent_seller_profiles` row = one brand. Previously every read path went through
`_latest_seller_profile(db, user_id)`, i.e. `order_by(id.desc()).first()`, latest row wins.
After filling in B's profile, A's was buried and could never be read again; the two overwrote each other.

**The backfill only keeps each user's latest row as the active brand.** Each user already has multiple rows in existing data,
which are artifacts of repeated historical edits, not real brands; treating each row as a brand would show old users
several fake brands as soon as they come in, and wrongly trigger the multi-brand selection card. After archiving, existing users have 1 active brand -> no selection card -> zero regression.

Also adds four email-setting columns to brands: previously the whole report email prompt was a hard-coded template, and the only differentiation was
the 5 recipient perspectives of style_key. A sells dental care, B sells SaaS, and apart from profile variables the emails sent out
followed exactly the same wording rules.

Revision ID: d5f1a7b93c48
Revises: c2e8a5d31f94
"""
from alembic import op
import sqlalchemy as sa

revision = "d5f1a7b93c48"
down_revision = "c2e8a5d31f94"
branch_labels = None
depends_on = None

TABLE = "sales_agent_seller_profiles"

# (column name, type, server_default)
_COLUMNS = (
    ("brand_key", sa.String(length=64), ""),
    ("sort_order", sa.Integer(), "100"),
    ("archived_at", sa.DateTime(), None),
    ("email_style_key", sa.String(length=32), ""),
    ("email_tone", sa.String(length=32), ""),
    ("email_prompt_extra", sa.Text(), ""),
    ("email_signature", sa.Text(), ""),
)

_ACTIVE_INDEX = "idx_sales_agent_seller_profiles_user_active"
_BRAND_UNIQUE = "ux_sales_agent_seller_profiles_user_brand"


def _inspector():
    return sa.inspect(op.get_bind())


def _existing_columns() -> set[str]:
    insp = _inspector()
    if TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(TABLE)}


def _existing_indexes() -> set[str]:
    insp = _inspector()
    if TABLE not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(TABLE)}


def upgrade() -> None:
    have = _existing_columns()
    if not have:
        return

    for name, type_, default in _COLUMNS:
        if name in have:
            continue
        op.add_column(TABLE, sa.Column(
            name, type_,
            nullable=(default is None),
            server_default=default,
        ))

    bind = op.get_bind()
    # The backfill order must not be reversed: fill brand_key with unique values first, then create the unique index;
    # the other way round conflicts immediately because the whole table has brand_key=''.
    bind.execute(sa.text(f"UPDATE {TABLE} SET brand_key = 'brand-' || id WHERE brand_key IS NULL OR brand_key = ''"))
    bind.execute(sa.text(f"UPDATE {TABLE} SET sort_order = 100 WHERE sort_order IS NULL"))
    bind.execute(sa.text(f"""
        UPDATE {TABLE} SET archived_at = COALESCE(updated_at, created_at)
        WHERE archived_at IS NULL
          AND id NOT IN (SELECT MAX(id) FROM {TABLE} GROUP BY user_id)
    """))
    bind.execute(sa.text(f"UPDATE {TABLE} SET name = '默认品牌' WHERE name = '默认卖家服务画像'"))

    indexes = _existing_indexes()
    if _ACTIVE_INDEX not in indexes:
        op.create_index(_ACTIVE_INDEX, TABLE, ["user_id", "archived_at", "sort_order"])
    if _BRAND_UNIQUE not in indexes:
        op.create_index(_BRAND_UNIQUE, TABLE, ["user_id", "brand_key"], unique=True)


def downgrade() -> None:
    have = _existing_columns()
    if not have:
        return
    indexes = _existing_indexes()
    for name in (_BRAND_UNIQUE, _ACTIVE_INDEX):
        if name in indexes:
            op.drop_index(name, table_name=TABLE)
    # The archive flag cannot be reverted: the information of which rows were active before the version simply does not exist.
    # Just drop the columns; the data itself is untouched.
    for name, _type, _default in reversed(_COLUMNS):
        if name in have:
            op.drop_column(TABLE, name)
