"""Engine session pool — centralized Playwright storage_state storage.

每条记录 = 一个用户在一台机器上对某个 engine 完成的一次完整登录所产生的
完整 storage_state(cookies + localStorage + sessionStorage 全量)。
browser-service 从 pool 里 check-out 最少被用的一条,替换本地文件,
跑完 search 后 check-in 回报具体的 FailureType.

D1 schema 已扩展:fail_counts_json / lease / region / preferred_worker_id
(参见 backend/alembic/versions/a1b2c3d4e5f6_scheduler_p1_p3_p4.py)。

D2(本文件)实现 P1 — 失败信号 enum 化 + 各类型对应的 quarantine 策略。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base


class EngineSessionORM(Base):
    __tablename__ = "engine_sessions"
    __table_args__ = (
        Index("idx_engine_sessions_pool", "engine", "status", "captcha_count", "last_used_at"),
        Index("idx_sessions_lease", "engine", "status", "leased_until"),
        Index("idx_sessions_region", "engine", "status", "captured_from_region"),
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

    # P1 失败信号(D2)
    last_fail_type = Column(String, nullable=True)
    last_fail_at = Column(DateTime, nullable=True)
    fail_counts_json = Column(Text, nullable=False, default="{}")

    # P3 lease + region affinity(D2 schema 已加,逻辑在 D4 落)
    leased_by_worker_id = Column(Integer, nullable=True)
    leased_until = Column(DateTime, nullable=True)
    captured_from_region = Column(String, nullable=True)
    captured_from_ip = Column(String, nullable=True)
    preferred_worker_id = Column(Integer, nullable=True)


class FailureType(str, Enum):
    """check-in 时 worker 上报的本次使用结果.

    每个 type 有独立的 quarantine 政策(见 engine_sessions.py 的 _QUARANTINE_POLICY).
    """
    SUCCESS = "success"
    CAPTCHA = "captcha"
    EMPTY_ANSWER = "empty_answer"      # 返回空字符串,可能 UI 改版或 session 半失效
    LOGIN_LOST = "login_lost"          # 跳登录页 / 强制重登,session 彻底失效
    DOM_NOT_FOUND = "dom_not_found"    # 关键 selector 找不到,可能引擎改版
    TIMEOUT = "timeout"                # streaming 超过 max_wait
    CRASH = "crash"                    # 异常抛出,不计 session 账


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
    """check-in payload.

    New shape (D2+):
      {"id": 7, "result": "success" | "captcha" | ..., "error_msg": "..." | null}

    Backward-compat:旧 worker 仍发 {"id": 7, "captcha_triggered": true/false},
    自动翻译成 result=CAPTCHA / SUCCESS。允许过渡期 vm03 还没升级。
    """
    id: int
    result: FailureType = FailureType.SUCCESS
    error_msg: Optional[str] = None
    # 旧字段:不出现在新 API,只用于 deserialize 旧 worker payload
    captcha_triggered: Optional[bool] = None

    @model_validator(mode="after")
    def _translate_legacy(self) -> "SessionCheckIn":
        # 旧 worker 没设 result(默认 SUCCESS)但设了 captcha_triggered=true
        # → 视为 CAPTCHA。新 worker 用 result,captcha_triggered 不传。
        if self.captcha_triggered is True and self.result == FailureType.SUCCESS:
            self.result = FailureType.CAPTCHA
        return self


class PoolStatusEntry(BaseModel):
    engine: str
    active: int
    quarantined: int
    expired: int
    oldest_active_captured_at: Optional[datetime]
