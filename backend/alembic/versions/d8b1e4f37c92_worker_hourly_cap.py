"""Add worker_hourly_cap to engine_rate_policies: move per-machine rate limiting to the server and split it by engine

Per-device rate limiting for physical devices has always been on the **client** (hourly_task_limit / daily_task_limit in V-networks/workers/desktop-cn).
Three problems:

  1. Every change must be pushed to the whole fleet, while a server-side configuration change can take effect immediately.
  2. **Not split by engine**. The worker has a single state["task_times"] counter, and once three engines run on physical devices
     they eat each other's quota: Doubao fills up 120 jobs, and Qwen and Wenxin cannot run a single one that day.
  3. **Failures also consume quota** (worker.py counts when a task starts, regardless of outcome, i.e. P0-4), which can exhaust a device's daily limit despite low successful output.

The server-side layer avoids all three: it counts per (machine x engine), a one-row config change takes effect, and the count uses
claimed_at (same source as the egress gate). The client counter is **kept** as a fallback: machines should not run wild if the center is down;
but it is no longer the link that constrains capacity, and can be raised via center-delivered config.

engine_rate_policies already has worker_daily_cap (per-engine per-machine daily limit), but it was previously only used to
**compute capacity** (_worker_fleet_capacity) and was not enforced at claim. This migration adds the hourly half,
and together the two fields form the server-side per-machine gate.

**But both columns are left empty (NULL = unlimited).** On the anonymous channel the main gate is the egress gate, not the per-machine gate; see the long comment
where the _DEFAULTS block below was removed. This gate is a means to "throttle a single machine when needed",
not a routine constraint.

⚠️ down_revision follows d5a3e8c71f42. c4f7a2e91b58 on the develop branch also follows it: after merging both
   it will be a normal fork point that `alembic upgrade heads` (plural) can handle. This repo normally has multiple heads,
   and the deploy script uses heads.

Revision ID: d8b1e4f37c92
Revises: d5a3e8c71f42
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b1e4f37c92"
down_revision: Union[str, None] = "d5a3e8c71f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# **Default values are deliberately not seeded.** On the anonymous channel what risk control really looks at is the egress: the conclusion reproduced three times
# in doubao_collector is "what's limited is not the cookie, it's the egress IP/device", and the egress gate (_egress_blocked_engines,
# 120/hour/egress) already handles that.
#
# Adding another per-machine gate by device count is **backwards**: with four machines sharing an egress, 20x4=80/hour is stricter than the egress gate's 120,
# wasting 40/hour of egress budget for nothing, and machines cannot borrow from each other when load is uneven. This is the same reasoning as the fleet capacity rule
# "count by egress, not by device count" (comment on _worker_fleet_capacity), and the new gate should not
# repeat that mistake by counting devices.
#
# So this column is **created but left empty** (NULL = unlimited):
#   · normally the egress gate governs everything, with no per-machine limit;
#   · when one machine misbehaves and needs throttling, add a row for that engine: this is exactly the point of moving it to the server,
#     one value change takes effect without pushing to the whole fleet.
#
# Note: the client counter (hourly_task_limit=20 / daily_task_limit=120 in V-networks/workers/desktop-cn)
# **is still running and still a hard constraint**. To actually use the full egress budget it must be raised via center-delivered config;
# this server-side gate only makes "raising the client" safe (it can be throttled back any time when needed).

def _has_col(bind, col: str) -> bool:
    return col in {c["name"] for c in sa.inspect(bind).get_columns("engine_rate_policies")}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "worker_hourly_cap"):
        op.add_column("engine_rate_policies",
                      sa.Column("worker_hourly_cap", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "worker_hourly_cap"):
        op.drop_column("engine_rate_policies", "worker_hourly_cap")
