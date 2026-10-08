"""Compliance audit center P0 foundation: four tables + topic compliance profile column.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md (v2 "one gate, three stages").

  - compliance_lexicons / compliance_lexicon_terms  banned-word lexicons (hard-block data source)
  - compliance_scan_results                          scan result snapshots (lexicon version traceable)
  - compliance_audit_logs                            append-only audit log (shared by all gates)
  - ai_telemetry_topics.compliance_profile_json      topic-level compliance profile
    (ymyl_tier / qualification list qualifications / disclaimer templates disclaimers / placement restrictions restrictions)

Lexicon term data is not loaded in the migration: run backend/scripts/seed_compliance_lexicons.py to import the two generic lexicons.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b6e2d9a4c1f7'
down_revision: Union[str, None] = 'c5e7a2f9d316'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compliance_lexicons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=24), nullable=False, server_default="generic"),
        sa.Column("rules_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=16), nullable=False, server_default="2026.07"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_comp_lex_code", "compliance_lexicons", ["code"], unique=True)

    op.create_table(
        "compliance_lexicon_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lexicon_id", sa.Integer(),
                  sa.ForeignKey("compliance_lexicons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("fix", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("idx_comp_term_lexicon", "compliance_lexicon_terms", ["lexicon_id"])

    op.create_table(
        "compliance_scan_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_id", sa.Integer(),
                  sa.ForeignKey("topic_generated_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("hook", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("lexicon_versions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("hits_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ban_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_disclaimers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("verdict", sa.String(length=24), nullable=False, server_default="pass"),
        sa.Column("scanned_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_comp_scan_doc", "compliance_scan_results", ["doc_id"])
    op.create_index("idx_comp_scan_topic", "compliance_scan_results", ["topic_id"])

    op.create_table(
        "compliance_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_comp_audit_topic", "compliance_audit_logs", ["topic_id"])
    op.create_index("idx_comp_audit_created", "compliance_audit_logs", ["created_at"])

    op.add_column("ai_telemetry_topics",
                  sa.Column("compliance_profile_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("ai_telemetry_topics", "compliance_profile_json")
    op.drop_index("idx_comp_audit_created", table_name="compliance_audit_logs")
    op.drop_index("idx_comp_audit_topic", table_name="compliance_audit_logs")
    op.drop_table("compliance_audit_logs")
    op.drop_index("idx_comp_scan_topic", table_name="compliance_scan_results")
    op.drop_index("idx_comp_scan_doc", table_name="compliance_scan_results")
    op.drop_table("compliance_scan_results")
    op.drop_index("idx_comp_term_lexicon", table_name="compliance_lexicon_terms")
    op.drop_table("compliance_lexicon_terms")
    op.drop_index("idx_comp_lex_code", table_name="compliance_lexicons")
    op.drop_table("compliance_lexicons")
