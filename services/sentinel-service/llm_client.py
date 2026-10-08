"""LLM client - single-provider direct connection, switched via the LLM_PROVIDER env.

Design:
- Defaults to GLM (Zhipu AI), OpenAI-compatible endpoint https://open.bigmodel.cn/api/paas/v4/.
- Switching to another backend only needs `export LLM_PROVIDER=deepseek` / `qwen` / `openai`, plus
  the corresponding *_API_KEY in .env.
- No fallback. Failures raise native openai SDK exceptions, and the upper layer (service.py's _wrap)
  wraps them uniformly as sentinel "failed" + error category.

Why no fallback:
- A fallback chain amplifies the failure surface (GLM rate limit -> OpenAI network -> Qwen out of balance; any one breaking misleads troubleshooting).
- Switching providers is an ops decision (account top-up / switch); the runtime should not decide on its own.
"""
from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import httpx
import openai

log = logging.getLogger("sentinel.llm")

# RateLimit backoff within the same provider (free-tier RPM limits are common; GLM 1302 / DashScope
# Throttling etc. all go this way). Not a cross-provider fallback - a single provider stays a single provider.
_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_BASE_WAIT = 6.0  # seconds, first wait; exponential backoff + jitter afterwards


# ───────────────────── env bootstrap ─────────────────────
# The shell that starts sentinel-service may not export the repo .env keys into env;
# proactively read the repo root .env (only fills missing env, never overrides externally set values).
def _bootstrap_env_from_dotenv() -> None:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    here = Path(__file__).resolve()
    for parent in here.parents:
        envf = parent / ".env"
        if not envf.exists():
            continue
        try:
            vals = dotenv_values(envf)
        except Exception:  # noqa: BLE001
            return
        for k in (
            "LLM_PROVIDER",
            "GLM_API_KEY", "GLM_BASE_URL",
            "QWEN_API_KEY", "QWEN_BASE_URL",
            "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            # search-related env (CN data center fallback / cookie)
            "DDG_REMOTE_URL", "DDG_REMOTE_TOKEN",
            "DDGS_BACKENDS", "DDGS_TIMEOUT_S",
            "BAIDU_COOKIE",
            "SEARXNG_URL",
            "HTTP_PROXY", "HTTPS_PROXY",
        ):
            if not os.environ.get(k) and vals.get(k):
                os.environ[k] = vals[k]
        return


# ───────────────────── provider configuration ─────────────────────
# Change the default provider: export LLM_PROVIDER=deepseek (or qwen / openai)
# Change the default model: export <PROVIDER>_MODEL=xxx e.g. GLM_MODEL=glm-4-plus
_PROVIDERS: dict[str, dict[str, Any]] = {
    "glm": {
        "key_env":    "GLM_API_KEY",
        "base_url":   "https://open.bigmodel.cn/api/paas/v4/",
        "model_env":  "GLM_MODEL",
        "default_model": "glm-4.5-flash",
        "use_proxy":  False,
    },
    "deepseek": {
        "key_env":    "DEEPSEEK_API_KEY",
        "base_url":   "https://api.deepseek.com/v1",
        "model_env":  "DEEPSEEK_MODEL",
        # DeepSeek deprecated deepseek-chat in 2026-07; only deepseek-v4-pro / deepseek-v4-flash are supported now.
        # At runtime the DEEPSEEK_MODEL env takes precedence (see the geo-sentinel drop-in); this default is only a fallback.
        "default_model": "deepseek-v4-flash",
        "use_proxy":  False,
    },
    "qwen": {
        "key_env":    "QWEN_API_KEY",
        "base_url":   "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env":  "QWEN_MODEL",
        "default_model": "qwen-plus",
        "use_proxy":  False,
    },
    "openai": {
        "key_env":    "OPENAI_API_KEY",
        "base_url":   None,  # SDK default https://api.openai.com/v1
        "model_env":  "OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "use_proxy":  True,   # direct connection from China does not work, must go through HTTPS_PROXY
    },
}


def _provider() -> str:
    name = (os.environ.get("LLM_PROVIDER") or "glm").lower()
    if name not in _PROVIDERS:
        raise RuntimeError(
            f"unknown LLM_PROVIDER={name!r}. valid: {sorted(_PROVIDERS)}"
        )
    return name


def _provider_cfg() -> dict[str, Any]:
    cfg = dict(_PROVIDERS[_provider()])
    # base_url can be overridden by the <PROVIDER>_BASE_URL env - DeepSeek direct access has fully moved to the neolink
    # gateway; the key is only valid on the gateway, and calling api.deepseek.com always returns 401.
    override = os.environ.get(cfg["key_env"].replace("_API_KEY", "_BASE_URL"))
    if override:
        cfg["base_url"] = override.rstrip("/")
    return cfg


