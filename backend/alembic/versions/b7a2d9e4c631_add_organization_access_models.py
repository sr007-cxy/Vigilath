"""add organization access models

Revision ID: b7a2d9e4c631
Revises: f2b1c9d8a046
Create Date: 2026-08-30 07:10:00.000000

P1 generic organization authorization infrastructure:
- organizations, members, enterprise account authorizations and member assignments;
- immutable operation context and append-only system audit;
- operation_context_id for phase-1 async runs/tasks;
- agency role value domain and PostgreSQL/SQLite partial unique indexes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7a2d9e4c631"
down_revision: Union[str, None] = "f2b1c9d8a046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONTEXT_TABLES = (
    "ai_telemetry_runs",
    "ai_telemetry_topic_briefings",
    "ai_telemetry_topic_source_strategy",
    "ai_telemetry_topic_solutions",
    "topic_content_gen_batches",
    "topic_doc_publish_tasks",
    "sentiment_run_logs",
    "publish_dispatch_tasks",
    "browser_jobs",
)


def _add_operation_context_references() -> None:
    for table_name in _CONTEXT_TABLES:
        fk_name = f"fk_{table_name}_operation_context"
        index_name = f"ix_{table_name}_operation_context_id"
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("operation_context_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                fk_name,
                "operation_contexts",
                ["operation_context_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(index_name, ["operation_context_id"])


def _drop_operation_context_references() -> None:
    for table_name in reversed(_CONTEXT_TABLES):
        fk_name = f"fk_{table_name}_operation_context"
        index_name = f"ix_{table_name}_operation_context_id"
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_index(index_name)
            batch_op.drop_constraint(fk_name, type_="foreignkey")
            batch_op.drop_column("operation_context_id")


def _create_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in ("operation_contexts", "system_audit_logs"):
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()
                """
            )
    elif dialect == "sqlite":
        for table_name in ("operation_contexts", "system_audit_logs"):
            for action in ("UPDATE", "DELETE"):
                suffix = action.lower()
                op.execute(
                    f"""
                    CREATE TRIGGER trg_{table_name}_append_only_{suffix}
                    BEFORE {action} ON {table_name}
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is append-only');
                    END
                    """
                )


