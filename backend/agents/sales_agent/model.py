"""Sales Agent's dedicated Yuanbao/Hunyuan model factory.

Model configuration is resolved only within the Sales Agent's own directory, without reusing the GEO Agent's model factory.
Variable names are compatible with the project's existing Yuanbao API patterns:
- Company public web search: backend/agents/sales_agent/discovery.py uses TENCENT_SEARCH_API_KEY / TENCENT_SEARCH_CNT.
- Yuanbao web-connected synthesis: compatible with DEEPSEEK_API_KEY / YUANBAO_SYNTH_MODEL_DEEPSEEK and
  OPENROUTER_API_KEY / YUANBAO_SYNTH_MODEL.
- If production has direct Yuanbao/Hunyuan configured, prefer YUANBAO_API_KEY / HUNYUAN_API_KEY /
  TENCENT_HUNYUAN_API_KEY and other TokenHub/Hunyuan variables.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


_HUNYUAN_DEFAULT_MODEL = {"chat": "hy3", "pro": "hy3"}
# Aligned with the default model of Yuanbao API synthesis in V-networks/worker-center/app/yuanbao_search.py.
_YUANBAO_SYNTH_DEEPSEEK_DEFAULT_MODEL = {"chat": "deepseek-v4-flash", "pro": "deepseek-v4-flash"}
_YUANBAO_SYNTH_OPENROUTER_DEFAULT_MODEL = {"chat": "deepseek/deepseek-v4-flash", "pro": "deepseek/deepseek-v4-flash"}
_HUNYUAN_DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class SalesAgentModelConfig:
    model_name: str
    base_url: str
    api_key: str
    via_openrouter: bool = False


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _sales_agent_provider_hint() -> str:
    """Explicit SALES_AGENT_MODEL_* config is treated as Yuanbao/Hunyuan by default, unless base/model points to DeepSeek."""
    base_url = _first_env("SALES_AGENT_MODEL_BASE_URL", "SALE_AGENT_MODEL_BASE_URL").lower()
    model_name = _first_env("SALES_AGENT_MODEL", "SALE_AGENT_MODEL").lower()
    if "deepseek" in base_url or model_name.startswith("deepseek") or "/deepseek" in model_name:
        return "deepseek_synth"
    return "hunyuan"


def _normalize_openrouter_model(model_name: str) -> str:
    name = (model_name or "").strip()
    if name and "/" not in name and name.startswith("deepseek"):
        return f"deepseek/{name}"
    return name


def _scene_model(scene: str, *, provider: str) -> str:
    key = scene.upper()
    common_scene = [f"SALES_AGENT_MODEL_{key}", f"SALE_AGENT_MODEL_{key}"]
    common_global = ["SALES_AGENT_MODEL", "SALE_AGENT_MODEL"]

    if provider == "hunyuan":
        provider_scene = [
            f"YUANBAO_MODEL_{key}",
            f"HUNYUAN_MODEL_{key}",
            f"TENCENT_HUNYUAN_MODEL_{key}",
            f"TENCENT_MAAS_MODEL_{key}",
            f"TENCENTMAAS_MODEL_{key}",
            f"TOKENHUB_MODEL_{key}",
            f"YUANBAO_{key}_MODEL",
            f"HUNYUAN_{key}_MODEL",
            f"TENCENT_HUNYUAN_{key}_MODEL",
        ]
        provider_global = [
            "YUANBAO_MODEL",
            "HUNYUAN_MODEL",
            "TENCENT_HUNYUAN_MODEL",
            "TENCENT_MAAS_MODEL",
            "TENCENTMAAS_MODEL",
            "TENCENT_TOKENHUB_MODEL",
            "TOKENHUB_MODEL",
        ]
        defaults = _HUNYUAN_DEFAULT_MODEL
    elif provider == "openrouter":
        provider_scene = [
            f"YUANBAO_SYNTH_MODEL_{key}",
            f"OPENROUTER_MODEL_YUANBAO_{key}",
            f"OPENROUTER_MODEL_HUNYUAN_{key}",
            f"OPENROUTER_DEEPSEEK_MODEL_{key}",
        ]
        provider_global = [
            # Kept consistent with telemetry-service's Yuanbao API synthesis variables.
            "YUANBAO_SYNTH_MODEL",
            # Kept consistent with the OPENROUTER_MODEL_<engine> convention in telemetry-service/openrouter.py.
            "OPENROUTER_MODEL_YUANBAO",
            "OPENROUTER_MODEL_HUNYUAN",
            "OPENROUTER_DEEPSEEK_MODEL",
        ]
        defaults = _YUANBAO_SYNTH_OPENROUTER_DEFAULT_MODEL
    else:
        provider_scene = [
            f"YUANBAO_SYNTH_MODEL_DEEPSEEK_{key}",
            f"YUANBAO_DEEPSEEK_MODEL_{key}",
            f"AGENT_MODEL_{key}",
            f"DEEPSEEK_MODEL_{key}",
        ]
        provider_global = [
            # Exactly the same name as in telemetry-service/app/yuanbao_search.py.
            "YUANBAO_SYNTH_MODEL_DEEPSEEK",
            "YUANBAO_DEEPSEEK_MODEL",
            "AGENT_MODEL",
            "DEEPSEEK_MODEL",
        ]
        defaults = _YUANBAO_SYNTH_DEEPSEEK_DEFAULT_MODEL

    model_name = _first_env(*(common_scene + provider_scene + common_global + provider_global)) or defaults.get(scene, defaults["pro"])
    if provider == "openrouter":
        return _normalize_openrouter_model(model_name)
    return model_name


def resolve_sales_agent_model_config(scene: str = "chat") -> SalesAgentModelConfig:
    """Resolve the Sales Agent brain model configuration.

    Priority:
    1. SALES_AGENT_MODEL_API_KEY / SALE_AGENT_MODEL_API_KEY explicit override, defaulting to direct Yuanbao/Hunyuan;
    2. YUANBAO_API_KEY / HUNYUAN_API_KEY / TENCENT_HUNYUAN_API_KEY and other direct Yuanbao/Hunyuan;
    3. the project's existing Yuanbao API synthesis variables DEEPSEEK_API_KEY / YUANBAO_SYNTH_MODEL_DEEPSEEK;
    4. the project's existing OPENROUTER_API_KEY / YUANBAO_SYNTH_MODEL fallback.
    """
    sales_agent_key = _first_env("SALES_AGENT_MODEL_API_KEY", "SALE_AGENT_MODEL_API_KEY")
    if sales_agent_key:
        provider = _sales_agent_provider_hint()
        if provider == "hunyuan":
            base_url = _first_env(
                "SALES_AGENT_MODEL_BASE_URL",
                "SALE_AGENT_MODEL_BASE_URL",
                "YUANBAO_BASE_URL",
                "HUNYUAN_BASE_URL",
                "TENCENT_HUNYUAN_BASE_URL",
                "TENCENT_MAAS_BASE_URL",
                "TENCENTMAAS_BASE_URL",
                "TOKENHUB_BASE_URL",
            ) or _HUNYUAN_DEFAULT_BASE_URL
        else:
            base_url = _first_env("SALES_AGENT_MODEL_BASE_URL", "SALE_AGENT_MODEL_BASE_URL", "DEEPSEEK_BASE_URL") or _DEEPSEEK_DEFAULT_BASE_URL
        return SalesAgentModelConfig(
            model_name=_scene_model(scene, provider=provider),
            base_url=base_url.rstrip("/"),
            api_key=sales_agent_key,
            via_openrouter=False,
        )

    hunyuan_key = _first_env(
        "YUANBAO_API_KEY",
        "HUNYUAN_API_KEY",
        "TENCENT_HUNYUAN_API_KEY",
        "TENCENT_MAAS_API_KEY",
        "TENCENTMAAS_API_KEY",
        "TENCENT_TOKENHUB_API_KEY",
        "TOKENHUB_API_KEY",
    )
    if hunyuan_key:
        base_url = _first_env(
            "YUANBAO_BASE_URL",
            "HUNYUAN_BASE_URL",
            "TENCENT_HUNYUAN_BASE_URL",
            "TENCENT_MAAS_BASE_URL",
            "TENCENTMAAS_BASE_URL",
            "TENCENT_TOKENHUB_BASE_URL",
            "TOKENHUB_BASE_URL",
        ) or _HUNYUAN_DEFAULT_BASE_URL
        return SalesAgentModelConfig(
            model_name=_scene_model(scene, provider="hunyuan"),
            base_url=base_url.rstrip("/"),
            api_key=hunyuan_key,
            via_openrouter=False,
        )

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return SalesAgentModelConfig(
            model_name=_scene_model(scene, provider="deepseek_synth"),
            base_url=(os.environ.get("DEEPSEEK_BASE_URL", "").strip() or _DEEPSEEK_DEFAULT_BASE_URL).rstrip("/"),
            api_key=deepseek_key,
            via_openrouter=False,
        )

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        return SalesAgentModelConfig(
            model_name=_scene_model(scene, provider="openrouter"),
            base_url=(os.environ.get("OPENROUTER_BASE_URL", "").strip() or _OPENROUTER_DEFAULT_BASE_URL).rstrip("/"),
            api_key=openrouter_key,
            via_openrouter=True,
        )

    raise RuntimeError(
        "Sales Agent 元宝模型缺少配置:请配置 SALES_AGENT_MODEL_API_KEY / YUANBAO_API_KEY / HUNYUAN_API_KEY "
        "/ TENCENT_HUNYUAN_API_KEY,或复用项目现有 DEEPSEEK_API_KEY,或配置 OPENROUTER_API_KEY。"
    )


def get_sales_agent_model(scene: str = "chat"):
    """Build the Pydantic AI model. Cached by (scene, thread) — not to save objects, but to save connections.

    The thread id was added to the key later (scheduled sending): an httpx client is bound to the event loop that created it,
    and each sending thread of the scheduler process has its own loop. Caching only by scene, the second thread would get the first
    thread's pool and fail immediately with `Event loop is closed`.

    Including the thread id in the key is safe because loops are **long-lived per thread**
    (`service/send_schedule.py::_thread_event_loop`) — one thread = one loop = one pool.
    On the uvicorn side there's only one loop and one thread, so behavior is exactly as before.
    """
    return _sales_agent_model_for_thread(scene, threading.get_ident())


@lru_cache(maxsize=32)
def _sales_agent_model_for_thread(scene: str, thread_key: int):
    """The actual build + cache. maxsize raised from 8 to 32: the key gained a dimension (thread), while the scheduler process's
    sending threads are a fixed 4 long-lived ones, not growing with the number of plans. Eviction just rebuilds a connection pool once.

    Beneath the returned chain hangs an httpx connection pool:

        OpenAIModel → OpenAIProvider → AsyncOpenAI → httpx.AsyncClient → connection pool

    Without caching, every call creates a new pool, discarding the DNS/TCP/TLS already established last time, so every request
    goes through a cold start again. The chat path avoiding this was pure luck — `build_sales_agent` has its own
    `lru_cache`, which happened to cache the pool too; the intro letter, however, builds a new `Agent` in the SSE generator every time
    (`outreach_auto_draft_stream` in `api.py`), so every letter connected from scratch, and production measured
    "connect 12.4s".

    Cost: changing the `SALES_AGENT_MODEL_*` environment variables requires restarting `geo-agent` to take effect.
    `build_sales_agent` has always been like this; this stays consistent.

    The safety premise is **one pool per loop**. This used to say "a single event loop per process", which still holds in the
    geo-agent process; but the scheduled-sending scheduler process is multi-threaded with one long-lived loop per thread,
    so the cache key includes the thread id. Before adding thread/coroutine pools here, come back to this note — reusing an httpx
    client across loops raises `Event loop is closed`, and only blows up on the second call.
    """
    try:
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,请先 `pip install pydantic-ai-slim[openai]`") from e

    cfg = resolve_sales_agent_model_config(scene)
    provider = OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key)
    return OpenAIModel(cfg.model_name, provider=provider)


# Per-request model timeout, with defaults per scene; each must be shorter than that scene's whole-turn wall clock (`runtime/limits.timeout_for`),
# otherwise the degradation branch never gets its chance to trigger, and users see a spinner until the gateway times out.
#
# `pro`'s two call sites have different failure costs, so only a fairly generous fallback value is given (90s, estimated from the output volume of up to 3 report email
# variants); call sites needing faster degradation pass `timeout=` themselves to override — that's how the intro letter rewrite uses it:
# if it times out it falls back to the standard intro letter, and the user still gets a sendable letter, so a shorter wait is preferred.
_MODEL_REQUEST_TIMEOUTS: dict[str, float] = {"chat": 12.0, "pro": 90.0}


def _model_request_timeout(scene: str) -> float:
    """Per-request model timeout in seconds for the scene; 0 means no timeout (unregistered scenes keep the original behavior)."""
    default = _MODEL_REQUEST_TIMEOUTS.get(scene, 0.0)
    if default <= 0:
        return 0.0
    raw = os.environ.get(f"SALES_AGENT_MODEL_TIMEOUT_{scene.upper()}", "").strip()
    try:
        value = float(raw) if raw else 0
    except ValueError:
        value = 0
    return value if value > 0 else default


def get_sales_agent_model_settings(scene: str = "chat", timeout: float = 0.0):
    """Return Sales Agent model settings. A positive `timeout` overrides the scene's default timeout."""
    cfg = resolve_sales_agent_model_config(scene)
    kwargs: dict[str, Any] = {}
    request_timeout = float(timeout) if timeout and timeout > 0 else _model_request_timeout(scene)
    if request_timeout > 0:
        kwargs["timeout"] = request_timeout
    if cfg.via_openrouter:
        kwargs["extra_body"] = {"provider": {"require_parameters": True}}
    if not kwargs:
        return None
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    return OpenAIChatModelSettings(**kwargs)
