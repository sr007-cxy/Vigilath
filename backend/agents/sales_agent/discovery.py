"""Sales Agent public web company discovery.

Only uses public search / public directory leads to extract company identity info, and only fills in company-level public emails, phones and contact forms;
company info pages like Qichacha / Tianyancha / Aiqicha / Tanji serve only as company identity leads from search hits,
never circumventing platform restrictions to scrape detail pages or contact info. DB writes are left to service.py for unified ownership, dedup and compliance records.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

import requests

from agents.sales_agent.constants import MAX_RECOMMENDATIONS
from agents.sales_agent.contact_extraction import discover_public_contacts
from agents.sales_agent.runtime_log import duration_ms, new_model_call_id, now_monotonic, write_sales_agent_log

SEARCHPRO_URL = "https://api.wsa.cloud.tencent.com/SearchPro"

_ALLOWED_SEARCH_CNT = (10, 20, 30, 40, 50)
_DEFAULT_MAX_QUERIES = 12
_DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 30.0

_BLOCKED_DOMAINS = (
    "linkedin.com", "liepin.com", "zhipin.com", "51job.com",
    "zhaopin.com", "lagou.com", "kanzhun.com", "weibo.com", "zhihu.com",
    "baidu.com", "bing.com", "google.com", "sogou.com", "so.com",
)
_REGISTRY_HINTS = (
    "gsxt.gov.cn", "creditchina.gov.cn", "samr.gov.cn", "gov.cn",
)
_BUSINESS_DATA_HINTS = (
    "qichacha.com", "tianyancha.com", "aiqicha.baidu.com", "qixin.com", "tanzhi",
)
_DIRECTORY_HINTS = (
    "11467.com", "huangye88.com", "qiyeku.com", "china.cn", "hc360.com",
    "made-in-china.com", "1688.com", "b2b.baidu.com", "qiyesou.com",
)
_JUNK_TITLE_WORDS = (
    "招聘", "职位", "百科", "新闻", "资讯", "问答", "论坛", "贴吧", "地图",
    "批发价格", "联系方式大全", "电话号码", "手机号", "私人微信",
)
_COMPANY_RE = re.compile(
    r"([一-龥A-Za-z0-9（）()·&\-]{2,80}?(?:股份有限公司|集团有限公司|有限责任公司|有限公司|集团|公司|工厂|厂|中心|事务所|研究院|医院|学校|银行|协会|商会|合作社))"
)
_SEP_RE = re.compile(r"\s*(?:[-_|｜—–·]| - | _ | \| )\s*")


@dataclass(frozen=True)
class CompanyDiscoveryCandidate:
    name: str
    website: str = ""
    industry: str = ""
    region: str = ""
    employee_size: str = ""
    source: str = "public_company_site"
    source_url: str = ""
    confidence: str = "medium"
    evidence_title: str = ""
    evidence_snippet: str = ""
    query: str = ""
    email: str = ""
    phone: str = ""
    contact_form_url: str = ""
    contact_source: str = ""
    contact_source_url: str = ""
    consent_status: str = "public"
    contacts: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CompanyDiscoveryResult:
    candidates: list[CompanyDiscoveryCandidate] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    search_available: bool = True
    missing_config: list[str] = field(default_factory=list)
    status: str = "ok"


def normalize_domain(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(":", 1)[0]


def _clean_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}{parsed.path or '/'}"


def _is_blocked_domain(domain: str) -> bool:
    return any(hint in domain for hint in _BLOCKED_DOMAINS)


def _classify_source(url: str) -> str:
    domain = normalize_domain(url)
    if not domain or _is_blocked_domain(domain):
        return ""
    if any(hint in domain for hint in _REGISTRY_HINTS):
        return "public_registry"
    if any(hint in domain for hint in _BUSINESS_DATA_HINTS):
        return "official_directory"
    if any(hint in domain for hint in _DIRECTORY_HINTS):
        return "official_directory"
    return "public_company_site"


def _clean_company_name(title: str) -> str:
    text = re.sub(r"<[^>]+>", " ", title or "")
    text = re.sub(r"\s+", " ", text).strip(" -_|｜—–·\t\r\n")
    if not text:
        return ""
    match = _COMPANY_RE.search(text)
    if match:
        return match.group(1).strip(" -_|｜—–·")[:255]
    first = _SEP_RE.split(text, maxsplit=1)[0].strip()
    first = re.sub(r"(?:官方网站|官网首页|官网|首页|公司简介|企业介绍)$", "", first).strip()
    if 2 <= len(first) <= 60 and not any(w in first for w in _JUNK_TITLE_WORDS):
        return first[:255]
    return ""


def _profile_lists(profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    industries = [str(x).strip() for x in (profile.get("industries") or []) if str(x).strip()]
    regions = [str(x).strip() for x in (profile.get("regions") or []) if str(x).strip()]
    industries = [x for x in industries if "待补充" not in x]
    regions = [x for x in regions if "待补充" not in x] or ["全国"]
    return industries[:5], regions[:5]


_UNLIMITED_INDUSTRY_TERMS = {"不限行业", "不限制行业", "行业不限", "全行业", "不限"}


def _industry_search_terms(industry: str) -> list[str]:
    """Convert the profile industry into company discovery search terms; "any industry" isn't searched literally."""
    text = (industry or "").strip()
    if not text or text in _UNLIMITED_INDUSTRY_TERMS:
        return []
    if text in {"工业制造", "制造业", "制造", "工业"}:
        return ["机械制造", "汽车零部件", "电子元器件", "自动化设备", "工业电气", "精密加工"]
    return [text]


