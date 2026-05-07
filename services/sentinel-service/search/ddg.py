"""Thin DuckDuckGo wrapper.

Uses the `ddgs` package (formerly `duckduckgo-search`). Region defaults to
`wt-wt` (no region bias) so a single query gets both Chinese and English
results in one shot — the analyzer is bilingual.
"""
from __future__ import annotations

import os
import time

try:
    from ddgs import DDGS  # ddgs >= 9.x
except ImportError:  # pragma: no cover — older package name
    from duckduckgo_search import DDGS  # type: ignore


# 默认 backend="auto" 会把 yandex/yahoo/grokipedia/mojeek/wikipedia 全试一遍,
# 国内网络下 yandex 几乎必超时、grokipedia 时不时挂.把范围收敛到稳定 + 国内
# 可达(走代理)的几家,大幅降低单查询耗时.可用 DDGS_BACKENDS env 覆盖.
_DEFAULT_BACKENDS = "duckduckgo,brave,bing"
# 单引擎调用超时(秒).ddgs 默认 10s — 偶尔超时累计起来 monitor 几十个 query
# 会被 backend 5 分钟 TIMEOUT_MONITOR 截断,所以拉短.
_DEFAULT_TIMEOUT = 8


def _ddgs_proxy() -> str | None:
    """搜索引擎在国内多数需要代理.读 HTTP(S)_PROXY env."""
    return (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        or None
    )


def ddg_search(query: str, max_results: int = 10,
               region: str = "wt-wt", safesearch: str = "off",
               timelimit: str | None = None) -> list[dict]:
    """Returns a list of {title, href, body} dicts.

    timelimit: 'd' (past day), 'w' (past week), 'm' (past month), 'y' (past year),
               or None (no limit).
    """
    backends = os.environ.get("DDGS_BACKENDS") or _DEFAULT_BACKENDS
    try:
        timeout = int(os.environ.get("DDGS_TIMEOUT_S") or _DEFAULT_TIMEOUT)
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    proxy = _ddgs_proxy()
    # 不同 ddgs 版本签名略有差,用 try/except 兜一下旧版.
    try:
        d = DDGS(proxy=proxy, timeout=timeout)
    except TypeError:
        d = DDGS()
    with d:
        try:
            results = list(d.text(
                query,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
                max_results=max_results,
                backend=backends,
            ))
        except TypeError:
            # 旧版 ddgs 不支持 backend 参数 — 退回 auto.
            results = list(d.text(
                query,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
                max_results=max_results,
            ))
    return results


def throttle(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
