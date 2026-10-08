"""Structure and indexes of the four new scheduled-send tables (计划-2026-09-11-定时发送邮件.md §3 / §3.5).

This file guards two things, both of the "no test today, quietly changed tomorrow" kind:

1. **Not one required index may be missing.** The executor scans the schedule table every 60 seconds, and the item table will grow to
   millions of rows within a year -- missing those two indexes is what would really slow the DB down.
2. **Not one unwanted index may be added.** The heartbeat row is updated every 60 seconds (520K times a year);
   as long as the updated columns have no index, PG uses HOT updates and dead tuples are reclaimed in-page; indexing
   `last_beat_at` would instead bloat the index on every heartbeat.

Plus a harder one: **this migration must not touch any existing table**.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_send_schedule_tables.py -q
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from agents.sales_agent.models import (
    SalesAgentEmailSuppressionORM,
    SalesAgentSchedulerHeartbeatORM,
    SalesAgentSendScheduleItemORM,
    SalesAgentSendScheduleORM,
)

MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "d1a6f3c92e70_add_sales_agent_send_schedules.py"

NEW_TABLES = {
    "sales_agent_send_schedules",
    "sales_agent_send_schedule_items",
    "sales_agent_email_suppressions",
    "sales_agent_scheduler_heartbeats",
}


def _index_columns(orm) -> set[tuple[str, ...]]:
    """Indexes declared on the ORM (including single-column `index=True`), returned as column groups."""
    out = {tuple(c.name for c in idx.columns) for idx in orm.__table__.indexes}
    out |= {(c.name,) for c in orm.__table__.columns if c.index}
    return out


def test_schedule_table_has_the_due_scan_index():
    """The executor runs `status='armed' AND scheduled_at <= ...` every 60 seconds; without an index it's a full table scan every minute."""
    assert ("status", "scheduled_at") in _index_columns(SalesAgentSendScheduleORM)


def test_schedule_table_has_user_index():
    """The list page GET /outreach/schedules queries by user."""
    assert ("user_id",) in _index_columns(SalesAgentSendScheduleORM)


def test_item_table_has_the_schedule_status_index():
    """The item table is the only new table in this design that grows to millions of rows; every read/write carries schedule_id."""
    assert ("schedule_id", "status") in _index_columns(SalesAgentSendScheduleItemORM)


def test_suppression_is_unique_per_user_and_email():
    """One user + one address may have only one row, or the "hits the suppression list" check would depend on insertion order."""
    uniques = {
        tuple(c.name for c in con.columns)
        for con in SalesAgentEmailSuppressionORM.__table__.constraints
        if type(con).__name__ == "UniqueConstraint"
    }
    assert ("user_id", "email") in uniques


def test_heartbeat_key_is_unique():
    hb = SalesAgentSchedulerHeartbeatORM.__table__
    unique_idx = {tuple(c.name for c in i.columns) for i in hb.indexes if i.unique}
    assert ("instance_key",) in unique_idx


def test_heartbeat_has_no_index_on_updated_columns():
    """The heartbeat updates the same row every 60 seconds. Once an updated column has an index, HOT updates are impossible,
    and dead tuples enter the index 520K times a year -- this table should have only the instance_key index."""
    hb = SalesAgentSchedulerHeartbeatORM.__table__
    indexed: set[str] = {c.name for i in hb.indexes for c in i.columns}
    indexed |= {c.name for c in hb.columns if c.index}
    hot_columns = {"last_beat_at", "live_workers", "last_error"}
    assert not (indexed & hot_columns), f"心跳表被更新的列上有索引:{sorted(indexed & hot_columns)}"


# ------------------------------------------------------- the migration itself


def _migration_calls() -> list[ast.Call]:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _op_calls(name: str) -> list[ast.Call]:
    out = []
    for call in _migration_calls():
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == name and isinstance(func.value, ast.Name) and func.value.id == "op":
            out.append(call)
    return out


def _first_str_arg(call: ast.Call, position: int = 0) -> str:
    arg = call.args[position]
    assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), ast.dump(arg)
    return arg.value


def test_migration_creates_exactly_the_four_new_tables():
    created = {_first_str_arg(c) for c in _op_calls("create_table")}
    assert created == NEW_TABLES


@pytest.mark.parametrize("op_name", ["add_column", "alter_column", "drop_column", "rename_table", "execute"])
def test_migration_never_touches_existing_tables(op_name):
    """Phase one touches no existing table -- not even adding an index. The last production slowdown came from directly UPDATEing a big table."""
    assert _op_calls(op_name) == [], f"迁移里出现了 op.{op_name}(),但一期不许改现有表"


def test_migration_only_indexes_the_new_tables():
    for call in _op_calls("create_index"):
        table = _first_str_arg(call, 1)
        assert table in NEW_TABLES, f"迁移给现有表 {table} 加了索引"


def test_migration_does_not_index_heartbeat_beat_column():
    """Same as the ORM one: the migration must not index the heartbeat table's hot-updated columns either."""
    for call in _op_calls("create_index"):
        if _first_str_arg(call, 1) != "sales_agent_scheduler_heartbeats":
            continue
        columns = call.args[2]
        assert isinstance(columns, ast.List)
        names = [c.value for c in columns.elts if isinstance(c, ast.Constant)]
        assert "last_beat_at" not in names, "心跳表的 last_beat_at 被建了索引,HOT update 就没了"


def test_migration_downgrade_drops_all_four():
    dropped = {_first_str_arg(c) for c in _op_calls("drop_table")}
    assert dropped == NEW_TABLES
