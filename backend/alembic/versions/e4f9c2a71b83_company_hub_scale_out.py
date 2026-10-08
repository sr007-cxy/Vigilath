"""Company recommendation data source DB: expand the company table + three new tables for sources/batches/signals

Background: the company candidate pool is currently hard-coded in `agents/sales_agent/seed_companies.py` (130 companies),
and `sales_agent_companies` has only 9 columns. Once data volume grows, these are the first things to break:

- `service/companies.py` loads every company with a website into memory as a dedup dict on each discovery;
- `service/recommend.py` does `order_by(updated_at).limit(n*2)` before scoring, so once the candidate pool
  is large it only picks among "the 2n most recently updated", skewing the pool;
- industry/region use `ilike('%x%')` to match concatenated strings like `'广东省 深圳市'` (Guangdong Province, Shenzhen) and `'智能硬件 / 跨境电商'` (smart hardware / cross-border e-commerce),
  which can neither use an index nor filter accurately.

So this migration does three things:

1. Add normalized columns to `sales_agent_companies` (`normalized_domain` / `canonical_name`
   / `province` / `city` / `industry_primary`) with indexes, pushing dedup and filtering down into the DB;
2. Add `sales_agent_company_sources` (sources can be down-weighted/disabled in bulk),
   `sales_agent_company_import_batches` (imports can be investigated and rolled back),
   `sales_agent_company_signals` (reusable company-level signals);
3. Backfill the normalized columns, and create unique indexes only when **no duplicates are confirmed**.

Deliberately not done:

- No existing table is deleted, renamed or moved: everything is added columns and tables, so existing data and other modules are unaffected;
- When existing data has duplicate domains there is **no automatic merge**; it degrades to a plain index and prints a to-do list. Automatic merging
  would merge A's contact info into B, worse than keeping duplicates;
- No PG schema. Tests all use `create_engine("sqlite://")` + `create_all`,
  and giving tables a schema makes SQLite treat it as an ATTACH database name and error out, after which every engine must remember
  to set `schema_translate_map`. If separate backup/authorization is ever really needed, PG's
  `ALTER TABLE ... SET SCHEMA` is a metadata operation and foreign keys follow automatically, so moving then is not too late.

`sales_agent_lead_evidences` stays as-is: it is **an immutable evidence snapshot of one recommendation** (bound to
campaign/lead), while the new `company_signals` are **company-level facts** (bound to company, updatable,
expirable, reused across campaigns). Without separating them, evidence rows = campaigns x companies per run.

Revision ID: e4f9c2a71b83
Revises: f2a8c4d61e05

Following f2a8c4d61e05 is **not** because of a dependency: that one drops
the four email-setting columns of `sales_agent_seller_profiles`, unrelated to the company DB. Simply
both were written after b3e6c9a1d472; whichever committed first goes first, and the later one was repointed to keep a single head.
The two do not affect each other, and would run just as well in reverse order.
"""
from urllib.parse import urlsplit

from alembic import op
import sqlalchemy as sa

revision = "e4f9c2a71b83"
down_revision = "f2a8c4d61e05"
branch_labels = None
depends_on = None

COMPANIES = "sales_agent_companies"
CONTACTS = "sales_agent_company_contacts"
SOURCES = "sales_agent_company_sources"
BATCHES = "sales_agent_company_import_batches"
SIGNALS = "sales_agent_company_signals"

# Municipalities / special administrative regions have no "province + city" levels; listed separately to avoid splitting 「北京市」 ("Beijing") into province=Beijing, city=empty.
_MUNICIPALITIES = ("北京", "上海", "天津", "重庆", "香港", "澳门")
_PROVINCE_SUFFIXES = ("省", "自治区", "特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区")


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {i["name"] for i in _inspector().get_indexes(table) if i.get("name")}


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _add_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _drop_column(table: str, name: str) -> None:
    if name in _columns(table):
        op.drop_column(table, name)


def _drop_index(name: str, table: str) -> None:
    if name in _indexes(table):
        op.drop_index(name, table_name=table)


