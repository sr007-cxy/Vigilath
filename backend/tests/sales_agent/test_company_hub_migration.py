import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "e4f9c2a71b83_company_hub_scale_out.py"
    )
    spec = importlib.util.spec_from_file_location("company_hub_scale_out", migration_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_company_backfill_uses_batched_executemany():
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    companies = sa.Table(
        "sales_agent_companies",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
        sa.Column("website", sa.String),
        sa.Column("industry", sa.String),
        sa.Column("region", sa.String),
        sa.Column("source", sa.String),
        sa.Column("canonical_name", sa.String),
        sa.Column("normalized_domain", sa.String),
        sa.Column("industry_primary", sa.String),
        sa.Column("industry_tags_json", sa.Text),
        sa.Column("province", sa.String),
        sa.Column("city", sa.String),
        sa.Column("source_url", sa.String),
    )
    metadata.create_all(engine)
    update_batches = []

    @sa.event.listens_for(engine, "before_cursor_execute")
    def capture_updates(_conn, _cursor, statement, parameters, _context, executemany):
        if statement.lstrip().upper().startswith("UPDATE"):
            update_batches.append((executemany, len(parameters) if executemany else 1))

    with engine.begin() as connection:
        connection.execute(
            companies.insert(),
            [
                {
                    "id": company_id,
                    "name": f"测试公司（第{company_id}批）",
                    "website": f"https://www.example{company_id}.com/path",
                    "industry": "智能硬件 / 跨境电商",
                    "region": "广东省 深圳市",
                    "source": "",
                    "canonical_name": "",
                    "normalized_domain": "",
                    "industry_primary": "",
                    "industry_tags_json": "[]",
                    "province": "",
                    "city": "",
                    "source_url": "",
                }
                for company_id in range(1, 1202)
            ],
        )
        migration.op = SimpleNamespace(get_bind=lambda: connection)
        migration._backfill_companies()
        sample = connection.execute(
            sa.select(
                companies.c.canonical_name,
                companies.c.normalized_domain,
                companies.c.industry_primary,
                companies.c.province,
                companies.c.city,
            ).where(companies.c.id == 1)
        ).one()

    assert update_batches == [(True, 500), (True, 500), (True, 201)]
    assert sample == ("测试公司", "example1.com", "智能硬件", "广东省", "深圳市")
