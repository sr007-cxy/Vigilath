"""Collection orchestration (provider-agnostic): keyword x city x capital band slicing -> list dedup -> detail enrichment.

    from company_registry import Collector, get_provider
    col = Collector(get_provider("qichamao"))
    res = col.run(word="代理记账", cities=["深圳"], bands="all")  # "bookkeeping agency", Shenzhen
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union

from .config import Settings
from .models import Company
from .providers.base import Provider

log = logging.getLogger("company_registry.collector")

BandsArg = Union[str, List[str], None]  # "all" / "none" / [regm...] / None


@dataclass
class CollectResult:
    source: str = ""
    companies: List[Company] = field(default_factory=list)
    slices: int = 0
    list_requests: int = 0
    detail_done: int = 0
    aborted: bool = False
    abort_reason: str = ""

    @property
    def unique(self) -> int:
        return len(self.companies)

    @property
    def targets(self) -> List[Company]:
        return [c for c in self.companies if c.is_target_company]

    def stats(self) -> dict:
        tg = self.targets
        wp = sum(1 for c in tg if c.phone)
        we = sum(1 for c in tg if c.emails)
        return {
            "source": self.source,
            "unique": self.unique,
            "target_companies": len(tg),
            "non_company": self.unique - len(tg),
            "with_phone": wp,
            "with_email": we,
            "phone_rate": round(wp / len(tg), 3) if tg else 0.0,
            "email_rate": round(we / len(tg), 3) if tg else 0.0,
            "detail_done": self.detail_done,
            "slices": self.slices,
            "list_requests": self.list_requests,
            "aborted": self.aborted,
        }


class Collector:
    def __init__(self, provider: Provider, settings: Optional[Settings] = None):
        self.provider = provider
        self.s = settings or Settings()

    def _resolve_bands(self, bands: BandsArg) -> List[str]:
        if bands == "all":
            return self.provider.capital_bands or [""]
        if bands in (None, "none", ""):
            return [""]
        if isinstance(bands, str):
            return [b.strip() for b in bands.split(",") if b.strip()]
        return list(bands)

    def run(
        self,
        word: str,
        cities: Optional[List[str]] = None,
        bands: BandsArg = None,
        company_type: Optional[str] = None,
        fetch_detail: bool = True,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> CollectResult:
        cities = cities or [""]
        band_list = self._resolve_bands(bands)
        ctype = self.provider.default_company_type if company_type is None else company_type
        res = CollectResult(source=self.provider.name)
        by_key: Dict[str, Company] = {}

        def emit(msg: str):
            if on_progress:
                on_progress(msg)
            else:
                log.info(msg)

        if not self.provider.ready():
            res.aborted = True
            res.abort_reason = f"provider {self.provider.name} 凭证未配置(如 Cookie)"
            emit("[!] " + res.abort_reason)
            return res

        # ---- Phase 1: list-level slicing ----
        for city in cities:
            before = len(by_key)
            for band in band_list:
                for o in range(self.provider.max_page):
                    lr = self.provider.list_companies(word, city, band, ctype, o)
                    res.list_requests += 1
                    if not lr.ok:
                        res.aborted = True
                        res.abort_reason = f"登录失效/反爬 @ {city or '全国'} band={band or '-'} o={o}"
                        emit("[!] " + res.abort_reason + " —— 中止列表层")
                        break
                    for c in lr.companies:
                        by_key.setdefault(c.dedup_key, c)
                res.slices += 1
                if res.aborted:
                    break
            emit(f"{city or '全国'}: 累计去重 {len(by_key)} (+{len(by_key) - before})")
            if res.aborted:
                break

        res.companies = list(by_key.values())
        emit(f"列表层去重总数: {res.unique}")

        # ---- Phase 2: detail enrichment ----
        if fetch_detail and not res.aborted:
            targets = res.companies[: self.s.max_detail]
            if res.unique > self.s.max_detail:
                emit(f"[i] 唯一 {res.unique} 超上限 {self.s.max_detail},仅补前 {self.s.max_detail} 家")
            consec = 0
            for i, c in enumerate(targets, 1):
                ok = self.provider.enrich(c)
                if not ok:
                    consec += 1
                    if consec >= self.s.max_consecutive_failures:
                        res.aborted = True
                        res.abort_reason = f"详情层连续 {consec} 次登录失效,已完成 {i-1}"
                        emit("[!] " + res.abort_reason)
                        break
                else:
                    consec = 0
                res.detail_done += 1
                if i % 20 == 0 or i == len(targets):
                    emit(f"  detail [{i}/{len(targets)}]")

        emit("完成: " + str(res.stats()))
        return res