# --------------------------------------------------------------------------
# Normalization: how dedup keys are computed
# --------------------------------------------------------------------------

def _normalize_domain(website: str) -> str:
    """`https://WWW.Example.com/contact/` -> `example.com`. Returns an empty string if it cannot be computed."""
    raw = (website or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = urlsplit(raw).hostname or ""
    except ValueError:
        return ""
    host = host.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    # Bare IPs or hostnames without a dot are not company identities; better to fall back to name dedup
    if not host or "." not in host:
        return ""
    return host[:255]


def _canonical_name(name: str) -> str:
    """Remove content inside full-width/half-width parentheses and whitespace, for dedup of companies without a website."""
    text = (name or "").strip()
    for lb, rb in (("（", "）"), ("(", ")"), ("[", "]"), ("【", "】")):
        while lb in text and rb in text[text.index(lb) :]:
            start = text.index(lb)
            end = text.index(rb, start)
            text = (text[:start] + text[end + 1 :]).strip()
    return "".join(text.split())[:255]


def _split_region(region: str) -> tuple[str, str]:
    """`'广东省 深圳市'` -> `('广东省', '深圳市')`; `'北京市'` -> `('北京市', '北京市')` (province/city split, e.g. Guangdong/Shenzhen, Beijing/Beijing)."""
    text = (region or "").strip()
    if not text:
        return "", ""
    parts = [p for p in text.replace("/", " ").replace(",", " ").replace("，", " ").split() if p]
    if not parts:
        return "", ""
    head = parts[0]
    if any(head.startswith(m) for m in _MUNICIPALITIES):
        return head[:64], (parts[1][:64] if len(parts) > 1 else head[:64])
    if len(parts) >= 2:
        return head[:64], parts[1][:64]
    # Only one segment: treat it as the province if it has a province-level suffix, otherwise as the city
    if any(head.endswith(s) for s in _PROVINCE_SUFFIXES):
        return head[:64], ""
    return "", head[:64]


def _primary_industry(industry: str) -> str:
    """`'智能硬件 / 跨境电商 / OEM'` -> `'智能硬件'` (smart hardware). Only takes the first segment, no vocabulary mapping."""
    text = (industry or "").strip()
    if not text:
        return ""
    for sep in ("/", "|", "、", ",", "，", ">"):
        if sep in text:
            text = text.split(sep)[0]
            break
    return text.strip()[:64]


def _industry_tags(industry: str) -> list[str]:
    text = (industry or "").strip()
    if not text:
        return []
    for sep in ("|", "、", ",", "，", ">"):
        text = text.replace(sep, "/")
    return [t.strip()[:64] for t in text.split("/") if t.strip()]


# --------------------------------------------------------------------------


def _create_sources() -> None:
    if _has_table(SOURCES):
        return
    op.create_table(
        SOURCES,
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="manual_import",
                  comment="来源类型:manual_import/public_website/search_result/directory/detection_report/authorized_upload"),
        sa.Column("source_name", sa.String(length=255), nullable=False, server_default="", comment="来源名称,例如「用户导入表格」「某行业协会公开目录」"),
        sa.Column("source_url", sa.String(length=512), nullable=False, server_default="", comment="来源 URL,可为空"),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, comment="导入用户 ID,系统任务为空"),
        sa.Column("trust_level", sa.String(length=16), nullable=False, server_default="medium", comment="来源可信度:high/medium/low"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100", comment="推荐打分权重,批量降权用"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="来源失效时一键停用其名下全部数据"),
        sa.Column("terms_note", sa.Text(), nullable=False, server_default="", comment="来源使用限制说明"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 企业数据来源登记表",
    )
    op.create_index("ix_sales_agent_company_sources_id", SOURCES, ["id"])
    op.create_index("idx_sales_agent_company_sources_type", SOURCES, ["source_type", "is_enabled"])


def _create_batches() -> None:
    if _has_table(BATCHES):
        return
    op.create_table(
        BATCHES,
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, comment="发起用户 ID,系统任务为空"),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey(f"{SOURCES}.id"), nullable=True, comment="本批数据所属来源 ID"),
        sa.Column("batch_type", sa.String(length=32), nullable=False, server_default="manual_import",
                  comment="批次类型:seed_import/manual_import/search_discovery/website_crawl/report_import"),
        sa.Column("request_params_json", sa.Text(), nullable=False, server_default="{}", comment="行业/地区/关键词/数量等请求参数 JSON 字符串"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending", comment="批次状态:pending/running/completed/partial/failed"),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default="0", comment="请求数量"),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0", comment="发现企业数"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0", comment="新增企业数"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0", comment="更新企业数"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0", comment="判定为重复的数量"),
        sa.Column("blocked_count", sa.Integer(), nullable=False, server_default="0", comment="因来源不合规被拦截的数量"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0", comment="失败数量"),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default="", comment="错误摘要"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        comment="Sales Agent 企业数据导入批次表",
    )
    op.create_index("ix_sales_agent_company_import_batches_id", BATCHES, ["id"])
    op.create_index("idx_sales_agent_company_batches_status", BATCHES, ["status", "created_at"])
    op.create_index("idx_sales_agent_company_batches_user", BATCHES, ["user_id", "created_at"])


def _create_signals() -> None:
    if _has_table(SIGNALS):
        return
    op.create_table(
        SIGNALS,
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey(f"{COMPANIES}.id"), nullable=False, comment="所属企业 ID"),
        sa.Column("signal_type", sa.String(length=64), nullable=False, server_default="firmographic",
                  comment="信号类型:industry_match/region_match/content_asset/seo_signal/geo_relevance/contact_available/growth_signal"),
        sa.Column("signal_key", sa.String(length=128), nullable=False, server_default="", comment="同类信号幂等键"),
        sa.Column("signal_text", sa.Text(), nullable=False, server_default="", comment="信号摘要"),
        sa.Column("signal_value", sa.String(length=255), nullable=False, server_default="", comment="结构化值,便于筛选排序"),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey(f"{SOURCES}.id"), nullable=True, comment="来源 ID"),
        sa.Column("source_url", sa.String(length=512), nullable=False, server_default="", comment="来源页面 URL"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表名,只有真实报告才填"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID,只有真实报告才填"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="medium", comment="置信度:high/medium/low"),
        sa.Column("extracted_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="抽取时间"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="失效时间,过期信号不进推荐理由"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否仍可用于推荐"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 企业级可复用推荐信号表",
    )
    op.create_index("ix_sales_agent_company_signals_id", SIGNALS, ["id"])
    op.create_index("ix_sales_agent_company_signals_company_id", SIGNALS, ["company_id"])
    op.create_index("idx_sales_agent_company_signals_active", SIGNALS, ["company_id", "signal_type", "is_active"])
    op.create_index("ux_sales_agent_company_signals_key", SIGNALS, ["company_id", "signal_type", "signal_key"], unique=True)