def build_discovery_queries(profile: dict[str, Any], limit: int) -> list[str]:
    industries, regions = _profile_lists(profile)
    queries: list[str] = []
    service_terms = [str(x).strip() for x in (profile.get("service_scope") or []) if str(x).strip()]
    for region in regions:
        for industry in industries:
            base_region = "" if region == "全国" else region
            terms = _industry_search_terms(industry)
            # 「不限行业」 ("any industry") isn't used as an industry filter, but the public search still needs keywords;
            # otherwise discovery returns empty right away with queries=[]. Here our own service direction is used as the fallback search term.
            if not terms:
                terms = service_terms[:3]
            for term in terms:
                templates = [
                    f"{base_region} {term} 企业 官网",
                    f"{base_region} {term} 公司 官网",
                    f"{base_region} {term} 厂家 官网",
                ]
                for q in templates:
                    q = re.sub(r"\s+", " ", q).strip()
                    if q and q not in queries:
                        queries.append(q)
    if not queries:
        for term in service_terms[:3]:
            for q in (f"{term} 企业 官网", f"{term} 公司 官网"):
                q = re.sub(r"\s+", " ", q).strip()
                if q and q not in queries:
                    queries.append(q)
    try:
        max_queries = int(os.environ.get("SALES_AGENT_DISCOVERY_MAX_QUERIES", str(_DEFAULT_MAX_QUERIES)) or _DEFAULT_MAX_QUERIES)
    except Exception:  # noqa: BLE001
        max_queries = _DEFAULT_MAX_QUERIES
    if limit > 60:
        max_queries = max(max_queries, 10)
    return queries[:max(1, max_queries)]


def _search_count(limit: int) -> int:
    raw = os.environ.get("SALES_AGENT_DISCOVERY_SEARCH_CNT") or os.environ.get("TENCENT_SEARCH_CNT") or "10"
    try:
        cnt = int(raw)
    except Exception:  # noqa: BLE001
        cnt = 10
    if limit >= 80 and cnt < 20:
        cnt = 20
    return cnt if cnt in _ALLOWED_SEARCH_CNT else 10


def search_capability() -> dict[str, Any]:
    missing: list[str] = []
    if not os.environ.get("TENCENT_SEARCH_API_KEY", "").strip():
        missing.append("TENCENT_SEARCH_API_KEY")
    return {
        "search_available": not missing,
        "missing_config": missing,
        "status": "ok" if not missing else "search_unavailable",
    }


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except Exception:  # noqa: BLE001
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:  # noqa: BLE001
        return default


