"""WeChat QR-code login + phone number binding + SMS verification codes

Adds WeChat identity / phone number columns to users; adds three tables for QR login sessions, verification codes and official account token cache.

Revision ID: d4b9e2a7c318
Revises: a7d2e4f91c83
Create Date: 2026-08-25

Deployment note: the repo has multiple heads, and `alembic upgrade head` reports multiple heads;
upgrade to this target explicitly: `alembic upgrade d4b9e2a7c318`.
"""
from alembic import op
import sqlalchemy as sa


revision = "d4b9e2a7c318"
down_revision = "a7d2e4f91c83"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    # ── Add columns to users (idempotent: test/prod share appdb, another branch may have added them first) ──
    cols = [
        ("wechat_openid", sa.Column("wechat_openid", sa.String(length=64), nullable=True)),
        ("wechat_unionid", sa.Column("wechat_unionid", sa.String(length=64), nullable=True)),
        ("wechat_nickname", sa.Column("wechat_nickname", sa.String(length=128), nullable=True)),
        ("wechat_avatar", sa.Column("wechat_avatar", sa.String(length=512), nullable=True)),
        ("wechat_subscribed", sa.Column("wechat_subscribed", sa.Boolean(),
                                        nullable=False, server_default=sa.false())),
        ("phone", sa.Column("phone", sa.String(length=32), nullable=True)),
        ("phone_verified_at", sa.Column("phone_verified_at", sa.DateTime(), nullable=True)),
    ]
    for name, col in cols:
        if not _has_column("users", name):
            op.add_column("users", col)

    existing_idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("users")}
    if "ix_users_wechat_openid" not in existing_idx:
        op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=True)
    if "ix_users_wechat_unionid" not in existing_idx:
        op.create_index("ix_users_wechat_unionid", "users", ["wechat_unionid"])
    if "ix_users_phone" not in existing_idx:
        op.create_index("ix_users_phone", "users", ["phone"], unique=True)

    if not _has_table("wechat_login_sessions"):
        op.create_table(
            "wechat_login_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scene", sa.String(length=64), nullable=False),
            sa.Column("ticket", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("openid", sa.String(length=64), nullable=True),
            sa.Column("unionid", sa.String(length=64), nullable=True),
            sa.Column("subscribed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("bind_token", sa.Text(), nullable=True),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("scanned_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_wechat_login_sessions_scene", "wechat_login_sessions",
                        ["scene"], unique=True)
        op.create_index("ix_wechat_login_sessions_status", "wechat_login_sessions", ["status"])
        op.create_index("ix_wechat_login_sessions_openid", "wechat_login_sessions", ["openid"])
        op.create_index("ix_wechat_login_sessions_user_id", "wechat_login_sessions", ["user_id"])
        op.create_index("ix_wechat_login_sessions_created_at", "wechat_login_sessions",
                        ["created_at"])
        op.create_index("ix_wechat_login_sessions_expires_at", "wechat_login_sessions",
                        ["expires_at"])

    if not _has_table("sms_codes"):
        op.create_table(
            "sms_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("phone", sa.String(length=32), nullable=False),
            sa.Column("code", sa.String(length=8), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False, server_default="bind"),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("send_ip", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_sms_codes_phone", "sms_codes", ["phone"])
        op.create_index("ix_sms_codes_purpose", "sms_codes", ["purpose"])
        op.create_index("ix_sms_codes_created_at", "sms_codes", ["created_at"])

    if not _has_table("wechat_mp_tokens"):
        op.create_table(
            "wechat_mp_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("app_id", sa.String(length=64), nullable=False),
            sa.Column("access_token", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_wechat_mp_tokens_app_id", "wechat_mp_tokens", ["app_id"], unique=True)


def downgrade() -> None:
    for t in ("wechat_mp_tokens", "sms_codes", "wechat_login_sessions"):
        if _has_table(t):
            op.drop_table(t)
    for idx in ("ix_users_phone", "ix_users_wechat_unionid", "ix_users_wechat_openid"):
        try:
            op.drop_index(idx, table_name="users")
        except Exception:  # noqa: BLE001 - the index was never created in the first place
            pass
    for name in ("phone_verified_at", "phone", "wechat_subscribed", "wechat_avatar",
                 "wechat_nickname", "wechat_unionid", "wechat_openid"):
        if _has_column("users", name):
            op.drop_column("users", name)