def _extend_companies() -> None:
    _add_column(COMPANIES, sa.Column("canonical_name", sa.String(length=255), nullable=False, server_default="", comment="规范企业名,无官网时的去重依据"))
    _add_column(COMPANIES, sa.Column("normalized_domain", sa.String(length=255), nullable=False, server_default="", comment="规范化官网域名,主去重键"))
    _add_column(COMPANIES, sa.Column("industry_primary", sa.String(length=64), nullable=False, server_default="", comment="一级行业,用于走索引筛选"))
    _add_column(COMPANIES, sa.Column("industry_tags_json", sa.Text(), nullable=False, server_default="[]", comment="原始行业标签数组 JSON 字符串"))
    _add_column(COMPANIES, sa.Column("province", sa.String(length=64), nullable=False, server_default="", comment="省份,从 region 拆出"))
    _add_column(COMPANIES, sa.Column("city", sa.String(length=64), nullable=False, server_default="", comment="城市,从 region 拆出"))
    _add_column(COMPANIES, sa.Column("business_summary", sa.Text(), nullable=False, server_default="", comment="业务摘要"))
    _add_column(COMPANIES, sa.Column("source_url", sa.String(length=512), nullable=False, server_default="", comment="企业数据来源页面 URL"))
    _add_column(COMPANIES, sa.Column("source_id", sa.Integer(), nullable=True, comment="首次来源 ID"))
    _add_column(COMPANIES, sa.Column("import_batch_id", sa.Integer(), nullable=True, comment="首次导入批次 ID,用于整批回滚"))
    _add_column(COMPANIES, sa.Column("data_status", sa.String(length=32), nullable=False, server_default="active", comment="数据状态:active/needs_review/duplicate/invalid/restricted"))
    _add_column(COMPANIES, sa.Column("merged_into_id", sa.Integer(), nullable=True, comment="判重合并后指向存活行,不物理删"))
    _add_column(COMPANIES, sa.Column("confidence", sa.String(length=16), nullable=False, server_default="medium", comment="数据置信度:high/medium/low"))
    _add_column(COMPANIES, sa.Column("last_verified_at", sa.DateTime(), nullable=True, comment="最近验证时间,用于轮转复验"))