def _contact_enrich_limit(limit: int) -> int:
    raw = os.environ.get("SALES_AGENT_DISCOVERY_CONTACT_LIMIT")
    if raw is not None:
        return max(0, min(limit, _int_env("SALES_AGENT_DISCOVERY_CONTACT_LIMIT", 0)))
    return max(0, limit)


def _search_tencent_searchpro(query: str, cnt: int, remaining_budget: float | None = None) -> tuple[list[dict[str, Any]], str]:
    key = os.environ.get("TENCENT_SEARCH_API_KEY", "").strip()
    request_id = new_model_call_id()
    if not key:
        write_sales_agent_log({
            "request_id": request_id,
            "event": "searchpro_call",
            "status": "failed",
            "scene": "company_discovery",
            "provider": "tencent_searchpro",
            "query": query,
            "cnt": cnt,
            "error": "TENCENT_SEARCH_API_KEY 未配置",
        })
        return [], "TENCENT_SEARCH_API_KEY 未配置,无法执行公网企业搜索"
    timeout = _float_env("SALES_AGENT_DISCOVERY_TIMEOUT", 10.0)
    if remaining_budget is not None:
        timeout = min(timeout, max(0.1, remaining_budget))
    started_at = now_monotonic()
    write_sales_agent_log({
        "request_id": request_id,
        "event": "searchpro_call",
        "status": "started",
        "scene": "company_discovery",
        "provider": "tencent_searchpro",
        "query": query,
        "cnt": cnt,
        "timeout": timeout,
    })
    try:
        resp = requests.post(
            SEARCHPRO_URL,
            json={"Query": query, "Cnt": cnt},
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("Response", {})
        if "Error" in data:
            msg = f"SearchPro 错误:{data['Error'].get('Message', 'unknown')}"
            write_sales_agent_log({
                "request_id": request_id,
                "event": "searchpro_call",
                "status": "failed",
                "scene": "company_discovery",
                "provider": "tencent_searchpro",
                "query": query,
                "cnt": cnt,
                "duration_ms": duration_ms(started_at),
                "error": msg,
            })
            return [], msg
        pages = []
        for raw in data.get("Pages", []) or []:
            try:
                page = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            if isinstance(page, dict):
                pages.append(page)
        write_sales_agent_log({
            "request_id": request_id,
            "event": "searchpro_call",
            "status": "ok",
            "scene": "company_discovery",
            "provider": "tencent_searchpro",
            "query": query,
            "cnt": cnt,
            "duration_ms": duration_ms(started_at),
            "pages_count": len(pages),
        })
        return pages, ""
    except Exception as e:  # noqa: BLE001
        msg = f"SearchPro 请求失败:{e}"
        write_sales_agent_log({
            "request_id": request_id,
            "event": "searchpro_call",
            "status": "failed",
            "scene": "company_discovery",
            "provider": "tencent_searchpro",
            "query": query,
            "cnt": cnt,
            "duration_ms": duration_ms(started_at),
            "error": msg,
        })
        return [], msg


def _extract_candidate_from_result(page: dict[str, Any], profile: dict[str, Any], query: str) -> CompanyDiscoveryCandidate | None:
    url = _clean_url(str(page.get("url") or page.get("Url") or ""))
    source = _classify_source(url)
    if not source:
        return None
    title = str(page.get("title") or page.get("Title") or "").strip()
    snippet = str(page.get("passage") or page.get("snippet") or page.get("Passage") or "").strip()
    if any(word in title or word in snippet for word in _JUNK_TITLE_WORDS):
        return None
    name = _clean_company_name(title) or _clean_company_name(snippet)
    if not name:
        return None
    industries, regions = _profile_lists(profile)
    region = "" if regions == ["全国"] else regions[0]
    website = url if source == "public_company_site" else ""
    confidence = "high" if source == "public_company_site" else "medium"
    return CompanyDiscoveryCandidate(
        name=name,
        website=website,
        industry=industries[0] if industries else "",
        region=region,
        source=source,
        source_url=url,
        confidence=confidence,
        evidence_title=title[:255],
        evidence_snippet=snippet[:500],
        query=query,
    )


def _enrich_candidate_contacts(candidate: CompanyDiscoveryCandidate) -> CompanyDiscoveryCandidate:
    contact_target = candidate.website or candidate.source_url
    if candidate.source not in {"public_company_site", "official_directory"} or not contact_target:
        return candidate
    try:
        contacts = discover_public_contacts(contact_target, candidate.source_url, source=candidate.source)
    except Exception:  # noqa: BLE001
        return candidate
    if not contacts:
        return candidate
    contact_dicts = [c.as_dict() for c in contacts]
    best = contact_dicts[0]
    return replace(
        candidate,
        email=str(best.get("email") or ""),
        phone=str(best.get("phone") or ""),
        contact_form_url=str(best.get("contact_form_url") or ""),
        contact_source=str(best.get("contact_source") or "public_company_site"),
        contact_source_url=str(best.get("contact_source_url") or candidate.source_url),
        confidence=str(best.get("confidence") or candidate.confidence),
        consent_status=str(best.get("consent_status") or "public"),
        contacts=contact_dicts,
    )


def _candidate_key(c: CompanyDiscoveryCandidate) -> str:
    domain = normalize_domain(c.website)
    if c.website and domain:
        return f"domain:{domain}"
    return f"name:{c.name.casefold()}|{c.region.casefold()}"


def _source_priority(source: str) -> int:
    return {
        "public_company_site": 4,
        "public_registry": 3,
        "official_directory": 2,
        "authorized_partner": 2,
        "manual": 1,
        "user_provided": 1,
    }.get(source, 0)


def _dedupe_candidates(candidates: list[CompanyDiscoveryCandidate]) -> list[CompanyDiscoveryCandidate]:
    chosen: dict[str, CompanyDiscoveryCandidate] = {}
    for c in candidates:
        key = _candidate_key(c)
        old = chosen.get(key)
        if old is None or _source_priority(c.source) > _source_priority(old.source):
            chosen[key] = c
    return list(chosen.values())


def discover_public_companies(profile: dict[str, Any], limit: int) -> CompanyDiscoveryResult:
    try:
        raw_limit = int(limit or MAX_RECOMMENDATIONS)
    except Exception:  # noqa: BLE001
        raw_limit = MAX_RECOMMENDATIONS
    n = max(1, min(MAX_RECOMMENDATIONS, raw_limit))
    queries = build_discovery_queries(profile, n)
    deadline = time.monotonic() + _float_env("SALES_AGENT_DISCOVERY_TOTAL_TIMEOUT", _DEFAULT_DISCOVERY_TIMEOUT_SECONDS)
    capability = search_capability()
    cnt = _search_count(n)
    found: list[CompanyDiscoveryCandidate] = []
    errors: list[str] = []
    if not capability["search_available"]:
        errors.append("TENCENT_SEARCH_API_KEY 未配置,无法执行公网企业搜索")
        return CompanyDiscoveryResult(
            candidates=[],
            queries=queries,
            errors=errors,
            search_available=False,
            missing_config=capability["missing_config"],
            status="search_unavailable",
        )

    for query in queries:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("企业搜索超过时间预算,已返回当前找到的真实企业")
            break
        pages, error = _search_tencent_searchpro(query, cnt, remaining_budget=remaining)
        if error:
            if error not in errors:
                errors.append(error)
            if "TENCENT_SEARCH_API_KEY" in error:
                break
            continue
        for page in pages:
            candidate = _extract_candidate_from_result(page, profile, query)
            if candidate is not None:
                found.append(candidate)
        found = _dedupe_candidates(found)
        if len(found) >= n:
            break

    enriched = []
    contact_limit = _contact_enrich_limit(min(n, len(found)))
    for i, candidate in enumerate(found[:n]):
        if i < contact_limit and time.monotonic() < deadline:
            enriched.append(_enrich_candidate_contacts(candidate))
        else:
            enriched.append(candidate)
    status = "ok" if len(enriched) >= n else ("partial" if enriched else ("no_results" if not errors else "search_error"))
    return CompanyDiscoveryResult(
        candidates=enriched,
        queries=queries,
        errors=errors,
        search_available=True,
        missing_config=[],
        status=status,
    )
