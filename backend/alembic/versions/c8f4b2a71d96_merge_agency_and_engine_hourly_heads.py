"""merge agency access and engine hourly usage heads

Revision ID: c8f4b2a71d96
Revises: c1f8b3d24e97, b7a2d9e4c631
Create Date: 2026-08-30 00:00:00.000000

Pure merge revision, no DDL. It converges develop's engine hourly usage migration with the feature branch's
engine rate-limit policy migration and organization permission migration into a single head, so the deploy-side
`alembic upgrade head` has a unique target.
"""

revision = "c8f4b2a71d96"
down_revision = ("c1f8b3d24e97", "b7a2d9e4c631")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
