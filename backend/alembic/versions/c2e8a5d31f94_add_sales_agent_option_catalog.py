"""Persist the profile/filter card option catalog to DB

The 7 professional capabilities used to be literals in `service/guidance.py` (copied twice), and industries/regions/sizes
were tuples in `constants.py` / `common.py`. Custom items users wrote in a card only landed in that profile row's
`service_capabilities_json` and never became selectable options next time.

This table unifies the five option kinds: rows with both `user_id` and `seller_profile_id` NULL are system presets
(shared by all accounts, seeded by this migration); rows with both set are written by users and private per brand.

The code-side tuples are not deleted: they are both this migration's seed source and the fallback when the DB query fails/returns empty,
so environments without the migration behave the same.

Revision ID: c2e8a5d31f94
Revises: a1c6f4b8e207
"""
from alembic import op
import sqlalchemy as sa

revision = "c2e8a5d31f94"
down_revision = "a1c6f4b8e207"
branch_labels = None
depends_on = None

TABLE = "sales_agent_option_catalog"

# The seed shares its source with backend/agents/sales_agent/constants.py, service/common.py and service/guidance.py.
# 「自定义能力」「自定义行业」 ("custom capability", "custom industry") are not persisted: they are "let me write my own" action entries, not options.
_CAPABILITIES = (
    "AI-GEO优化",
    "SEO技术团队",
    "官网建设/优化",
    "内容营销能力",
    "行业解决方案经验",
    "数据分析能力",
    "销售/交付团队",
)

_TYPICAL_CUSTOMERS = (
    ("有官网但搜索获客一般的企业", "已有官网,但自然搜索、询盘或咨询转化不理想。"),
    ("想提升 AI 曝光的品牌方", "希望在 ChatGPT、Perplexity、AI Overviews 等场景被更多推荐。"),
    ("有内容团队但转化不足的公司", "持续做内容,但排名、留资或成交线索没有明显增长。"),
    ("正在做数字化获客的中小企业", "愿意投入官网、SEO、GEO 或 AI 搜索获客体系升级。"),
)

_INDUSTRIES = (
    "工业制造", "企业服务", "软件SaaS", "电商零售", "教育培训", "医疗健康", "口腔医疗",
    "美容医美", "餐饮酒店", "本地生活", "房地产家居", "金融保险", "法律财税", "汽车服务",
    "文旅旅游", "不限行业",
)

_REGIONS = (
    "全国", "一线城市", "北京", "上海", "深圳", "广州", "杭州", "苏州", "南京", "成都",
    "武汉", "西安", "重庆", "长三角", "珠三角", "京津冀", "华东", "华南",
)

_COMPANY_SIZES = (
    ("1-10 名员工", 1, 10),
    ("11-100 名员工", 11, 100),
    ("101-500 名员工", 101, 500),
    ("501-1000 名员工", 501, 1000),
    ("1001+ 名员工", 1001, None),
)


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _seed_rows() -> list[dict]:
    rows: list[dict] = []
    for idx, value in enumerate(_CAPABILITIES):
        rows.append({"kind": "capability", "value": value, "label": value, "description": "", "sort_order": (idx + 1) * 10})
    for idx, (value, desc) in enumerate(_TYPICAL_CUSTOMERS):
        rows.append({"kind": "typical_customer", "value": value, "label": value, "description": desc, "sort_order": (idx + 1) * 10})
    for idx, value in enumerate(_INDUSTRIES):
        rows.append({"kind": "industry", "value": value, "label": value, "description": "", "sort_order": (idx + 1) * 10})
    for idx, value in enumerate(_REGIONS):
        rows.append({"kind": "region", "value": value, "label": value, "description": "", "sort_order": (idx + 1) * 10})
    for idx, (value, low, high) in enumerate(_COMPANY_SIZES):
        rows.append({
            "kind": "company_size", "value": value, "label": value, "description": "",
            "sort_order": (idx + 1) * 10, "min_employees": low, "max_employees": high,
        })
    for row in rows:
        row.setdefault("min_employees", None)
        row.setdefault("max_employees", None)
        row["user_id"] = None
        row["seller_profile_id"] = None
        row["source"] = "system"
        row["archived_at"] = None
    return rows


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("seller_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_seller_profiles.id"), nullable=True, index=True),
        sa.Column("value", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("min_employees", sa.Integer(), nullable=True),
        sa.Column("max_employees", sa.Integer(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_sales_agent_option_catalog_lookup", TABLE, ["kind", "user_id", "seller_profile_id", "sort_order"])
    # Note: SQL UNIQUE treats NULLs as not equal to each other, so this constraint **cannot govern system preset rows**
    # (user_id / seller_profile_id are both NULL). System rows rely on this migration seeding only once +
    # service/catalog.py's check-then-insert to avoid duplicates; what this constraint really prevents is
    # the same brand inserting the same custom item twice.
    op.create_index("ux_sales_agent_option_catalog_value", TABLE, ["kind", "user_id", "seller_profile_id", "value"], unique=True)

    rows = _seed_rows()
    if rows:
        op.bulk_insert(sa.table(
            TABLE,
            sa.column("kind", sa.String),
            sa.column("user_id", sa.Integer),
            sa.column("seller_profile_id", sa.Integer),
            sa.column("value", sa.String),
            sa.column("label", sa.String),
            sa.column("description", sa.String),
            sa.column("source", sa.String),
            sa.column("sort_order", sa.Integer),
            sa.column("min_employees", sa.Integer),
            sa.column("max_employees", sa.Integer),
            sa.column("archived_at", sa.DateTime),
        ), rows)


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
