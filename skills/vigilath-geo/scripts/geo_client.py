#!/usr/bin/env python3
"""Vigilath GEO agent thin client - lets the host agent (OpenClaw) connect to the Vigilath GEO agent.

Zero third-party dependencies (pure standard library), drop-in ready. Auth via environment variables; the token is never hard-coded/printed.

Usage:
  python geo_client.py check https://example.com               # no login! GEO+SEO dual health check
  python geo_client.py check https://example.com --no-seo      # GEO only
  python geo_client.py check https://example.com --seo-only    # SEO only
  python geo_client.py mention <your-brand> 商用清洁机器人             # no login! ask AI about the brand, paste back verbatim ("commercial cleaning robot")
  python geo_client.py wallet                                  # check balance (look before spending)
  python geo_client.py plans                                   # see subscription tiers for continuous monitoring
  python geo_client.py subscribe watch30                       # start a subscription: via WeChat Pay
  python geo_client.py topup 100                               # top up: returns how to pay (including the web QR code URL)
  python geo_client.py topup 100 --tx 0x…                      # after paying on-chain, settle with the transaction hash
  python geo_client.py advanced aeo https://example.com        # no login! AEO audit (zero cost)
  python geo_client.py advanced entity <brand>                 # no login! entity recognition audit
  python geo_client.py industry                                # no login! list of industries with datasets
  python geo_client.py industry 商用清洁机器人                  # no login! who is TOP1 in AI for this industry ("commercial cleaning robot")
  python geo_client.py chat "How did today's campaign perform?"
  python geo_client.py data today          # today / coverage / report ...
  python geo_client.py capabilities        # what the current token can do
  python geo_client.py login               # re-authorize (device code; when the token expires/switching accounts)

Credential sources (priority: environment variables > ~/.vigilath/config):
  VIGILATH_AGENT_TOKEN   1-year account token (issued by Vigilath), required
  VIGILATH_BASE          optional, default https://vigilath.cn/api/agent/v1
The one-line installer (install.sh) writes the token into ~/.vigilath/config, so it works right after install without setting environment variables.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def _load_config() -> dict:
    """Read ~/.vigilath/config (KEY=VALUE lines), for installs that do not set environment variables."""
    cfg: dict[str, str] = {}
    path = os.path.expanduser("~/.vigilath/config")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return cfg


_CFG = _load_config()


def _conf(key: str, default: str = "") -> str:
    return (os.environ.get(key) or _CFG.get(key) or default).strip()


BASE = _conf("VIGILATH_BASE", "https://vigilath.cn/api/agent/v1").rstrip("/")
# The anonymous audit endpoint is under the site API root (/api/check/anonymous), not in /agent/v1; derived from BASE
SITE_API = BASE[: -len("/agent/v1")] if BASE.endswith("/agent/v1") else BASE


def _die(msg: str, code: int = 1) -> "None":
    print(f"[vigilath-geo] 错误:{msg}", file=sys.stderr)
    sys.exit(code)


def _normalize_url(raw: str) -> str:
    """Turn whatever the host agent passes into a URL that can be requested.

    Why: when the host extracts a URL from the user's message, it often includes quotes, Markdown wrapping,
    trailing punctuation or Chinese full-width symbols, and often omits the scheme. This used to POST it without changing a character,
    so "audit example.com" turned into a request against `example.com` - the server did not see the site the user
    meant. **Only strip wrappers and add the scheme; never replace the domain, never guess**:
    when unsure, erroring so the user can clarify is better than silently auditing the wrong site.
    """
    s = (raw or "").strip()
    # Markdown link [text](http://…): take only what is inside the parentheses
    m = re.search(r"\]\((https?://[^)\s]+)\)", s)
    if m:
        s = m.group(1)
    # Leading/trailing quotes / angle brackets / Chinese quotes / backticks, and trailing punctuation
    s = s.strip("\"'`<>《》「」『』“”‘’ \t\r\n")
    s = s.rstrip("。,、;:!?.,;:!?")
    if not s:
        _die("网址是空的。问用户要一个完整网址,不要自己猜。")
    # First decide "is a scheme written". Note we cannot only look for `://` - `javascript:alert(1)` has no
    # double slash, so checking only `://` would turn it into `https://javascript:alert(1)`, laundering something
    # that should be rejected into a valid URL.
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", s)
    if m:
        if m.group(1).lower() not in ("http", "https"):
            _die(f"只支持 http/https 的网址,收到:{raw!r}")
        if "://" not in s:
            _die(f"网址格式不对(scheme 后面缺 //),收到:{raw!r}")
    else:
        # Only add the scheme. **Do not touch the hostname** - "helpful" rewrites like adding www. or switching to .com
        # are exactly where "audited a different site" came from.
        s = "https://" + s.lstrip("/")
    return s


def _headers() -> dict:
    token = _conf("VIGILATH_AGENT_TOKEN")
    if not token:
        _die("缺少 token —— 跑一行安装,或设环境变量 VIGILATH_AGENT_TOKEN / 写进 ~/.vigilath/config。")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _server_message(body: str) -> str:
    """Dig the human-readable message the backend wrote out of the response body.

    The backend's uniform shape is `{"error": {"code": ..., "message": ...}}` (FastAPI's
    HTTPException is `{"detail": ...}`, where detail is sometimes a dict itself).
    The backend's message is always more accurate than the client fallback - e.g. the anonymous quota one distinguishes "this device is used up"
    from "the current network egress is used up, maybe a colleague used it first"; dumping it as JSON loses all that.
    """
    try:
        d = json.loads(body)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(d, dict):
        return ""
    for node in (d.get("error"), d.get("detail"), d):
        if isinstance(node, dict) and isinstance(node.get("message"), str):
            return node["message"].strip()
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _explain_http(e: urllib.error.HTTPError, *, body: str = None) -> str:
    if body is None:
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            body = ""
    hint = {
        401: "token 无效 / 过期 / 已禁用。自己重新授权即可:`login`(不用重装,已装好的技能不受影响)。",
        402: "余额不足 —— 这是付费能力。用 `wallet` 看余额、`topup <金额>` 充值;响应里通常带了还需要多少与充值方式,如实转达,别重试刷接口。",
        403: "无权限(能力未授权,或 Origin 不在白名单)。",
        429: "账号配额 / 限速,稍后退避重试。",
    }.get(e.code, "")
    # If the backend said something, use it; do not bury it as JSON behind a generic message
    msg = _server_message(body)
    if msg:
        return f"HTTP {e.code} {msg}".strip()
    return f"HTTP {e.code} {hint} {body}".strip()


def _request(req: urllib.request.Request, timeout: int, passthrough: tuple = ()):
    """Status codes in `passthrough` are raised to the caller as-is; everything else goes through `_die` with a human message.

    An allowlist rather than "always raise": the other 15 call sites rely on `_request`'s internal `_die`
    for human messages; always raising would mean every call site has to write a try.
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        # ★ Order matters: `_explain_http` calls e.read() on its first line, and an HTTPError body is a
        #   one-shot stream - after it is read, an outer read only gets an empty string, missing accepts / payee
        #   address / order id. So the passthrough check must come before it.
        if e.code in passthrough:
            raise
        _die(_explain_http(e))
    except urllib.error.URLError as e:
        # A read timeout and "cannot connect" are different: with the former the server is most likely still running; calling it "cannot connect" misleads users into checking their network
        import socket as _socket

        if isinstance(getattr(e, "reason", None), (TimeoutError, _socket.timeout)):
            _die(f"等了 {timeout}s 还没返回 —— 服务端可能还在跑。稍等一会儿再问一次,别连着重试。")
        _die(f"连不上 Vigilath({BASE}):{e.reason}")
    except TimeoutError:
        _die(f"等了 {timeout}s 还没返回 —— 服务端可能还在跑。稍等一会儿再问一次,别连着重试。")


