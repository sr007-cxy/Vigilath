"""EngineSession — D4 hot browser model.

每个 EngineSession 持有一对长期(page, ctx) + 一条 pool session 的绑定关系.
query() 在已加载好的 page 上跑 (reset chat → input → wait → extract),
不再每次 launch + goto + setup,节省 ~10-15s per query.

Lifecycle:
  worker startup → instantiate EngineSession 给每个 engine → init() 检出 session
                                                             + launch browser
                                                             + 跑准备步骤
  per query     → query(text) 复用 hot (page, ctx),返回 EngineResult
  session 翻车  → rotate(reason: FailureType) close + check-in 老 + check-out 新 + 重 init
  worker shutdown → close() 关 browser + check-in session

并发安全:每个 EngineSession 内部有 asyncio.Lock,同一 instance 同一时刻只跑 1 个
query.要并发需要 M 个 instance 组成 pool(D7 P3 = C 阶段).

适配协议:engine adapter 要提供两个新方法:
  - _prepare_page(page, ctx) → None  (goto, banner, smart search toggle 等"准备好"工作)
  - _query_with_page(page, ctx, query) → EngineResult  (reset + input + wait + extract)
若 adapter 没实现,EngineSession.init() 会 raise NotImplementedError.

D4 POC 只接 deepseek,其他 4 个 engine 沿用旧的 search() 一次性流程.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .browser import create_stealth_page
from .engines.base import EngineAdapter, EngineResult
from .session_store import report_session_outcome


_log = logging.getLogger(__name__)


class EngineSession:
    def __init__(self, engine_name: str, adapter: EngineAdapter):
        self.engine_name = engine_name
        self.adapter = adapter
        self.page = None
        self.ctx = None
        self.query_count = 0
        self.last_used_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    # ── Adapter capability check ─────────────────────────────────
    def _has_hot_protocol(self) -> bool:
        return (
            hasattr(self.adapter, "_prepare_page")
            and hasattr(self.adapter, "_query_with_page")
        )

    # ── Lifecycle ────────────────────────────────────────────────
    async def init(self) -> None:
        """Launch browser + load session + adapter._prepare_page."""
        if not self._has_hot_protocol():
            raise NotImplementedError(
                f"engine {self.engine_name} 没实现 hot protocol "
                "(_prepare_page + _query_with_page).D4 阶段仅 deepseek 支持."
            )
        async with self._lock:
            if self._initialized:
                return
            t0 = time.time()
            # session_store.load_storage_state(engine) 在 create_stealth_page 内部
            # 已经做了 pool check-out + fallback,这里直接用即可
            self.page, self.ctx = await create_stealth_page(self.engine_name)
            await self.adapter._prepare_page(self.page, self.ctx)
            self._initialized = True
            _log.info("[hot-session] %s init OK in %.1fs", self.engine_name, time.time() - t0)

    async def query(self, query_text: str) -> EngineResult:
        """Reuse hot page to run one query.

        并发请求会串行化(_lock).调用前 init() 必须已完成,否则 raise.
        """
        if not self._initialized:
            await self.init()
        async with self._lock:
            t0 = time.time()
            try:
                result = await self.adapter._query_with_page(self.page, self.ctx, query_text)
                self.query_count += 1
                self.last_used_at = time.time()
                _log.info(
                    "[hot-session] %s query #%d in %.1fs ans_len=%d err=%s",
                    self.engine_name, self.query_count, time.time() - t0,
                    len(result.answer or ""), result.error,
                )
                # 失败信号反馈给 pool.SUCCESS 不必每次都报(check-in 用 SUCCESS 也行,
                # 但会涨 use_count;hot 模式下我们用 query_count 自己 track,只在
                # 真 failure 时 report,SUCCESS 静默)
                if result.error:
                    report_session_outcome(self.engine_name, result=_classify_error(result.error))
                return result
            except Exception as e:
                _log.exception("[hot-session] %s query crashed: %s", self.engine_name, e)
                report_session_outcome(self.engine_name, result="crash", error_msg=str(e))
                return EngineResult(engine=self.adapter.name, query=query_text, error=str(e))

    async def rotate(self, reason: str = "manual") -> None:
        """Close current ctx + check-in session + re-init with fresh session."""
        async with self._lock:
            _log.info("[hot-session] %s rotate reason=%s", self.engine_name, reason)
            await self._close_inner(check_in_result=reason)
            self._initialized = False
        # 退出 lock 再调 init(init 自己取 lock,避免死锁)
        await self.init()

    async def close(self) -> None:
        """Worker shutdown: close ctx + check-in session as SUCCESS."""
        async with self._lock:
            await self._close_inner(check_in_result="success")
            self._initialized = False

    async def _close_inner(self, check_in_result: str) -> None:
        if self.ctx is not None:
            try:
                await self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            self.page = None
        # check-in 当前 session;session_store 的 _last_checkout 是 thread-local,
        # init/query 是 async task 共享同一个 thread-local,这里能 hit 同一条
        report_session_outcome(self.engine_name, result=check_in_result)


def _classify_error(err_msg: str) -> str:
    """Best-effort 把 adapter 报的 error 字符串映射到 FailureType."""
    e = (err_msg or "").lower()
    if "textarea not visible" in e or "input not visible" in e or "login may have expired" in e:
        return "login_lost"
    if "captcha" in e or "验证" in e:
        return "captcha"
    if "timeout" in e or "超时" in e:
        return "timeout"
    if "not found" in e or "selector" in e:
        return "dom_not_found"
    return "crash"
