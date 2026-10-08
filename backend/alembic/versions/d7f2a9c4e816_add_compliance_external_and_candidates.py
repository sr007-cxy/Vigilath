"""Compliance audit center P2: external moderation soft-signal column + lexicon term candidate pool columns.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md 5.7 (external banned-word service, internal first then external);
integration plan: docs/06-发布运维/合规与资质/合规中心/合规审计中心-外部词库接入方案-阿里云TextModeration.md.

  - compliance_scan_results.external_json   Aliyun external moderation result snapshot (soft signal, not used in decisions)
  - compliance_lexicon_terms.status         active/pending/rejected (candidate pool)
  - compliance_lexicon_terms.source         seed/manual/external/platform_rules
  - compliance_lexicon_terms.origin_note    recommendation source context
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7f2a9c4e816'
down_revision: Union[str, None] = 'c8f4a2d7e913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "compliance_scan_results",
        sa.Column("external_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "compliance_lexicon_terms",
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
    )
    op.add_column(
        "compliance_lexicon_terms",
        sa.Column("source", sa.String(length=24), nullable=False, server_default="seed"),
    )
    op.add_column(
        "compliance_lexicon_terms",
        sa.Column("origin_note", sa.String(length=256), nullable=False, server_default=""),
    )
    op.create_index("idx_comp_term_status", "compliance_lexicon_terms", ["status"])


def downgrade() -> None:
    op.drop_index("idx_comp_term_status", table_name="compliance_lexicon_terms")
    op.drop_column("compliance_lexicon_terms", "origin_note")
    op.drop_column("compliance_lexicon_terms", "source")
    op.drop_column("compliance_lexicon_terms", "status")
    op.drop_column("compliance_scan_results", "external_json")