def _extend_contacts() -> None:
    _add_column(CONTACTS, sa.Column("source_id", sa.Integer(), nullable=True, comment="联系方式来源 ID"))
    _add_column(CONTACTS, sa.Column("last_verified_at", sa.DateTime(), nullable=True, comment="最近验证时间,退信后回写"))
    _add_column(CONTACTS, sa.Column("bounce_count", sa.Integer(), nullable=False, server_default="0", comment="连续退信次数,到阈值自动置 is_reachable=false"))


def _add_foreign_keys() -> None:
    """SQLite does not support ALTER TABLE ADD CONSTRAINT; foreign keys are only added on PG.

    SQLite is only used in tests and does not enable the foreign_keys pragma by default, so missing these constraints does not affect assertions;
    the ORM side still declares ForeignKey, and `create_all` inlines REFERENCES into CREATE TABLE.
    """
    if not _is_postgres():
        return
    existing = {fk.get("name") for fk in _inspector().get_foreign_keys(COMPANIES)}
    if "fk_sales_agent_companies_source" not in existing:
        op.create_foreign_key("fk_sales_agent_companies_source", COMPANIES, SOURCES, ["source_id"], ["id"])
    if "fk_sales_agent_companies_batch" not in existing:
        op.create_foreign_key("fk_sales_agent_companies_batch", COMPANIES, BATCHES, ["import_batch_id"], ["id"])
    if "fk_sales_agent_companies_merged_into" not in existing:
        op.create_foreign_key("fk_sales_agent_companies_merged_into", COMPANIES, COMPANIES, ["merged_into_id"], ["id"])
    contact_fks = {fk.get("name") for fk in _inspector().get_foreign_keys(CONTACTS)}
    if "fk_sales_agent_company_contacts_source" not in contact_fks:
        op.create_foreign_key("fk_sales_agent_company_contacts_source", CONTACTS, SOURCES, ["source_id"], ["id"])


