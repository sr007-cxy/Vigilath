"""Add a provider column to the paid publishing media mirror (Ruanwenjiang + Meijieku sites share the table)

Meijieku (vip.meijieku.com) is the upstream site of Ruanwenjiang (tenant.meijieku.com), with an isomorphic protocol and
the same resource pool, but prices are computed per each site's membership tier. Both must be mirrored into rwj_media_resources to compare prices,
and **resource_id cannot be shared across sites** (each has its own numbering), so:

  - add a provider column, backfilling all existing rows with 'rwj' (only Ruanwenjiang was synced before)
  - change the unique key from (res_type, resource_id) to (provider, res_type, resource_id):
    without this, Meijieku's first sync would hit the unique key massively
  - rwj_sync_runs also gets provider, so batch records are viewed per site

Revision ID: c9f1a4d7b230
Revises: d8b3e6c04f17
Create Date: 2026-08-11
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "c9f1a4d7b230"
down_revision = "d8b3e6c04f17"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _cols(name: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(name)}


def _constraints(name: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    out = {c["name"] for c in insp.get_unique_constraints(name) if c.get("name")}
    out |= {i["name"] for i in insp.get_indexes(name) if i.get("name")}
    return out


_UQ_CLAUSE = re.compile(
    r",\s*CONSTRAINT\s+uq_rwj_media_type_rid\s+UNIQUE\s*\([^)]*\)", re.I)


def _sqlite_drop_old_unique() -> None:
    """Replace the table-level unique constraint on SQLite: only possible via a copy-and-move full table rebuild.

    The old constraint is an inline UNIQUE(res_type, resource_id) in CREATE TABLE, which SQLite
    stores as sqlite_autoindex that can be neither dropped nor bypassed: without a rebuild Meijieku's first sync would
    massively hit the unique key on "same number at both sites". The approach is to extract the original CREATE statement, remove that constraint,
    replay it, then recreate the original indexes as-is (indexes disappear with DROP TABLE).
    """
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='rwj_media_resources'"
    )).scalar()
    if not row or "uq_rwj_media_type_rid" not in row:
        return
    idx_sqls = [r[0] for r in bind.execute(sa.text(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name='rwj_media_resources' AND sql IS NOT NULL"
    )).fetchall()]
    new_ddl = _UQ_CLAUSE.sub("", row).replace(
        "rwj_media_resources", "rwj_media_resources_new", 1)
    cols = [c["name"] for c in sa.inspect(bind).get_columns("rwj_media_resources")]
    collist = ", ".join(f'"{c}"' for c in cols)
    op.execute(sa.text(new_ddl))
    op.execute(sa.text(
        f"INSERT INTO rwj_media_resources_new ({collist}) "
        f"SELECT {collist} FROM rwj_media_resources"))
    op.execute(sa.text("DROP TABLE rwj_media_resources"))
    op.execute(sa.text(
        "ALTER TABLE rwj_media_resources_new RENAME TO rwj_media_resources"))
    for sql in idx_sqls:
        op.execute(sa.text(sql))


def upgrade() -> None:
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"

    if _has_table("rwj_media_resources"):
        if "provider" not in _cols("rwj_media_resources"):
            op.add_column(
                "rwj_media_resources",
                sa.Column("provider", sa.String(length=8), nullable=False,
                          server_default="rwj"),
            )
        # The old unique key (res_type, resource_id) must give way: the two sites number independently
        if sqlite:
            _sqlite_drop_old_unique()
        elif "uq_rwj_media_type_rid" in _constraints("rwj_media_resources"):
            op.drop_constraint("uq_rwj_media_type_rid", "rwj_media_resources",
                               type_="unique")
        existing = _constraints("rwj_media_resources")
        if "uq_rwj_media_prov_type_rid" not in existing:
            if sqlite:
                op.create_index("uq_rwj_media_prov_type_rid", "rwj_media_resources",
                                ["provider", "res_type", "resource_id"], unique=True)
            else:
                op.create_unique_constraint(
                    "uq_rwj_media_prov_type_rid", "rwj_media_resources",
                    ["provider", "res_type", "resource_id"])
        if "idx_rwj_media_provider" not in existing:
            op.create_index("idx_rwj_media_provider", "rwj_media_resources",
                            ["provider", "is_active"])

    if _has_table("rwj_sync_runs"):
        if "provider" not in _cols("rwj_sync_runs"):
            op.add_column(
                "rwj_sync_runs",
                sa.Column("provider", sa.String(length=8), nullable=False,
                          server_default="rwj"),
            )
        if "idx_rwj_sync_provider_started" not in _constraints("rwj_sync_runs"):
            op.create_index("idx_rwj_sync_provider_started", "rwj_sync_runs",
                            ["provider", "started_at"])


def downgrade() -> None:
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"

    if _has_table("rwj_sync_runs"):
        if "idx_rwj_sync_provider_started" in _constraints("rwj_sync_runs"):
            op.drop_index("idx_rwj_sync_provider_started", table_name="rwj_sync_runs")
        if "provider" in _cols("rwj_sync_runs"):
            op.drop_column("rwj_sync_runs", "provider")

    if _has_table("rwj_media_resources"):
        existing = _constraints("rwj_media_resources")
        if "idx_rwj_media_provider" in existing:
            op.drop_index("idx_rwj_media_provider", table_name="rwj_media_resources")
        if "uq_rwj_media_prov_type_rid" in existing:
            if sqlite:
                op.drop_index("uq_rwj_media_prov_type_rid", table_name="rwj_media_resources")
            else:
                op.drop_constraint("uq_rwj_media_prov_type_rid", "rwj_media_resources",
                                   type_="unique")
        if not sqlite and "uq_rwj_media_type_rid" not in existing:
            # Non-rwj rows must be cleared before downgrading, otherwise the old unique key cannot be created
            op.execute(sa.text("DELETE FROM rwj_media_resources WHERE provider <> 'rwj'"))
            op.create_unique_constraint("uq_rwj_media_type_rid", "rwj_media_resources",
                                        ["res_type", "resource_id"])
        if "provider" in _cols("rwj_media_resources"):
            op.drop_column("rwj_media_resources", "provider")
