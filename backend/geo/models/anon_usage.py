"""Anonymous usage log — the quota basis for login-free capabilities (see geo/services/anon_quota.py).

One row per call. Only the minimum needed for counting is stored: capability name, device identifier, egress IP, time.
No query content, no brand names — these are anonymous users with no account to attribute to, so the less stored the better.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base


class AnonUsageORM(Base):
    __tablename__ = "anon_usage"
    __table_args__ = (
        Index("ix_anon_usage_feat_dev", "feature", "device_id", "used_at"),
        Index("ix_anon_usage_feat_ip", "feature", "ip", "used_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    feature = Column(String(length=32), nullable=False)        # mention / rival / ...
    device_id = Column(String(length=64), nullable=False, default="")   # Client-generated, can be cleared
    ip = Column(String(length=64), nullable=False, default="")          # Egress IP, hard constraint
    note = Column(String(length=255), nullable=False, default="")       # For troubleshooting (e.g. brand name hash)
    used_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AnonQuickTaskORM(Base):
    """Async task of the login-free engine quick test.

    Why async: engine measurements go through the batch-run center → account pool → browser/HTTP engines, taking minutes;
    waiting synchronously would tie up workers and hit nginx timeouts. The client hides polling, so to the user it still feels like one command.
    """
    __tablename__ = "anon_quick_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(length=40), nullable=False, unique=True, index=True)
    feature = Column(String(length=32), nullable=False)         # mention / rival
    brand = Column(String(length=128), nullable=False, default="")
    device_id = Column(String(length=64), nullable=False, default="")
    ip = Column(String(length=64), nullable=False, default="")
    status = Column(String(length=16), nullable=False, default="running")   # running/done/failed
    result_json = Column(Text, nullable=True)
    error = Column(String(length=512), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