def _backfill_companies() -> None:
    """Backfill existing name/website/industry/region into the normalized columns in batches.

    Each batch issues a single executemany. The old implementation read only 500 rows at a time but still called
    ``execute(UPDATE ...)`` row by row; on a remote PostgreSQL 25k rows meant 25k network
    round trips, dragging this revision into a long transaction.
    """
    import json

    bind = op.get_bind()
    table = sa.table(
        COMPANIES,
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("website", sa.String),
        sa.column("industry", sa.String),
        sa.column("region", sa.String),
        sa.column("source", sa.String),
        sa.column("canonical_name", sa.String),
        sa.column("normalized_domain", sa.String),
        sa.column("industry_primary", sa.String),
        sa.column("industry_tags_json", sa.Text),
        sa.column("province", sa.String),
        sa.column("city", sa.String),
        sa.column("source_url", sa.String),
    )
    update_stmt = (
        sa.update(table)
        .where(table.c.id == sa.bindparam("_company_id"))
        .values(
            canonical_name=sa.bindparam("_canonical_name"),
            normalized_domain=sa.bindparam("_normalized_domain"),
            industry_primary=sa.bindparam("_industry_primary"),
            industry_tags_json=sa.bindparam("_industry_tags_json"),
            province=sa.bindparam("_province"),
            city=sa.bindparam("_city"),
        )
    )

    last_id, batch_size, total = 0, 500, 0
    while True:
        rows = bind.execute(
            sa.select(table.c.id, table.c.name, table.c.website, table.c.industry, table.c.region)
            .where(table.c.id > last_id)
            .order_by(table.c.id)
            .limit(batch_size)
        ).fetchall()
        if not rows:
            break

        updates = []
        for row in rows:
            province, city = _split_region(row.region)
            updates.append(
                {
                    "_company_id": row.id,
                    "_canonical_name": _canonical_name(row.name),
                    "_normalized_domain": _normalize_domain(row.website),
                    "_industry_primary": _primary_industry(row.industry),
                    "_industry_tags_json": json.dumps(_industry_tags(row.industry), ensure_ascii=False),
                    "_province": province,
                    "_city": city,
                }
            )

        bind.execute(update_stmt, updates)
        last_id = rows[-1].id
        total += len(updates)
    if total:
        print(f"[e4f9c2a71b83] 已回填 {total} 家企业的规范化字段")


def _company_index_columns() -> None:
    """Create filter indexes first: they can be built whether or not duplicates exist, directly replacing the ilike full table scan."""
    existing = _indexes(COMPANIES)
    if "idx_sales_agent_companies_filter" not in existing:
        op.create_index("idx_sales_agent_companies_filter", COMPANIES, ["industry_primary", "province", "data_status"])
    if "idx_sales_agent_companies_domain" not in existing:
        op.create_index("idx_sales_agent_companies_domain", COMPANIES, ["normalized_domain"])
    if "idx_sales_agent_companies_verified" not in existing:
        op.create_index("idx_sales_agent_companies_verified", COMPANIES, ["last_verified_at"])


def _duplicate_domains() -> list[tuple[str, int]]:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT normalized_domain, COUNT(*) AS n FROM {COMPANIES} "
            "WHERE normalized_domain <> '' GROUP BY normalized_domain HAVING COUNT(*) > 1 ORDER BY n DESC"
        )
    ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def _duplicate_name_province() -> list[tuple[str, str, int]]:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT canonical_name, province, COUNT(*) AS n FROM {COMPANIES} "
            "WHERE normalized_domain = '' AND canonical_name <> '' "
            "GROUP BY canonical_name, province HAVING COUNT(*) > 1 ORDER BY n DESC"
        )
    ).fetchall()
    return [(r[0], r[1], int(r[2])) for r in rows]


def _unique_indexes() -> None:
    """Create unique indexes only when existing data is clean; if there are duplicates, skip and print the list for manual dedup.

    Automatic merging would merge A's contact info into B, and getting it wrong is worse than keeping duplicates: the design doc's
    "if unsure whether it is a duplicate, mark needs_review, do not auto-merge" refers exactly to this.
    """
    existing = _indexes(COMPANIES)
    dup_domains = _duplicate_domains()
    dup_names = _duplicate_name_province()

    if dup_domains or dup_names:
        print(f"[e4f9c2a71b83] 跳过唯一索引:发现 {len(dup_domains)} 组重复域名、{len(dup_names)} 组重复(企业名+省份)")
        for domain, n in dup_domains[:20]:
            print(f"  重复域名 {domain} × {n}")
        for name, province, n in dup_names[:20]:
            print(f"  重复企业 {name} / {province or '(无省份)'} × {n}")
        print("  人工判重后把重复行置 data_status='duplicate' + merged_into_id,")
        print("  再跑 python backend/scripts/company_unique_indexes.py --apply 补建唯一索引")
        return

    where = "normalized_domain <> '' AND data_status <> 'duplicate'"
    if "ux_sales_agent_companies_domain" not in existing:
        op.create_index(
            "ux_sales_agent_companies_domain", COMPANIES, ["normalized_domain"], unique=True,
            postgresql_where=sa.text(where), sqlite_where=sa.text(where),
        )
    where_name = "normalized_domain = '' AND canonical_name <> '' AND data_status <> 'duplicate'"
    if "ux_sales_agent_companies_name_province" not in existing:
        op.create_index(
            "ux_sales_agent_companies_name_province", COMPANIES, ["canonical_name", "province"], unique=True,
            postgresql_where=sa.text(where_name), sqlite_where=sa.text(where_name),
        )
    print("[e4f9c2a71b83] 存量无重复,已建立企业去重唯一索引")