# ───────────────────── http client (proxy strategy) ─────────────────────
# The repo environment has ALL_PROXY=socks5://... (httpx reads it by default), which needs the socksio package that we do not install;
# so trust_env=False everywhere. OpenAI is unreachable from China, so when use_proxy=True we explicitly
# inject HTTP(S)_PROXY into the underlying httpx.
def _http_client(use_proxy: bool) -> httpx.Client:
    # 60s is not enough for reasoning models - deepseek-v4-flash on neolink generating a monitoring plan
    # (long JSON) often takes over a minute; with two SDK retries, one run-monitor waits three minutes for nothing and is marked failed.
    timeout = httpx.Timeout(float(os.environ.get("LLM_TIMEOUT_S", "180")), connect=10.0)
    if not use_proxy:
        return httpx.Client(trust_env=False, timeout=timeout)
    proxy = (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        or os.environ.get("PROXY_URL")
    )
    if proxy and proxy.startswith(("http://", "https://")):
        return httpx.Client(trust_env=False, proxy=proxy, timeout=timeout)
    return httpx.Client(trust_env=False, timeout=timeout)


# ───────────────────── client construction ─────────────────────
def make_client() -> openai.OpenAI:
    cfg = _provider_cfg()
    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        raise RuntimeError(
            f"LLM_PROVIDER={_provider()} but {cfg['key_env']} is not set"
        )
    kwargs: dict[str, Any] = {"api_key": api_key, "http_client": _http_client(cfg["use_proxy"])}
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]
    return openai.OpenAI(**kwargs)


def _model() -> str:
    cfg = _provider_cfg()
    return os.environ.get(cfg["model_env"]) or cfg["default_model"]


# ───────────────────── public API ─────────────────────
def chat_create(_legacy_default_model: str = "", *, _usage_feature: str | None = None,
                **kwargs: Any):
    """Thin wrapper around chat.completions.create.

    _legacy_default_model only exists for signature compatibility with existing call sites (plan / analyze / brief / draft
    pass things like 'gpt-4o-mini' internally); the actual model is decided by LLM_PROVIDER + the *_MODEL env.
    Other parameters (messages / response_format / max_completion_tokens / ...) are passed through.

    On RateLimit, retries a limited number of times with exponential backoff, then raises. Batch callers (analyze looping over N posts)
    should add extra throttling on the outside so a burst does not exhaust the per-minute RPM.

    Usage accounting (storage/llm_usage.py -> main DB public.llm_call_log): this is the **only model
    egress in the whole service**, so recording here covers all 10 call sites. The feature scenario is tagged by each call site's set_feature().
    **Every failed rate-limit retry is also recorded as a row**, otherwise being rate-limited would be completely invisible on the usage page.

    Accounting is skipped when _usage_feature="__external__" - telemetry-service soft-imports this
    function to reuse its provider configuration (app/llm.py); those calls are recorded by telemetry itself with the real scenario,
    so recording them here too would double count.
    """
    kwargs.pop("model", None)
    external = _usage_feature == "__external__"
    client = make_client()
    last_exc: openai.RateLimitError | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            resp = client.chat.completions.create(model=_model(), **kwargs)
        except openai.RateLimitError as e:
            if not external:
                _record(status="http_error", error_kind="http_429", attempt=attempt, t0=t0)
            last_exc = e
            if attempt == _RATE_LIMIT_RETRIES:
                break
            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt) + random.uniform(0, 1.0)
            log.warning(
                "%s/%s rate-limited (%s) — backoff %.1fs (attempt %d/%d)",
                _provider(), _model(), e, wait, attempt + 1, _RATE_LIMIT_RETRIES,
            )
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001 - re-raise unchanged after recording, so the caller sees the same exception
            if not external:
                _record(status="http_error", error_kind=type(e).__name__[:24],
                        attempt=attempt, t0=t0)
            raise
        else:
            if not external:
                _record(status="ok", usage=getattr(resp, "usage", None),
                        attempt=attempt, t0=t0, feature=_usage_feature)
            return resp
    assert last_exc is not None
    raise last_exc


def _record(*, status: str, usage: Any = None, error_kind: str = "",
            attempt: int = 0, t0: float = 0.0, feature: str | None = None) -> None:
    """Record one call in the model usage table. Soft import + full try - accounting must never affect sentiment monitoring batch runs."""
    try:
        from storage.llm_usage import record_llm_call

        cfg = _provider_cfg()
        record_llm_call(
            provider=_provider(), model=_model(), usage=usage, status=status,
            error_kind=error_kind, attempt_no=attempt,
            latency_ms=int((time.perf_counter() - t0) * 1000) if t0 else 0,
            base_url=cfg.get("base_url") or "", feature=feature,
        )
    except Exception:  # noqa: BLE001
        pass


# Compatibility - existing call sites used has_openai/has_qwen to check "is the key ready";
# now it means "is the currently selected provider available".
def has_llm() -> bool:
    try:
        cfg = _provider_cfg()
    except RuntimeError:
        return False
    return bool(os.environ.get(cfg["key_env"]))


# For compatibility with has_openai / has_qwen calls in search/plan.py etc., keep these two names:
# both are now equivalent to has_llm() (single-provider mode does not distinguish).
has_openai = has_llm
has_qwen = has_llm


_bootstrap_env_from_dotenv()
