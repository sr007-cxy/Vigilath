"""Merge two heads: full_diagnosis_scope and session_health (the end of the skillpay branch)

Pure merge, no table changes. Why it was added: the deploy script (deploy/deploy-systemd.sh) runs
`alembic upgrade head`, and with two heads in the repo the command is ambiguous and fails outright,
so every deploy with migrations had to manually upgrade to a target. After merging, upgrade head is deterministic again.

Revision ID: b9f4c1a7d602
Revises: f3a1c85d2b74, d5e9b3a71c24
"""

revision = "b9f4c1a7d602"
down_revision = ("f3a1c85d2b74", "d5e9b3a71c24")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