def _contact_unique_index() -> None:
    if "ux_sales_agent_contacts_company_email" in _indexes(CONTACTS):
        return
    bind = op.get_bind()
    dup = bind.execute(
        sa.text(
            f"SELECT company_id, email, COUNT(*) FROM {CONTACTS} "
            "WHERE email <> '' GROUP BY company_id, email HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dup:
        print(f"[e4f9c2a71b83] 跳过联系方式唯一索引:{len(dup)} 组 (company_id, email) 重复")
        print("  判重后跑 python backend/scripts/company_unique_indexes.py --apply 补建")
        return
    where = "email <> ''"
    op.create_index(
        "ux_sales_agent_contacts_company_email", CONTACTS, ["company_id", "email"], unique=True,
        postgresql_where=sa.text(where), sqlite_where=sa.text(where),
    )


def upgrade() -> None:
    # This migration "extends columns", it does not "create tables": companies / contacts are created by f6a2d4c8b931.
    # If someone DROPped these two tables to clear data, the dozen add_column calls below would fail one by one,
    # obscuring the root cause. Say it clearly once here: to clear data use TRUNCATE, not DROP.
    missing = [t for t in (COMPANIES, CONTACTS) if not _has_table(t)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} 不存在。本迁移只加列,建表在 f6a2d4c8b931。\n"
            "若为清空数据而删表:请先恢复表结构(alembic downgrade f6a2d4c8b931 后重新 upgrade),\n"
            "清数据请改用 TRUNCATE —— DROP 会连带删掉 lead_recommendations 等表指向本表的外键。"
        )
    _create_sources()
    _create_batches()
    _extend_companies()
    _extend_contacts()
    _create_signals()
    _add_foreign_keys()
    _backfill_companies()
    _company_index_columns()
    _unique_indexes()
    _contact_unique_index()


def downgrade() -> None:
    _drop_index("ux_sales_agent_contacts_company_email", CONTACTS)
    for name in (
        "ux_sales_agent_companies_name_province",
        "ux_sales_agent_companies_domain",
        "idx_sales_agent_companies_verified",
        "idx_sales_agent_companies_domain",
        "idx_sales_agent_companies_filter",
    ):
        _drop_index(name, COMPANIES)

    if _is_postgres():
        for name, table in (
            ("fk_sales_agent_company_contacts_source", CONTACTS),
            ("fk_sales_agent_companies_merged_into", COMPANIES),
            ("fk_sales_agent_companies_batch", COMPANIES),
            ("fk_sales_agent_companies_source", COMPANIES),
        ):
            if name in {fk.get("name") for fk in _inspector().get_foreign_keys(table)}:
                op.drop_constraint(name, table, type_="foreignkey")

    if _has_table(SIGNALS):
        op.drop_table(SIGNALS)

    for name in ("bounce_count", "last_verified_at", "source_id"):
        _drop_column(CONTACTS, name)
    for name in (
        "last_verified_at", "confidence", "merged_into_id", "data_status", "import_batch_id",
        "source_id", "source_url", "business_summary", "city", "province",
        "industry_tags_json", "industry_primary", "normalized_domain", "canonical_name",
    ):
        _drop_column(COMPANIES, name)

    if _has_table(BATCHES):
        op.drop_table(BATCHES)
    if _has_table(SOURCES):
        op.drop_table(SOURCES)
