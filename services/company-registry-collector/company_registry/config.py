"""Common configuration (independent of any specific provider).

Provider-specific configuration (e.g. each provider's Cookie, region/capital-band definitions) lives inside each provider,
read from its own environment variables, e.g. the qichamao provider reads `QICHAMAO_COOKIE`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Name patterns marking a "non-target company" (government/public institution/school), used for stats and as a filtering fallback; provider-agnostic
NON_COMPANY_PATTERN = (
    r"财政局|财政税务|税务所|税务局|财税所|干部学校|职业学院|会计学校|"
    r"人民政府|办公室|食堂|财源建设|工作领导"
)


@dataclass
class Settings:
    """Common parameters for collection pace and scale."""
    user_agent: str = field(default_factory=lambda: os.environ.get("ENTDATA_UA", DEFAULT_UA))
    request_interval: float = field(default_factory=lambda: float(os.environ.get("ENTDATA_INTERVAL", "0.8")))
    request_timeout: int = field(default_factory=lambda: int(os.environ.get("ENTDATA_TIMEOUT", "25")))
    max_consecutive_failures: int = 5
    # Hard cap on detail enrichment (when unique companies exceed it, only the first N are enriched)
    max_detail: int = field(default_factory=lambda: int(os.environ.get("ENTDATA_MAX_DETAIL", "500")))