def chat(message: str) -> dict:
    """Chat: stream deltas + cards, returning {'text': ..., 'cards': [...]}."""
    req = urllib.request.Request(
        f"{BASE}/chat",
        method="POST",
        data=json.dumps({"message": message}).encode("utf-8"),
        headers=_headers(),
    )
    text_parts: list[str] = []
    cards: list = []
    # ★ Must exceed the server's wall-clock limit for one round (240s for the chat tier), otherwise while the server is still wrapping up normally
    #   the client times out first, and the user sees a fake failure like "cannot connect".
    with _request(req, timeout=300) as resp:
        for raw in resp:                                  # SSE: line by line data: {...}
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "delta" in evt:
                text_parts.append(evt["delta"])
            elif "cards" in evt:
                cards = evt["cards"]
            elif "error" in evt:
                _die(evt["error"])
    return {"text": "".join(text_parts), "cards": cards}


def get(path: str, timeout: int = 30) -> dict:
    """GET a read-only endpoint (e.g. data/today, meta/capabilities)."""
    req = urllib.request.Request(f"{BASE}/{path.lstrip('/')}", headers=_headers())
    with _request(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(url: str, include_seo: bool = True) -> dict:
    """No-login site health check: runs all GEO 25 categories + SEO 12 categories; the free tier sees details for 5+3 categories.

    **No token** - this is the only command in the whole skill that needs no authorization; runs without credentials, unlimited.
    The trade-off: locked categories only show counts, not details; no fix suggestions; not written to account history.
    """
    url = _normalize_url(url)
    body = json.dumps({"url": url, "include_seo": include_seo}).encode("utf-8")
    req = urllib.request.Request(
        f"{SITE_API}/check/anonymous",
        data=body,
        headers={"Content-Type": "application/json"},   # deliberately no Authorization
        method="POST",
    )
    # 25+12 categories actually run and slow sites take time; allow a generous timeout so the agent does not mistake it for an outage
    with _request(req, timeout=180) as resp:
        d = json.loads(resp.read().decode("utf-8"))

    def _half(part: dict) -> dict:
        """Compress one half of the result into something that can be read to the user: aggregate by category, drop noise like i18n keys.

        Besides the existing per-category aggregation, also squeeze out `top_issues`: **the items that are not right**,
        fail before warn. User feedback said "the report is full of jargon, which hurts readability" -
        because the only readable thing was the 25 category names. Here "which items really have problems"
        is pulled out separately so the agent reads only those by default, with details kept in visible_categories for reference.
        """
        cats: dict = {}
        issues: list = []
        rank = {"FAIL": 0, "WARN": 1}
        for c in part.get("checks") or []:
            row = cats.setdefault(c["category"], {"pass": 0, "warn": 0, "fail": 0, "info": 0, "items": []})
            row[c["status"].lower()] = row.get(c["status"].lower(), 0) + 1
            row["items"].append({"status": c["status"], "message": c.get("message") or ""})
            status = (c.get("status") or "").upper()
            if status in rank and (c.get("message") or "").strip():
                issues.append({"严重度": "不通过" if status == "FAIL" else "待改进",
                               "_r": rank[status], "类目": c["category"],
                               "问题": c["message"].strip()})
        issues.sort(key=lambda x: x["_r"])
        for it in issues:
            it.pop("_r", None)
        return {
            "score": part.get("score"),
            "grade": part.get("grade"),
            "summary": part.get("summary"),
            "top_issues": issues[:5],
            "issue_count": len(issues),
            "visible_categories": cats,
            "locked_count": len(part.get("locked_categories") or []),
            "locked_categories": part.get("locked_categories") or [],
        }

    out = {"url": d.get("url") or url, "url_checked": url,
           "tier": d.get("tier"), "geo": _half(d)}
    if d.get("seo"):
        out["seo"] = _half(d["seo"])
    out["note"] = (
        "免登录免费检测,不限次。"
        "**先把 `url_checked` 念出来让用户核对测的是不是他要的那个站。**"
        "念结果时**默认只讲分数 + `top_issues` 那几条**,每条说清「这样会导致什么、该怎么改」;"
        "**不要逐条罗列检查项名**(用户反馈说专业名词太多、读不下去),"
        "他要全部明细再展开 `visible_categories`。"
        "锁住的类目只给了名字没给明细,也没有修复建议 —— **绝不替它猜结论**。"
        "要完整明细 + 修复建议:让用户在 Vigilath 注册后跑 `login` 授权。"
        "要知道 AI 引擎实际怎么说这个品牌(被引用率/竞品对比/舆情):那是「一键全面诊断」,"
        "走 `chat`,需要授权,整单 8-20 分钟。"
    )
    return out


def industry(key: str = "") -> dict:
    """No login: see who is TOP1 for an industry in AI search, how concentrated it is, and which questions have no stable recommendation yet.

    Data comes from the platform's real batch runs (not model estimates), but **only covers industries with datasets**; without a key, list them first.
    Like check, needs no credentials.
    """
    if not key:
        req = urllib.request.Request(f"{SITE_API}/industry/list", headers={"Accept": "application/json"})
        with _request(req, timeout=30) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        rows = d.get("industries") or []
        return {
            "industries": rows,
            "industry_count": d.get("industry_count", len(rows)),
            "sample_note": d.get("sample_note", ""),
            "note": ("这是已建库的行业清单。挑一个跑 `industry <行业名>` 看详情。"
                     "**主动说明覆盖范围**(见 sample_note):行业库是逐个建库的,不是全行业覆盖。"
                     "用户的行业不在里面时,如实说「这个行业还没有建库」,"
                     "**不要拿别的行业的数字替代**;想测他自己的品牌要走需要授权的诊断。"),
        }
    req = urllib.request.Request(
        f"{SITE_API}/industry/{urllib.parse.quote(key)}/data",
        headers={"Accept": "application/json"},
    )
    try:
        with _request(req, timeout=60, passthrough=(404,)) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # The 404 **body is what matters**: it carries the list of industries with datasets. Without it the agent can only say
        # "not found" and then fill in with whatever brand numbers are at hand - that is how a user asking
        # "industrial cleaning robot industry trends" got the sample cleaning data.
        detail = {}
        try:
            detail = (json.loads(e.read().decode("utf-8", "ignore") or "{}") or {}).get("detail") or {}
        except Exception:  # noqa: BLE001
            detail = {}
        if not isinstance(detail, dict):
            detail = {"message": str(detail)}
        return {
            "已建库": False, "查询的行业": key,
            "已建库的行业": detail.get("available") or [],
            "message": detail.get("message") or f"「{key}」这个行业还没有建库。",
            "sample_note": detail.get("sample_note", ""),
            "note": ("如实告诉用户这个行业还没建库,并把「已建库的行业」清单给他。"
                     "**绝不**用名字相近的另一个行业的数据回答(「工业清洁机器人」不是"
                     "「商用清洁机器人」),**更绝不**拿某一个品牌的数字当行业数字。"),
        }
    d["已建库"] = True
    d["note"] = ("免登录、真实跑批数据。`coverage` 等百分比已是最终值,不要再乘 100;"
                 "`series` 是历史走势,`series_keys` 是对应日期。"
                 "这是**行业**层面的数据,不是用户自己品牌的表现 —— 别混说。"
                 "问「行业走势」只能用 `heat` / `cr3` / `top1` 里的 series 回答,"
                 "返回里没有的走势就说没有,**绝不**拿某个品牌的数字顶替。"
                 "报数前先照 `sample_note` 交代样本口径(建库了几个行业、榜上最多几个品牌)——"
                 "用户看到只有几个品牌却不知道口径时,会以为数据不可信。")
    return d


def _device_id() -> str:
    """Persistent local identifier; no-login capabilities are rate-limited by it + the egress IP.

    If missing, generate one and write it into ~/.vigilath/config. **It must be writable even without a token** -
    no-login users have no credential file at all, and lacking a token must not prevent generating the identifier.
    Users clearing it resets the device quota, which is known: it is just natural bucketing; the hard limit is the server-side egress IP.
    """
    did = _conf("VIGILATH_DEVICE_ID")
    if did:
        return did
    import uuid as _uuid

    did = "dev_" + _uuid.uuid4().hex[:20]
    path = os.path.expanduser("~/.vigilath/config")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = dict(_CFG)
        cfg["VIGILATH_DEVICE_ID"] = did
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Vigilath —— 机密,勿提交\n")
            for k, v in cfg.items():
                f.write(f"{k}={v}\n")
        os.chmod(path, 0o600)
    except OSError:
        pass          # in a read-only environment, generate a new one each time; the server still has the IP gate
    _CFG["VIGILATH_DEVICE_ID"] = did
    return did


def mention_quota() -> dict:
    """Query without deducting: how many quick audits this device / this egress IP has left."""
    q = urllib.parse.urlencode({"device_id": _device_id()})
    req = urllib.request.Request(f"{SITE_API}/check/mention-quota?{q}",
                                 headers={"Accept": "application/json"})
    with _request(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def mention(brand: str, industry_terms: list, wait: bool = True) -> dict:
    """No login: ask AI about the brand and paste back the **engines' verbatim answers**. 3 category questions x 3 engines.

    Quota is counted per device + egress IP (default 2 per device / 30 days) - each run really costs engine spend,
    unlike zero-cost capabilities such as check / industry.

    ★ **Check the quota once** before starting. The server deducts at start time, so "out of quota"
      is better known as early as possible: it used to start directly and only learn on a 429; that request was not charged,
      but the user had already waited a round. `/check/mention-quota` is a query-only endpoint that exists for this step.

    The server is asynchronous; polling is hidden here: one command waits for the result, feeling synchronous.
    """
    try:
        st = mention_quota()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a failed quota check should not block the real work; let the server decide
        st = {}
    if st and not st.get("unlimited", False) and not st.get("allowed", True):
        return {
            "status": "no_quota",
            "quota": {k: st.get(k) for k in
                      ("device_used", "device_limit", "ip_used", "ip_limit", "window_days")},
            "note": ("免费快测额度用完了,**这次没有发起、没有消耗任何东西**。"
                     "如实告诉用户额度用完(设备与出口 IP 各有一份配额,公司网络里"
                     "可能是同事先用掉了),**不要重试**。检测本身不收费,限次只为防刷;"
                     "注册 Vigilath 账号后额度更高。"),
        }

    payload = json.dumps({
        "brand": brand,
        "industry_terms": industry_terms,
        "device_id": _device_id(),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{SITE_API}/check/mention/anonymous",
        data=payload,
        headers={"Content-Type": "application/json"},   # deliberately no Authorization
        method="POST",
    )
    with _request(req, timeout=60) as resp:
        started = json.loads(resp.read().decode("utf-8"))
    if not wait:
        return started

    task_id = started["task_id"]
    print(f"[vigilath] 已发起(剩余额度见 quota),约 1-3 分钟,轮询中…", file=sys.stderr)
    import time as _time

    deadline = _time.time() + 600
    while _time.time() < deadline:
        _time.sleep(6)
        req2 = urllib.request.Request(f"{SITE_API}/check/mention/{task_id}")
        try:
            with _request(req2, timeout=30) as resp:
                d = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue        # network blips should not interrupt polling
        if d.get("status") == "done":
            out = d.get("result") or {}
            out["quota"] = started.get("quota")
            return out
        if d.get("status") == "failed":
            # The server now refunds the quota when "no engine answered at all", with
            # quota_refunded in the response. Stop always saying "quota has been counted" - that message once made users think
            # an attempt was wasted (and at the time it really was).
            refunded = bool(d.get("quota_refunded"))
            return {"status": "failed", "error": d.get("error") or "未知错误",
                    "quota_refunded": refunded,
                    "note": ("这一轮没跑出结果,**额度已经退回**,可以直接再试一次。"
                             if refunded else
                             "这一轮没跑出结果。稍等一会儿再试,不要立刻连着重试。")}
    return {"status": "timeout", "task_id": task_id,
            "note": f"等超时了。稍后用 `mention-status {task_id}` 再查,任务还在跑。"}


def wallet() -> dict:
    """Check balance. **Look before any action that spends money** - say early when the balance is insufficient instead of letting the user wait for a failure."""
    return get("wallet")


def topup(amount_yuan: str, tx_hash: str = "") -> dict:
    """Top up. Two steps: first get the payment requirement (402), then after paying on-chain come back with tx_hash to settle.

    Without tx_hash it returns "how to pay"; **read the amount, payee address and network to the user exactly as given**,
    do not make them up. Users can also choose to top up on the web; the URL is in the returned web field.

    ★ The `--wechat` branch was removed on 2026-08-30. It called `{SITE_API}/skillpay/topup`,
      and that route **was never implemented** (skillpay only did /subscribe) - anyone using it
      only got a 404, yet the docs called it "preferred". WeChat top-up now has only two real paths:
      web QR code (the URL given in the web field below), and having the agent create an order with
      create_topup_order **in the chat on our own website**. The skill channel cannot reach the latter, so this only points to the web.
    """
    try:
        cents = int(round(float(amount_yuan) * 100))
    except ValueError:
        _die(f"充值金额要是数字(元),收到:{amount_yuan!r}")
    headers = {"Content-Type": "application/json"}
    if tx_hash:
        import base64 as _b64

        headers["X-PAYMENT"] = _b64.b64encode(
            json.dumps({"tx_hash": tx_hash}).encode("utf-8")
        ).decode("ascii")
    req = urllib.request.Request(
        f"{BASE}/wallet/topup",
        data=json.dumps({"amount_cents": cents}).encode("utf-8"),
        headers={**headers, "Authorization": _headers()["Authorization"]},
        method="POST",
    )
    try:
        with _request(req, timeout=60, passthrough=(402,)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 402:
            # 402 is a **normal step** of this flow, not an error: it carries the payment requirement
            body = json.loads(e.read().decode("utf-8", "ignore") or "{}")
            body["_hint"] = ("这不是失败 —— 按 accepts 里的网络/资产/收款地址/金额付款,"
                             "拿到交易哈希后重跑:topup <金额> --tx <哈希>。"
                             "或者引导用户到 web 字段给的页面用微信充值。")
            return body
        raise


# Advanced audits: no-login versions of the six "Advanced audits" cards on the website.
# The two zero-cost ones are unlimited; the four paid ones are limited per device + egress IP, returning 429 when the quota runs out.
ADV_MODES = {
    "aeo":        ("url",    "AEO 可见性审计(零成本、不限次)"),
    "crawl-test": ("url",    "AI 爬虫测试(零成本、不限次)"),
    "authority":  ("url",    "权威信号审计(真实付费调用,有额度)"),
    "citation":   ("url",    "AI 引用检测(真实付费调用,有额度)"),
    "entity":     ("entity", "实体 GEO 审计,不需要网址(真实付费调用,有额度)"),
    "compare":    ("urls",   "多站点对比,2-5 个网址(真实付费调用,有额度)"),
    "visibility": ("url",    "AI 可见性审计,跨多引擎的全面版(真实付费调用,有额度)"),
    "competitive-intel": ("url", "竞品情报:谁在被推荐、信源偏好(真实付费调用,有额度)"),
}


def advanced(mode: str, *args) -> dict:
    """Run one advanced audit without login. **No Authorization** - these are no-login capabilities."""
    if mode not in ADV_MODES:
        _die("mode 只能是:" + " / ".join(ADV_MODES) + "\n" +
             "\n".join(f"  {k:11s} {v[1]}" for k, v in ADV_MODES.items()))
    kind = ADV_MODES[mode][0]
    if kind == "url":
        if not args:
            _die(f"{mode} 需要一个网址")
        body = {"url": _normalize_url(args[0])}
    elif kind == "entity":
        if not args:
            _die("entity 需要实体名称,如:advanced entity 某某品牌 brand")
        body = {"entity_name": args[0],
                "entity_type": args[1] if len(args) > 1 else "brand"}
    else:
        urls = [_normalize_url(a) for a in args if not a.startswith("--")]
        if len(urls) < 2:
            _die("compare 至少要两个网址")
        body = {"urls": urls[:5]}
    body["device_id"] = _device_id()

    req = urllib.request.Request(
        f"{SITE_API}/check/advanced-anon/{mode}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},   # deliberately no Authorization
        method="POST",
    )
    # These actually crawl the site and query engines; allow a generous timeout
    with _request(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def plans() -> dict:
    """See the monthly subscription tiers and prices for continuous monitoring. **Viewable without login** - know the price before recommending."""
    req = urllib.request.Request(f"{SITE_API}/skillpay/plans", headers={"Accept": "application/json"})
    with _request(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def subscribe(plan_slug: str, out_trade_no: str = "") -> dict:
    """Start a continuous monitoring monthly subscription (WeChat Pay). Two steps:

    1) Without an order id -> returns 402: containing the payment code, order id and amount
    2) After the user authorizes in WeChat, **call again with the order id** -> only then is it activated

    Missing the second step means it never activates. Payment receipt and activation go by our order query, not by what the user says.
    """
    headers = dict(_headers())
    if out_trade_no:
        headers["X-Out-Trade-No"] = out_trade_no
    req = urllib.request.Request(
        f"{SITE_API}/skillpay/subscribe",
        data=json.dumps({"plan_slug": plan_slug}).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with _request(req, timeout=90, passthrough=(402,)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 402:
            d = json.loads(e.read().decode("utf-8", "ignore") or "{}")
            d["_hint"] = ("402 不是失败。① 把 WeixinPay-Required 作为 paymentCode 交给 "
                          "weixinpay_pay 申请授权;② 付完后带订单号再跑一次:"
                          "`subscribe <档位> --order <订单号>`。")
            return d
        raise


def login() -> "None":
    """Device code authorization: the terminal shows a pairing code, a person approves in the browser, and the token is written back to ~/.vigilath/config.

    Used when the token expires / switching accounts, without reinstalling the skill. Uses the same endpoints as install.sh.
    """
    import socket
    import time

    def _post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{BASE}/{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},   # no token before authorization, so no Authorization
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        d = _post("device/code", {"client": socket.gethostname()})
    except Exception as e:  # noqa: BLE001
        _die(f"连不上 {BASE} —— 检查网络或 VIGILATH_BASE:{e}")
    if d.get("status") != "ok":
        _die(f"起授权流程失败:{d.get('message') or d.get('status')}")

    # When the host agent runs this command, stdout is usually a pipe - default full buffering would hold the pairing code in the buffer,
    # and the user would wait until timeout without seeing it. Authorization relies on a human reading it, so line buffering is required here.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001  (old Python / unusual stdout)
        pass

    print("\n请在浏览器完成授权(10 分钟内有效):")
    print(f"    打开:{d.get('verify_url_complete') or d.get('verify_url')}")
    print(f"    配对码:{d['user_code']}\n")
    print("等待批准中…")

    interval = int(d.get("interval") or 5)
    deadline = time.time() + int(d.get("expires_in") or 600)
    token = ""
    while time.time() < deadline:
        time.sleep(interval)
        try:
            r = _post("device/token", {"device_code": d["device_code"]})
        except Exception:  # noqa: BLE001
            continue        # network blips should not interrupt polling
        st = r.get("status")
        if st == "approved":
            token = r["token"]
            break
        if st in ("denied", "expired"):
            _die(r.get("message") or f"授权失败:{st}")
    if not token:
        _die("等待授权超时(10 分钟),请重试 login")

    path = os.path.expanduser("~/.vigilath/config")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = dict(_CFG)
    cfg["VIGILATH_AGENT_TOKEN"] = token
    cfg["VIGILATH_BASE"] = BASE
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Vigilath GEO —— 机密,勿提交\n")
        for k, v in cfg.items():
            f.write(f"{k}={v}\n")
    os.chmod(path, 0o600)
    print("✅ 授权成功,凭证已写入 ~/.vigilath/config(权限 600)")


def main(argv: list) -> "None":
    if len(argv) < 2:
        _die('用法:geo_client.py check <网址> | mention <品牌> <行业词> | industry [行业名] | '
             'chat "问题" | data <name> | wallet | topup <金额> | plans | subscribe <档位> | advanced <mode> | capabilities | login')
    cmd = argv[1]
    if cmd == "chat":
        if len(argv) < 3:
            _die('chat 需要一个问题参数,如:chat "今天投放效果?"')
        out = chat(argv[2])
        print(out["text"].strip())
        if out["cards"]:
            print("\n--- 结构化卡片(把数字念给用户)---")
            print(json.dumps(out["cards"], ensure_ascii=False, indent=2))
    elif cmd == "data":
        if len(argv) < 3:
            _die("data 需要名字:today / coverage / report ...")
        print(json.dumps(get(f"data/{argv[2]}"), ensure_ascii=False, indent=2))
    elif cmd == "check":
        if len(argv) < 3:
            _die("check 需要一个网址,如:check https://example.com")
        # --no-seo GEO only (S1 site health check); --seo-only SEO only (S10 traditional SEO health check)
        seo_only = "--seo-only" in argv
        out = check(argv[2], include_seo="--no-seo" not in argv or seo_only)
        if seo_only:
            out = {k: v for k, v in out.items() if k != "geo"}
            out["note"] = (
                "免登录免费 SEO 体检,不限次。12 类全跑,免费档给 3 类明细,其余只给类目名。"
                "SEO 分与 GEO 分是两套独立口径,**不要相加、不要合成综合分**。"
                "想知道 AI 爬虫读不读得懂这个站,那是另一件事,跑 `check <网址> --no-seo`。"
            )
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif cmd == "mention":
        if len(argv) < 4:
            _die('mention 需要品牌名和至少一个行业词,如:mention "你的品牌名" 商用清洁机器人')
        print(json.dumps(mention(argv[2], [a for a in argv[3:] if not a.startswith("--")]),
                         ensure_ascii=False, indent=2))
    elif cmd == "mention-status":
        if len(argv) < 3:
            _die("mention-status 需要 task_id")
        req = urllib.request.Request(f"{SITE_API}/check/mention/{argv[2]}")
        with _request(req, timeout=30) as resp:
            print(resp.read().decode("utf-8"))
    elif cmd == "wallet":
        print(json.dumps(wallet(), ensure_ascii=False, indent=2))
    elif cmd == "topup":
        if len(argv) < 3:
            _die("topup 需要金额(元),如:topup 100;付款后核销:topup 100 --tx 0x…")
        if "--wechat" in argv:
            _die("`--wechat` 已经去掉了 —— 它打的那个接口从来没有实现过,只会 404。"
                 "微信充值请让用户到网页扫码(跑一次 `topup <金额>`,返回的 web 字段就是"
                 "充值页地址),或者在 Vigilath 网页里直接跟 GEO 助手说「帮我充值」。")
        tx = ""
        if "--tx" in argv:
            i = argv.index("--tx")
            tx = argv[i + 1] if len(argv) > i + 1 else ""
        print(json.dumps(topup(argv[2], tx), ensure_ascii=False, indent=2))
    elif cmd == "advanced":
        if len(argv) < 3:
            _die("用法:advanced <mode> <参数…>;mode 可选:" + " / ".join(ADV_MODES))
        print(json.dumps(advanced(argv[2], *argv[3:]), ensure_ascii=False, indent=2))
    elif cmd == "plans":
        print(json.dumps(plans(), ensure_ascii=False, indent=2))
    elif cmd == "subscribe":
        if len(argv) < 3:
            _die("用法:subscribe watch10|watch30|watch100 [--order <订单号>]")
        order = ""
        if "--order" in argv:
            i = argv.index("--order")
            order = argv[i + 1] if len(argv) > i + 1 else ""
        print(json.dumps(subscribe(argv[2], order), ensure_ascii=False, indent=2))
    elif cmd == "industry":
        print(json.dumps(industry(argv[2] if len(argv) > 2 else ""), ensure_ascii=False, indent=2))
    elif cmd == "capabilities":
        print(json.dumps(get("meta/capabilities"), ensure_ascii=False, indent=2))
    elif cmd == "login":
        login()
    else:
        _die(f"未知命令:{cmd}(支持 check / mention / advanced / industry / chat / data / plans / subscribe / wallet / topup / capabilities / login)")


if __name__ == "__main__":
    main(sys.argv)