def _drop_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in ("system_audit_logs", "operation_contexts"):
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
            )
        op.execute("DROP FUNCTION IF EXISTS reject_append_only_mutation()")
    elif dialect == "sqlite":
        for table_name in ("system_audit_logs", "operation_contexts"):
            for suffix in ("update", "delete"):
                op.execute(
                    f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{suffix}"
                )


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_users_role_allowed",
            "role IN ('admin', 'operator', 'user', 'tenant', 'maintainer', "
            "'agency_admin', 'agency_operator')",
        )

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("contact_name", sa.String(length=128), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("disabled_by_user_id", sa.Integer(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_organizations_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_type", name="uq_organizations_id_type"
        ),
    )
    op.create_index(
        "ix_organizations_type_status",
        "organizations",
        ["organization_type", "status"],
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("organization_type", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("member_role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("joined_by_user_id", sa.Integer(), nullable=False),
        sa.Column("removed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("removal_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "member_role IN ('admin', 'operator')",
            name="ck_organization_members_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_organization_members_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "organization_type"],
            ["organizations.id", "organizations.organization_type"],
            name="fk_organization_members_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["joined_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["removed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "organization_type",
            name="uq_organization_members_id_scope",
        ),
    )
    op.create_index(
        "uq_active_agency_member",
        "organization_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND organization_type = 'agency'"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND organization_type = 'agency'"
        ),
    )
    op.create_index(
        "ix_organization_members_org_status",
        "organization_members",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_organization_members_user_status",
        "organization_members",
        ["user_id", "status"],
    )

    op.create_table(
        "organization_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("organization_type", sa.String(length=32), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_organization_grants_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "organization_type"],
            ["organizations.id", "organizations.organization_type"],
            name="fk_organization_grants_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "organization_type",
            name="uq_organization_grants_id_scope",
        ),
    )
    op.create_index(
        "uq_active_agency_enterprise_grant",
        "organization_grants",
        ["target_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND organization_type = 'agency'"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND organization_type = 'agency'"
        ),
    )
    op.create_index(
        "ix_organization_grants_org_status",
        "organization_grants",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_organization_grants_target_status",
        "organization_grants",
        ["target_user_id", "status"],
    )

    op.create_table(
        "organization_member_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("organization_type", sa.String(length=32), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("assigned_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_organization_member_assignments_status",
        ),
        sa.ForeignKeyConstraint(
            ["member_id", "organization_id", "organization_type"],
            [
                "organization_members.id",
                "organization_members.organization_id",
                "organization_members.organization_type",
            ],
            name="fk_assignments_member_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "organization_id", "organization_type"],
            [
                "organization_grants.id",
                "organization_grants.organization_id",
                "organization_grants.organization_type",
            ],
            name="fk_assignments_grant_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_active_organization_member_assignment",
        "organization_member_assignments",
        ["member_id", "grant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_organization_assignments_org_status",
        "organization_member_assignments",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_organization_assignments_grant_status",
        "organization_member_assignments",
        ["grant_id", "status"],
    )

    op.create_table(
        "operation_contexts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("enterprise_user_id", sa.Integer(), nullable=True),
        sa.Column("authorization_mode", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_resource_type", sa.String(length=64), nullable=False),
        sa.Column("target_resource_id", sa.String(length=64), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("organization_member_id", sa.Integer(), nullable=True),
        sa.Column("organization_grant_id", sa.Integer(), nullable=True),
        sa.Column("organization_member_assignment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "authorization_mode IN ('self', 'platform', 'organization', 'service')",
            name="ck_operation_contexts_authorization_mode",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["enterprise_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_member_id"], ["organization_members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_grant_id"], ["organization_grants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_member_assignment_id"],
            ["organization_member_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operation_contexts_trace", "operation_contexts", ["trace_id"])
    op.create_index(
        "ix_operation_contexts_actor_created",
        "operation_contexts", ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_operation_contexts_enterprise_created",
        "operation_contexts", ["enterprise_user_id", "created_at"],
    )
    op.create_index(
        "ix_operation_contexts_target",
        "operation_contexts", ["target_resource_type", "target_resource_id"],
    )

    op.create_table(
        "system_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_context_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("organization_member_id", sa.Integer(), nullable=True),
        sa.Column("organization_grant_id", sa.Integer(), nullable=True),
        sa.Column("organization_member_assignment_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("enterprise_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_snapshot_json", sa.Text(), nullable=True),
        sa.Column("after_snapshot_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "result IN ('success', 'denied', 'failed')",
            name="ck_system_audit_logs_result",
        ),
        sa.ForeignKeyConstraint(
            ["operation_context_id"], ["operation_contexts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_member_id"], ["organization_members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_grant_id"], ["organization_grants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_member_assignment_id"],
            ["organization_member_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["enterprise_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_audit_logs_trace_created",
        "system_audit_logs", ["trace_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_actor_created",
        "system_audit_logs", ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_enterprise_created",
        "system_audit_logs", ["enterprise_user_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_object_created",
        "system_audit_logs", ["object_type", "object_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_organization_created",
        "system_audit_logs", ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_member_created",
        "system_audit_logs", ["organization_member_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_grant_created",
        "system_audit_logs", ["organization_grant_id", "created_at"],
    )
    op.create_index(
        "ix_system_audit_logs_assignment_created",
        "system_audit_logs", ["organization_member_assignment_id", "created_at"],
    )

    _add_operation_context_references()
    _create_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    _drop_operation_context_references()

    op.drop_index("ix_system_audit_logs_assignment_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_grant_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_member_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_organization_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_object_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_enterprise_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_actor_created", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_trace_created", table_name="system_audit_logs")
    op.drop_table("system_audit_logs")

    op.drop_index("ix_operation_contexts_target", table_name="operation_contexts")
    op.drop_index("ix_operation_contexts_enterprise_created", table_name="operation_contexts")
    op.drop_index("ix_operation_contexts_actor_created", table_name="operation_contexts")
    op.drop_index("ix_operation_contexts_trace", table_name="operation_contexts")
    op.drop_table("operation_contexts")

    op.drop_index(
        "ix_organization_assignments_grant_status",
        table_name="organization_member_assignments",
    )
    op.drop_index(
        "ix_organization_assignments_org_status",
        table_name="organization_member_assignments",
    )
    op.drop_index(
        "uq_active_organization_member_assignment",
        table_name="organization_member_assignments",
    )
    op.drop_table("organization_member_assignments")

    op.drop_index("ix_organization_grants_target_status", table_name="organization_grants")
    op.drop_index("ix_organization_grants_org_status", table_name="organization_grants")
    op.drop_index("uq_active_agency_enterprise_grant", table_name="organization_grants")
    op.drop_table("organization_grants")

    op.drop_index("ix_organization_members_user_status", table_name="organization_members")
    op.drop_index("ix_organization_members_org_status", table_name="organization_members")
    op.drop_index("uq_active_agency_member", table_name="organization_members")
    op.drop_table("organization_members")

    op.drop_index("ix_organizations_type_status", table_name="organizations")
    op.drop_table("organizations")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("ck_users_role_allowed", type_="check")
