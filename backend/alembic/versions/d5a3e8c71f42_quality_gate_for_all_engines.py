"""Extend the quality gate to account-pool engines: add worker_min_ok_rate for deepseek/qwen/wenxin/Yuanbao

Previously only the doubao_browser row had a threshold; other engines had an empty worker_min_ok_rate = gate closed.
On 2026-08-30 Baidu replaced the "task mode" onboarding popup, and Wenxin returned 20~25% empty answers for two straight days with zero alerts:
the quality gate was running at the time, it just did not cover Wenxin.

Threshold set at 20%: measured collapse days are 7~16% and normal days 96~100%, with a wide gap between the two,
and 20% in the middle neither hurts normal fluctuation nor fails to pull a broken machine within 6 hours.

**It does not pull whole machines**: one server-side browser-service runs five engines at once,
so for multi-engine machines the quality gate only pulls the problematic engine (writing worker_policies.denied_engines);
only physical devices running a single engine are drained entirely. And when an engine fails the threshold on every machine, **no machine is pulled**:
that is an engine-side problem, and pulling machines would only zero out capacity.

Revision ID: d5a3e8c71f42
Revises: b8f4c2e79a51
"""
from alembic import op

revision = "d5a3e8c71f42"
down_revision = "b8f4c2e79a51"
branch_labels = None
depends_on = None

_ENGINES = ("deepseek", "qwen", "wenxin", "yuanbao")


def upgrade() -> None:
    for e in _ENGINES:
        # Existing rows only get this column (do not overwrite their rate-limit parameters); if there is no row, insert one with only the threshold
        op.execute(f"""
            INSERT INTO engine_rate_policies (engine, worker_min_ok_rate, enabled, note, updated_by)
            VALUES ('{e}', 20, true,
                    '成色闸阈值:近6h该引擎成功率低于此值且样本>=10 → 自动摘除并告警', 'migration')
            ON CONFLICT (engine) DO UPDATE
              SET worker_min_ok_rate = COALESCE(engine_rate_policies.worker_min_ok_rate, 20)
        """)


def downgrade() -> None:
    for e in _ENGINES:
        op.execute(f"UPDATE engine_rate_policies SET worker_min_ok_rate=NULL WHERE engine='{e}'")
