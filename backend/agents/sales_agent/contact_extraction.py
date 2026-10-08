"""Sales Agent extraction of public company contact info.

Only extracts company-level public contact info from company websites / public contact pages / public directory pages. No login, no circumvention, no private
contact info; if unsure, return nothing.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import requests

_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})(?![\w.+-])")
_MAILTO_RE = re.compile(r"(?is)href=[\"']mailto:([^\"'?#]+)")
_TEL_RE = re.compile(r"(?is)href=[\"']tel:([^\"']+)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?86[-\s]?)?(?:400[-\s]?\d{3}[-\s]?\d{4}|800[-\s]?\d{3}[-\s]?\d{4}|0\d{2,3}[-\s]?\d{7,8}|1[3-9]\d{9})(?!\d)"
)
_HREF_RE = re.compile(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>")
_FORM_RE = re.compile(r"(?is)<form\b")
_TAG_RE = re.compile(r"<[^>]+>")
_PERSONAL_EMAIL_PREFIX_RE = re.compile(r"(?i)^[a-z]+[._-][a-z]+$")

_CONTACT_HINTS = (
    "contact", "contact-us", "contacts", "about", "about-us", "support", "service",
    "inquiry", "enquiry", "message", "feedback", "sales", "connect",
    "联系我们", "关于我们", "联系方式", "在线留言", "商务咨询", "客户服务", "客服", "支持", "咨询",
)
_FORM_HINTS = (
    "contact", "contact-us", "inquiry", "enquiry", "message", "feedback", "quote", "request",
    "联系我们", "在线留言", "商务咨询", "销售咨询", "业务咨询", "提交需求", "获取报价",
)
_POSITIVE_CONTACT_WORDS = (
    "邮箱", "邮件", "email", "e-mail", "mail", "电话", "联系电话", "服务热线",
    "客服热线", "客户服务", "总机", "商务咨询", "销售咨询", "业务咨询", "联系我们",
    "联系方式", "contact", "support", "service", "sales", "hotline",
)
_NEGATIVE_CONTACT_WORDS = (
    "招聘", "职位", "简历", "投递", "人事", "个人", "私人", "微信", "qq",
    "career", "careers", "jobs", "recruit", "recruitment", "resume", "human resources",
    "linkedin", "脉脉", "猎聘", "boss直聘", "智联", "前程无忧", "拉勾", "看准",
)
_ROLE_EMAIL_PREFIXES = {
    "info", "sales", "contact", "hello", "support", "service", "marketing", "biz", "bd",
    "office", "admin", "pr", "media", "market", "business", "customer", "help", "mail",
}
_BLOCKED_EMAIL_PREFIXES = {"hr", "jobs", "job", "career", "careers", "resume", "recruit", "zhaopin"}
_BLOCKED_DOMAINS = (
    "qichacha.com", "tianyancha.com", "aiqicha.baidu.com", "qixin.com", "tanzhi",
    "linkedin.com", "maimai.cn", "liepin.com", "zhipin.com", "51job.com", "zhaopin.com",
    "lagou.com", "kanzhun.com",
)


@dataclass(frozen=True)
class PublicContactCandidate:
    email: str = ""
    phone: str = ""
    contact_form_url: str = ""
    source: str = "public_company_site"
    source_url: str = ""
    confidence: str = "medium"
    consent_status: str = "public"

    def as_dict(self) -> dict[str, str]:
        return {
            "email": self.email,
            "phone": self.phone,
            "contact_form_url": self.contact_form_url,
            "contact_source": self.source,
            "contact_source_url": self.source_url,
            "confidence": self.confidence,
            "consent_status": self.consent_status,
        }


def _domain(url: str) -> str:
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


def _same_domain(base: str, other: str) -> bool:
    base_domain = _domain(base)
    other_domain = _domain(other)
    return bool(base_domain and other_domain and (other_domain == base_domain or other_domain.endswith("." + base_domain)))


def _is_blocked_url(url: str) -> bool:
    d = _domain(url)
    return not d or any(blocked in d for blocked in _BLOCKED_DOMAINS)


def _clean_url(url: str, base: str = "") -> str:
    raw = html.unescape((url or "").strip())
    if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    if base:
        raw = urljoin(base, raw)
    if raw.startswith("//"):
        raw = "https:" + raw
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}{parsed.path or '/'}"


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", raw or "")
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _deobfuscate(text: str) -> str:
    out = html.unescape(text or "")
    out = re.sub(r"(?i)\s*(?:\[|\(|（|【)\s*at\s*(?:\]|\)|）|】)\s*", "@", out)
    out = re.sub(r"(?i)\s+at\s+", "@", out)
    out = re.sub(r"(?i)\s*(?:\[|\(|（|【)\s*dot\s*(?:\]|\)|）|】)\s*", ".", out)
    out = re.sub(r"(?i)\s+dot\s+", ".", out)
    return out


def _context(text: str, start: int, end: int, radius: int = 80) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)].lower()


def _has_any(text: str, words: tuple[str, ...] | set[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _has_negative_contact_context(text: str) -> bool:
    return _has_any(text, _NEGATIVE_CONTACT_WORDS) or bool(re.search(r"(?i)(?<![a-z])hr(?![a-z])", text or ""))


def _normalize_email(value: str) -> str:
    email_value = unquote(html.unescape(value or "")).strip().strip("<>,;:，；。()（）[]【】")
    return email_value.lower()


def _email_allowed(email_value: str, ctx: str, from_mailto: bool) -> bool:
    if not email_value or "@" not in email_value or _has_negative_contact_context(ctx):
        return False
    prefix = email_value.split("@", 1)[0].split("+", 1)[0].lower()
    if prefix in _BLOCKED_EMAIL_PREFIXES:
        return False
    if prefix in _ROLE_EMAIL_PREFIXES:
        return True
    if from_mailto or _PERSONAL_EMAIL_PREFIX_RE.fullmatch(prefix):
        return False
    return _has_any(ctx, _POSITIVE_CONTACT_WORDS)


def _extract_email(text: str, raw_html: str) -> str:
    candidates: list[tuple[int, str]] = []
    for raw in _MAILTO_RE.findall(raw_html or ""):
        email_value = _normalize_email(raw)
        if _EMAIL_RE.fullmatch(email_value) and _email_allowed(email_value, "mailto", True):
            candidates.append((3, email_value))
    plain = _deobfuscate(text)
    for match in _EMAIL_RE.finditer(plain):
        email_value = _normalize_email(match.group(1))
        ctx = _context(plain, match.start(), match.end())
        if _email_allowed(email_value, ctx, False):
            prefix = email_value.split("@", 1)[0].split("+", 1)[0].lower()
            score = 2 if prefix in _ROLE_EMAIL_PREFIXES else 1
            candidates.append((score, email_value))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
    return candidates[0][1]


def _normalize_phone(value: str) -> str:
    phone = html.unescape(value or "").strip().strip(".,;，；。")
    phone = re.sub(r"\s+", "", phone)
    phone = phone.replace("（", "(").replace("）", ")")
    return phone[:64]


def _phone_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("86") and len(digits) > 11:
        digits = digits[2:]
    return digits


def _is_mobile(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\+?86)?1[3-9]\d{9}", re.sub(r"[-\s]", "", value or "")))


def _phone_allowed(phone: str, ctx: str, from_tel: bool) -> bool:
    if not phone or _has_negative_contact_context(ctx):
        return False
    digits = _phone_digits(phone)
    if len(digits) < 7:
        return False
    if digits.startswith(("400", "800")):
        return True
    if from_tel and not _is_mobile(phone):
        return True
    if _is_mobile(phone):
        return _has_any(ctx, _POSITIVE_CONTACT_WORDS)
    return from_tel or _has_any(ctx, _POSITIVE_CONTACT_WORDS)


def _extract_phone(text: str, raw_html: str) -> str:
    candidates: list[tuple[int, str]] = []
    for raw in _TEL_RE.findall(raw_html or ""):
        phone = _normalize_phone(unquote(raw))
        if _phone_allowed(phone, "tel", True):
            candidates.append((3, phone))
    plain = html.unescape(text or "")
    for match in _PHONE_RE.finditer(plain):
        phone = _normalize_phone(match.group(0))
        ctx = _context(plain, match.start(), match.end())
        if _phone_allowed(phone, ctx, False):
            score = 2 if _phone_digits(phone).startswith(("400", "800")) else 1
            candidates.append((score, phone))
    if not candidates:
        return ""
    seen: set[str] = set()
    unique: list[tuple[int, str]] = []
    for score, phone in candidates:
        key = _phone_digits(phone)
        if key and key not in seen:
            seen.add(key)
            unique.append((score, phone))
    unique.sort(key=lambda x: (-x[0], x[1]))
    return unique[0][1]


def _fetch_public_page(url: str) -> tuple[str, str]:
    clean = _clean_url(url)
    if not clean or _is_blocked_url(clean):
        return "", ""
    timeout = float(os.environ.get("SALES_AGENT_CONTACT_TIMEOUT", "10") or 10)
    max_bytes = int(os.environ.get("SALES_AGENT_CONTACT_MAX_BYTES", "700000") or 700000)
    try:
        resp = requests.get(
            clean,
            headers={"User-Agent": "Mozilla/5.0 (compatible; VigilathSalesAgent/1.0)"},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return "", ""
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and not any(t in ctype for t in ("html", "text", "xml")):
            return "", ""
        content = resp.content[:max_bytes]
        resp.encoding = resp.encoding or resp.apparent_encoding
        return content.decode(resp.encoding or "utf-8", errors="ignore"), resp.url
    except Exception:  # noqa: BLE001
        return "", ""


def _looks_contact_page(url: str, label: str = "") -> bool:
    text = f"{url} {label}".lower()
    return _has_any(text, _CONTACT_HINTS)


def _candidate_contact_urls(website: str, home_html: str, final_home_url: str) -> list[str]:
    root = _clean_url(final_home_url or website)
    if not root:
        return []
    parsed = urlparse(root)
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls = [root]
    for href, label_html in _HREF_RE.findall(home_html or ""):
        label = _strip_html(label_html)
        url = _clean_url(href, root)
        if url and _same_domain(root, url) and _looks_contact_page(url, label):
            urls.append(url)
    for path in ("/contact", "/contact-us", "/about", "/about-us", "/support", "/service"):
        urls.append(base + path)
    deduped: list[str] = []
    seen: set[str] = set()
    max_pages = int(os.environ.get("SALES_AGENT_CONTACT_MAX_PAGES", "4") or 4)
    for url in urls:
        key = url.rstrip("/")
        if key not in seen and not _is_blocked_url(url):
            seen.add(key)
            deduped.append(url)
        if len(deduped) >= max(1, max_pages):
            break
    return deduped


def _extract_contact_form_url(url: str, raw_html: str, text: str) -> str:
    if _FORM_RE.search(raw_html or "") and _has_any(f"{url} {text[:500]}", _FORM_HINTS):
        return url
    for href, label_html in _HREF_RE.findall(raw_html or ""):
        label = _strip_html(label_html)
        candidate = _clean_url(href, url)
        if candidate and _same_domain(url, candidate) and _has_any(f"{candidate} {label}", _FORM_HINTS):
            return candidate
    return ""


def _confidence(url: str, email_value: str, phone: str, form_url: str) -> str:
    if _looks_contact_page(url) and (email_value or phone):
        return "high"
    if form_url or email_value or phone:
        return "medium"
    return "low"


def discover_public_contacts(website: str, source_url: str = "", source: str = "public_company_site") -> list[PublicContactCandidate]:
    if not website or _is_blocked_url(website):
        return []
    home_html, final_home_url = _fetch_public_page(website)
    if not home_html:
        return []
    contacts: list[PublicContactCandidate] = []
    for url in _candidate_contact_urls(website, home_html, final_home_url or source_url or website):
        raw_html, final_url = (home_html, final_home_url) if url.rstrip("/") == (final_home_url or website).rstrip("/") else _fetch_public_page(url)
        page_url = final_url or url
        if not raw_html or not _same_domain(website, page_url):
            continue
        text = _strip_html(raw_html)
        if _has_negative_contact_context(text[:1200]) and not _has_any(text[:1200], _POSITIVE_CONTACT_WORDS):
            continue
        email_value = _extract_email(text, raw_html)
        phone = _extract_phone(text, raw_html)
        form_url = _extract_contact_form_url(page_url, raw_html, text)
        if not any((email_value, phone, form_url)):
            continue
        contacts.append(PublicContactCandidate(
            email=email_value,
            phone=phone,
            contact_form_url=form_url,
            source=source if source in {"public_company_site", "official_directory"} else "public_company_site",
            source_url=page_url,
            confidence=_confidence(page_url, email_value, phone, form_url),
            consent_status="public",
        ))
        if email_value and phone:
            break
    contacts.sort(key=lambda c: ({"high": 3, "medium": 2, "low": 1}.get(c.confidence, 0), bool(c.email), bool(c.phone)), reverse=True)
    return contacts[:3]
