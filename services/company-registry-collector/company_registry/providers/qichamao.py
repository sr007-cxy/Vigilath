"""Qichamao (qichamao.com) provider.

The login session Cookie is read from the `QICHAMAO_COOKIE` environment variable (the full Cookie header, key items
qz.newsite / qznewsite.uid; qz.newsite expires). All regexes were verified against real pages.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from typing import List, Optional

import requests

from ..config import Settings
from ..htmlutil import EMAIL_RE, clean_text
from ..models import Company
from .base import ListResult, Provider

BASE_URL = "https://www.qichamao.com"

# Noise addresses that appear site-wide on Qichamao pages and are not real company emails
NOISE_EMAILS = {"18195580@qq.com", "icadence@aliyun.com", "admin@hzsplt.com"}

_LIST_ITEM_RE = re.compile(
    r'<a class="listsec_tit" href="/orgcompany/searchitemdtl/([0-9a-f]+)\.html"[^>]*>'
    r"(.*?)</a>(.*?)(?=<a class=\"listsec_tit\"|$)",
    re.S,
)


class QichamaoProvider(Provider):
    name = "qichamao"
    # The four capital bands as measured (regm values)
    capital_bands = ["*-100.0", "100.0-500.0", "500.0-1000.0", "1000.0-*"]
    # Filter by 「有限责任公司」 ("limited liability company") to exclude non-target entities such as government finance/tax offices and schools
    default_company_type = "有限责任公司"
    max_page = 5

    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or Settings()
        self.cookie = os.environ.get("QICHAMAO_COOKIE", "").strip()
        self._sess = requests.Session()
        self._sess.headers.update({
            "User-Agent": self.s.user_agent,
            "Referer": BASE_URL + "/",
        })
        if self.cookie:
            self._sess.headers["Cookie"] = self.cookie
        self._last = 0.0

    # ---- Infrastructure ----
    def ready(self) -> bool:
        return bool(self.cookie)

    def _throttle(self):
        wait = self.s.request_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _get(self, path: str, referer: Optional[str] = None, retries: int = 2) -> str:
        url = BASE_URL + path
        headers = {"Referer": referer} if referer else {}
        err: Optional[Exception] = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                r = self._sess.get(url, headers=headers, timeout=self.s.request_timeout)
                if r.status_code == 200:
                    return r.text
                err = RuntimeError(f"HTTP {r.status_code} {url}")
            except requests.RequestException as e:
                err = e
            time.sleep(1.0 * (attempt + 1))
        raise err or RuntimeError(f"failed {url}")

    @staticmethod
    def _logged_in(raw: str) -> bool:
        # The list page's top nav template contains both "登录/免费注册" ("Log in/Sign up free") and "退出登录" ("Log out"), so use a positive marker:
        # "退出登录" ("Log out") or the username token (qycxb_) means a valid login session; guest pages have neither.
        return "退出登录" in raw or "qycxb_" in raw

    # ---- Provider interface ----
    def list_companies(self, word: str, city: str, band: str,
                       company_type: str, page: int) -> ListResult:
        keyword = f"{city}{word}" if city else word
        params = {"o": page}
        if company_type:
            params["companytype"] = company_type
        if band:
            params["regm"] = band
        qs = urllib.parse.urlencode(params)
        kw = urllib.parse.quote(keyword)
        raw = self._get(f"/search/qy/{kw}?{qs}")
        if not self._logged_in(raw):
            return ListResult(companies=[], ok=False)

        out: List[Company] = []
        for m in _LIST_ITEM_RE.finditer(raw):
            hid = m.group(1)
            name = re.sub(r"\s+", "", clean_text(m.group(2)))
            blk = clean_text(m.group(3))[:600]

            def f(*labels: str) -> str:
                for lab in labels:
                    mm = re.search(lab + r"[：:]\s*([^ ]{1,30})", blk)
                    if mm:
                        return mm.group(1).strip()
                return ""

            out.append(Company(
                id=hid, source=self.name, name=name, city=city, band=band,
                legal_rep=f("法人代表", "法定代表人"),
                capital=f("注册资本"), establish=f("成立日期"),
                status="存续" if "存续" in blk else f("状态"),
                detail_url=f"{BASE_URL}/orgcompany/searchitemdtl/{hid}.html",
            ))
        return ListResult(companies=out, ok=True)

    def enrich(self, company: Company) -> bool:
        ref = BASE_URL + f"/orgcompany/searchitemdtl/{company.id}.html"
        raw = self._get(f"/orgcompany/boss/{company.id}.html", referer=ref)
        ok = self._logged_in(raw)
        text = clean_text(raw)

        tm = re.search(r"<title>([^<_-]+)", raw)
        if tm:
            # boss page titles look like 「XX公司老板-XX公司法定代表人-…」 ("XX Co. boss - XX Co. legal representative - ..."); strip the trailing role words
            full = re.sub(r"(老板|法定代表人|股东|高管|是谁.*)$", "", tm.group(1).strip()).strip()
            if len(full) > len(company.name):
                company.name = full

        pm = re.search(r"企业电话[：:]\s*(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})", text)
        company.phone = pm.group(1) if pm else ""

        emails: List[str] = []
        em = re.search(r"企业邮箱[：:]\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
        if em and em.group(1) not in NOISE_EMAILS:
            emails.append(em.group(1))
        for e in EMAIL_RE.findall(raw):
            if e not in emails and e not in NOISE_EMAILS and not e.lower().endswith(".png"):
                emails.append(e)
        company.emails = emails[:4]

        am = re.search(r"企业地址[：:]\s*([^ ].{4,80}?)(?:发送名片|下载|数据更新|$)", text)
        company.address = am.group(1).strip() if am else ""
        im = re.search(r"所属行业[：:]\s*([^ ]{2,20})", text)
        company.industry = im.group(1) if im else ""
        return ok
