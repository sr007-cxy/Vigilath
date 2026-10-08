"""Physical device fleet: per-device runtime parameters, events, and egress rate limits.

Several physical devices can share an egress while their runtime limits remain
per-device. Treating their independent limits as aggregate egress capacity can
therefore overestimate safe throughput. Device health can also differ even when
all devices are connected, so the control plane needs per-device policy and
event data rather than one global runtime configuration.

This migration lands three things:

1. A doubao_browser row in engine_rate_policies: egress-level rate-limit parameters reuse the existing table and the existing
   three-gate semantics (ip_hourly_cap / ip_min_gap_sec / ip_daily_cap), no new table. The account pool side
   (backend/geo/api/engine_sessions.py::check_out) already runs the same scheme; the physical device fleet copies it.

2. Two columns added to engine_rate_policies:
     worker_daily_cap    daily capacity per physical device (denominator for fleet capacity, previously hard-coded in the scheduler)
     worker_min_ok_rate  quality gate threshold (percent); a device is automatically drained when its success rate over the last N falls below it

3. Two new tables, worker_policies / worker_events: the former puts each machine's runtime parameters in the DB,
   delivered via heartbeat and effective immediately (worker.py already merges hb["config"] on every heartbeat, with no device-side code changes);
   the latter records "what happened on this machine", fixing "it stopped and nobody knows why".

Hangs off c8f4b2a71d96 (that pure merge revision), not directly off c1f8b3d24e97:
the latter has already been converged by c8f4b2a71d96, and branching from it again would split the just-merged head into two,
leaving the deploy-side `alembic upgrade head` without a unique target. The engine_rate_policies this migration changes
is created by c1f8b3d24e97, which is an ancestor of c8f4b2a71d96, so the ordering still holds.

Revision ID: b8f4c2e79a51
Revises: c8f4b2a71d96
"""
from alembic import op
import sqlalchemy as sa

revision = "b8f4c2e79a51"
down_revision = "c8f4b2a71d96"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1) Add two columns to engine_rate_policies ────────────────────────────────
    op.add_column("engine_rate_policies",
                  sa.Column("worker_daily_cap", sa.Integer(), nullable=True))
    op.add_column("engine_rate_policies",
                  sa.Column("worker_min_ok_rate", sa.Integer(), nullable=True))

    # ── 2) Runtime parameters per physical device ─────────────────────────────────────────
    op.create_table(
        "worker_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_uid", sa.String(), nullable=False),
        # Overrides (JSON object). Merged into the heartbeat response config; the worker applies each key hot.
        # Only write keys to change: empty object = fully follow the global WORKER_CONFIG.
        sa.Column("config_json", sa.Text(), nullable=True),
        # JSON array: engines not dispatched to this machine. Replaces env WORKER_ENGINE_DENY_JSON (env kept as fallback)
        sa.Column("denied_engines", sa.Text(), nullable=True),
        # References engine_proxies.id. No foreign key constraint: the telemetry side has a mirror table;
        # the two services share a DB but model separately, and a constraint would make either side's table creation order an implicit dependency.
        sa.Column("proxy_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("uq_worker_policies_uid", "worker_policies",
                    ["worker_uid"], unique=True)

    # ── 3) Machine event stream ────────────────────────────────────────────────
    op.create_table(
        "worker_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_uid", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        # registered / online / offline / drained / resumed / upgraded /
        # config_changed / paused / stopped
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
    )
    # The panel fetches the last N by (machine, time desc); this composite index covers it exactly
    op.create_index("ix_worker_events_uid_at", "worker_events", ["worker_uid", "at"])

    # ── 4) Starting egress rate-limit values for doubao_browser ────────────────────────────
    # 8/hour: measured that a single session hits the wall after 5 consecutive jobs and self-heals in about 18 minutes -> single-egress ceiling about 15/hour; take half for headroom.
    # The three egress gates are **safety ceilings, not operating limits**: the data corrected this on launch day.
    #
    # Initially, inferring "5 machines sharing one egress = 5x density", we set 8/hour, 100/day, and right after launch it cut a fleet with
    # a 100% success rate in half. Only going through the daily logs showed the inference was wrong:
    #
    #   08-21  claimed 215  succeeded 215  100%
    #   08-24  claimed 270  succeeded 270  100%   <- the busiest day, yet the best
    #   08-27  claimed 147  succeeded  24   16%
    #   08-29  claimed 189  succeeded  13    7%   <- lower volume, yet the worst
    #   08-31  claimed 194  succeeded 189   97%   <- hit 81 in a single hour and still 100%
    #
    # **Daily claim volume is uncorrelated with failure rate**. If it really were a shared-egress density problem, the 270 day should have been the worst.
    # The two collapses on 08-27/08-29 had other causes (sporadic: login wall / profile running into a blank state / changes on the Doubao side),
    # which rate limiting cannot stop and should not be made to pay for.
    #
    # So these three numbers are set "above the measured healthy values": 270/day and 81/hour were observed fully healthy,
    # so we give 400/day, 120/hour, only as a fallback against runaways (e.g. a newly added batch of machines hammering without restraint).
    # Egress gap is 0: at a pace of 81 per hour a 240s cross-machine gap is meaningless, and worker.py
    # already has its own per-machine task_gap_min/max_sec gap.
    #
    # What actually catches failures is worker_min_ok_rate (the quality gate). It targets **results**, not traffic:
    # there is a wide gap between the 16% / 7% collapse days and the 96~100% normal days, and 20% sits in the middle,
    # neither hurting normal fluctuation nor failing to pull broken machines within 6 hours; and those two collapses
    # went unnoticed for days at the time.
    op.execute("""
        INSERT INTO engine_rate_policies
            (engine, ip_min_gap_sec, ip_hourly_cap, ip_daily_cap,
             worker_daily_cap, worker_min_ok_rate, enabled, note, updated_by)
        VALUES
            ('doubao_browser', 0, 120, 400, 120, 20, true,
             '真机机队安全上限,不是运行限额。实测健康值:08-24 单日 270 次 100%、08-31 单小时 81 次 100% —— 日领取量与失败率不相关,所以这三个数只当跑飞了的兜底。真正抓故障的是成色闸 worker_min_ok_rate', 'migration')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM engine_rate_policies WHERE engine='doubao_browser'")
    op.drop_index("ix_worker_events_uid_at", table_name="worker_events")
    op.drop_table("worker_events")
    op.drop_index("uq_worker_policies_uid", table_name="worker_policies")
    op.drop_table("worker_policies")
    op.drop_column("engine_rate_policies", "worker_min_ok_rate")
    op.drop_column("engine_rate_policies", "worker_daily_cap")
