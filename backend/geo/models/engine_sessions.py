"""Engine session pool — centralized Playwright storage_state storage.

每条记录 = 一个用户在一台机器上对某个 engine 完成的一次完整登录所产生的
完整 storage_state(cookies + localStorage + sessionStorage 全量)。
browser-service 从 pool 里 check-out 最少被用的一条,替换本地文件,
跑完 search 后 check-in 回报"是否被 CAPTCHA 挑了"。

参见 backend/alembic/versions/9f1a4c8e3b21_add_engine_sessions.py
和 backend/geo/api/engine_sessions.py。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base


class EngineSessionORM(Base):
    __tablename__ = "engine_sessions"
    __table_args__ = (
        Index("idx_engine_sessions_pool", "engine", "status", "captcha_count", "last_used_at"),
    )

    id = Column(Integer, primary_key=True)
    engine = Column(String, nullable=False, index=True)            # doubao/qwen/deepseek/wenxin/yuanbao
    source_label = Column(String, nullable=True)                   # "alice-mac", "bob-win-laptop", ...
    storage_state = Column(Text, nullable=False)                   # JSON 字符串
    user_agent = Column(String, nullable=True)
    platform = Column(String, nullable=True)                       # "MacIntel" / "Linux x86_64" / ...
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    captcha_count = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="active")      # active / quarantined / expired
    expires_at = Column(DateTime, nullable=True)


# ── Pydantic schemas ───────────────────────────────────────────────


class SessionUploadIn(BaseModel):
    engine: str = Field(..., description="doubao/qwen/deepseek/wenxin/yuanbao")
    storage_state: dict = Field(..., description="Playwright storage_state full JSON")
    source_label: Optional[str] = Field(None, description="who+device, e.g. 'alice-mac'")
    user_agent: Optional[str] = None
    platform: Optional[str] = None
    ttl_days: int = Field(7, ge=1, le=30, description="过期天数,默认 7,最多 30")


class SessionCheckedOut(BaseModel):
    """Returned to browser-service for use in a single /search call."""
    id: int
    engine: str
    source_label: Optional[str]
    storage_state: dict
    use_count: int


class SessionCheckIn(BaseModel):
    id: int
    captcha_triggered: bool = False


class PoolStatusEntry(BaseModel):
    engine: str
    active: int
    quarantined: int
    expired: int
    oldest_active_captured_at: Optional[datetime]
