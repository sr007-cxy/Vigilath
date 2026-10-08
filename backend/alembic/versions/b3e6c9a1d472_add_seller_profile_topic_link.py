"""The brand profile becomes the single source of truth for profiles: link brands to a source topic + report capability flag

Customers carefully filled in a 6-module brand profile at `/workbench/topics/:id/edit`
(`ai_telemetry_topics.profile_json`), while the sales assistant had a separate
"our profile" (`sales_agent_seller_profiles`) gathered question by question in chat. The two were unrelated: what customers filled in never reached the sales pipeline,
the sales side asked all over again, and the contents did not match.

Here ownership moves to the profile side: 1 topic = 1 brand, and profile content is
written one-way into this table by `brandprofile.sync_brand_from_topic()`. Two columns are added:

- `topic_id`: which topic this brand's profile comes from. Empty = legacy orphan brand, **kept usable**,
  just labeled "no linked brand profile", never auto-deleted (explicit user request).
- `report_capable`: whether the profile's service capabilities contain keywords like "audit/diagnosis/report/auditing...".
  If not, no report button. Defaults to false: orphan brands have no profile, so conservatively assume none.

The backfill claiming strategy is deliberately conservative, accepting only two cases that cannot be wrong:
1. The account has exactly one active brand + exactly one topic -> pair directly (the vast majority of existing data);
2. The brand name is **exactly equal** to the topic's profile short name/full name/name -> pair.
All others keep topic_id empty for the user to claim on the admin page. No fuzzy matching: a wrong pairing would write
A's profile into B's profile, worse than no pairing.

Revision ID: b3e6c9a1d472
Revises: a2d7f5c81e94
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "b3e6c9a1d472"
down_revision = "a2d7f5c81e94"
branch_labels = None
depends_on = None

TABLE = "sales_agent_seller_profiles"
TOPIC_TABLE = "ai_telemetry_topics"

_TOPIC_INDEX = "idx_sales_agent_seller_profiles_topic"
_TOPIC_UNIQUE = "ux_sales_agent_seller_profiles_user_topic"


def _inspector():
    return sa.inspect(op.get_bind())


def _existing_columns(table: str = TABLE) -> set[str]:
    insp = _inspector()
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _existing_indexes() -> set[str]:
    insp = _inspector()
    if TABLE not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(TABLE)}


def _profile_names(raw: str | None) -> set[str]:
    """The fields in a topic profile that may serve as the brand name (lowercased, whitespace removed, for exact matching)."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    out = set()
    for key in ("company_short_name", "company_full_name", "profile_name"):
        text = str(data.get(key) or "").strip().casefold()
        if text:
            out.add(text)
    return out


def _claim_topics(bind) -> None:
    """Pair topics with brands. Only accepts "one-to-one" and "same name"; leave empty if no pairing."""
    if TOPIC_TABLE not in _inspector().get_table_names():
        return

    brands = bind.execute(sa.text(
        f"SELECT id, user_id, name FROM {TABLE} WHERE archived_at IS NULL ORDER BY user_id, sort_order, id"
    )).fetchall()
    topics = bind.execute(sa.text(
        f"SELECT id, user_id, name, target, profile_json FROM {TOPIC_TABLE} ORDER BY user_id, id"
    )).fetchall()

    by_user_brands: dict[int, list] = {}
    for row in brands:
        by_user_brands.setdefault(row.user_id, []).append(row)
    by_user_topics: dict[int, list] = {}
    for row in topics:
        by_user_topics.setdefault(row.user_id, []).append(row)

    pairs: list[tuple[int, int]] = []  # (topic_id, brand_id)
    for user_id, user_brands in by_user_brands.items():
        user_topics = by_user_topics.get(user_id) or []
        if not user_topics:
            continue
        if len(user_brands) == 1 and len(user_topics) == 1:
            pairs.append((user_topics[0].id, user_brands[0].id))
            continue
        taken: set[int] = set()
        for brand in user_brands:
            key = str(brand.name or "").strip().casefold()
            if not key:
                continue
            for topic in user_topics:
                if topic.id in taken:
                    continue
                candidates = _profile_names(topic.profile_json)
                candidates |= {str(topic.name or "").strip().casefold(), str(topic.target or "").strip().casefold()}
                candidates.discard("")
                if key in candidates:
                    taken.add(topic.id)
                    pairs.append((topic.id, brand.id))
                    break

    for topic_id, brand_id in pairs:
        bind.execute(
            sa.text(f"UPDATE {TABLE} SET topic_id = :topic_id WHERE id = :brand_id"),
            {"topic_id": topic_id, "brand_id": brand_id},
        )


def upgrade() -> None:
    have = _existing_columns()
    if not have:
        return

    if "topic_id" not in have:
        # No FK constraint: adding a column with a foreign key in SQLite requires rebuilding the whole table, and this table
        # already has data and indexes on several deployments. The ORM side still declares the FK; business reads/writes all go by whether topic_id is empty.
        op.add_column(TABLE, sa.Column("topic_id", sa.Integer(), nullable=True))
    if "report_capable" not in have:
        # server_default must use `sa.false()` rather than `sa.text("0")`: PostgreSQL
        # boolean does not accept integer literals, and `DEFAULT 0` fails with "default expression is of
        # type integer", killing the whole migration. `sa.false()` is rendered by the dialect: `false` on PG,
        # `0` on SQLite, both correct. Production is PG; do not change it back to a literal.
        op.add_column(TABLE, sa.Column(
            "report_capable", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))

    bind = op.get_bind()
    # Likewise, raw SQL `SET report_capable = 0` also errors on PG due to a type mismatch.
    # Use SQLAlchemy's update() so the dialect renders the boolean.
    # (The column is created NOT NULL with a default, so in theory there are no NULLs; this guards the half-finished state
    #  where the column already exists and was originally created nullable.)
    _capable = sa.table(TABLE, sa.column("report_capable", sa.Boolean()))
    bind.execute(
        sa.update(_capable).where(_capable.c.report_capable.is_(None)).values(report_capable=False)
    )
    _claim_topics(bind)

    indexes = _existing_indexes()
    if _TOPIC_INDEX not in indexes:
        op.create_index(_TOPIC_INDEX, TABLE, ["topic_id"])
    if _TOPIC_UNIQUE not in indexes:
        # No partial index: NULLs are not equal to each other in unique indexes (both SQLite and PostgreSQL),
        # so orphan brands with empty topic_id can have any number of rows, while each claimed profile can have only one.
        op.create_index(_TOPIC_UNIQUE, TABLE, ["user_id", "topic_id"], unique=True)


def downgrade() -> None:
    have = _existing_columns()
    if not have:
        return
    indexes = _existing_indexes()
    for name in (_TOPIC_UNIQUE, _TOPIC_INDEX):
        if name in indexes:
            op.drop_index(name, table_name=TABLE)
    for name in ("report_capable", "topic_id"):
        if name in have:
            op.drop_column(TABLE, name)
