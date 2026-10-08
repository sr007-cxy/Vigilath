"""Cookie/token pool management - per-request rotation over multiple cookies + automatic health check removing dead cookies + usage stats.

Sentiment monitoring direct-collection sources (Weibo/Zhihu login sessions, Baidu/Sogou bypassing captchas) all rely on cookies, and cookies expire.
This module upgrades "one cookie per env" into a **pool**:

  Separate multiple cookies with `||` in the environment variable (backward compatible: one cookie = pool size 1):
      WEIBO_COOKIE="SUB=aaa; SUBP=bbb || SUB=ccc; SUBP=ddd"

Capabilities:
  · Per-request rotation: each call takes the next "live" cookie (round-robin), spreading risk-control load; one failing does not affect the rest.
  · Dead cookie removal: a cookie judged invalid (redirected to login/verification page) -> marked dead, skipped automatically, never used again.
  · Automatic health check: `python -m search.cookie_pool --check` probes every cookie of the four sources for live/dead and updates state.
       Run daily by geo-cookie-check.timer before the sentiment monitoring jobs; dead cookies are logged (journal) as a reminder to replace them.
  · Usage stats: each cookie records ok/fail counts and last success/failure times, persisted to a state file.

**Security**: the state file **only stores cookie fingerprints** (first 12 chars of sha256) + stats, never plaintext cookies.
State file path env `GEO_COOKIE_STATE` (default /var/lib/geo/cookie_state.json, falls back to /tmp if not writable).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))
_LOCK = threading.Lock()
_STATE: dict | None = None          # shared within the process: {source: {fp: entry}}

_DEFAULT_STATE = "/var/lib/geo/cookie_state.json"
_FALLBACK_STATE = "/tmp/geo_cookie_state.json"

# Rolling capture: when the platform proactively issues a new token in a response, save it to renew (zero risk, never logs in proactively).
# Note: this file contains plaintext cookies (renewed values), chmod 600; the _STATE state file still only stores fingerprints.
_REFRESH: dict | None = None
_DEFAULT_REFRESH = "/var/lib/geo/cookie_refresh.json"
_FALLBACK_REFRESH = "/tmp/geo_cookie_refresh.json"
# Per-source auth cookie keys that "can be renewed" (other keys are left alone)
_REFRESH_KEYS = {
    "weibo": ("SUB", "SUBP", "ALF", "SSOLoginState"),
    "zhihu": ("z_c0",),   # only the real auth token; tracking cookies like BEC change every time, renewing them is pointless
}

# Four sources: source -> environment variable name to read
SOURCES = {
    "weibo": "WEIBO_COOKIE",
    "zhihu": "ZHIHU_COOKIE",
    "baidu": "BAIDU_COOKIE",
    "sogou": "SOGOU_COOKIE",
}


def _now() -> str:
    return datetime.now(_CST).isoformat(timespec="seconds")


def _fp(cookie: str) -> str:
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()[:12]


def _split(raw: str) -> list[str]:
    """Split multiple cookies by `||`; strip whitespace, dedupe, preserve order."""
    out, seen = [], set()
    for c in (raw or "").split("||"):
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _state_path() -> str:
    p = os.environ.get("GEO_COOKIE_STATE", "").strip() or _DEFAULT_STATE
    d = os.path.dirname(p)
    try:
        os.makedirs(d, exist_ok=True)
        # Probe writability
        t = os.path.join(d, ".wtest")
        with open(t, "w") as f:
            f.write("")
        os.remove(t)
        return p
    except Exception:
        return _FALLBACK_STATE


def _load() -> dict:
    global _STATE
    if _STATE is not None:
        return _STATE
    try:
        with open(_state_path()) as f:
            _STATE = json.load(f)
    except Exception:
        _STATE = {}
    return _STATE


def _flush() -> None:
    """Atomic write (tmp+rename). The caller must hold _LOCK."""
    if _STATE is None:
        return
    p = _state_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_STATE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        print(f"  [cookie_pool] 状态写入失败 {p}: {e}", file=sys.stderr)


def _new_entry() -> dict:
    return {"alive": True, "ok": 0, "fail": 0, "last_ok": None,
            "last_fail": None, "dead_reason": None}


def _parse_cookie(s: str) -> dict:
    d = {}
    for p in (s or "").split(";"):
        p = p.strip()
        if "=" in p:
            k, v = p.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def _refresh_path() -> str:
    p = os.environ.get("GEO_COOKIE_REFRESH", "").strip() or _DEFAULT_REFRESH
    d = os.path.dirname(p)
    try:
        os.makedirs(d, exist_ok=True)
        return p
    except Exception:
        return _FALLBACK_REFRESH


def _load_refresh() -> dict:
    global _REFRESH
    if _REFRESH is not None:
        return _REFRESH
    try:
        with open(_refresh_path()) as f:
            _REFRESH = json.load(f)
    except Exception:
        _REFRESH = {}
    return _REFRESH


def _flush_refresh() -> None:
    """Atomic write + chmod 600 (contains plaintext). The caller must hold _LOCK."""
    if _REFRESH is None:
        return
    p = _refresh_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_REFRESH, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except Exception as e:
        print(f"  [cookie_pool] 刷新存储写入失败 {p}: {e}", file=sys.stderr)


class CookiePool:
    """Cookie pool for one source. Each adapter call creates a new one; state lives in the process-shared _STATE."""

    def __init__(self, source: str, env_var: str | None = None):
        self.source = source
        env_var = env_var or SOURCES.get(source, source.upper() + "_COOKIE")
        self.cookies = _split(os.environ.get(env_var, ""))
        self.fps = [_fp(c) for c in self.cookies]
        # Effective cookie: if rolling capture has stored a newer value (keyed by base fingerprint), use the renewed one; otherwise the env value
        self.effective = list(self.cookies)
        with _LOCK:
            st = _load().setdefault(source, {})
            for fp in self.fps:
                st.setdefault(fp, _new_entry())
            rf = _load_refresh().get(source, {})
        for i, fp in enumerate(self.fps):
            ov = rf.get(fp)
            if ov and ov.get("cookie"):
                self.effective[i] = ov["cookie"]
        self._rr = 0

    def count(self) -> int:
        return len(self.cookies)

    def _entry(self, idx: int) -> dict:
        return _load()[self.source][self.fps[idx]]

    def alive_indices(self) -> list[int]:
        st = _load()[self.source]
        return [i for i, fp in enumerate(self.fps) if st[fp].get("alive", True)]

    def alive_count(self) -> int:
        return len(self.alive_indices())

    def try_cookies(self):
        """Generator: starting from the round-robin cursor, yield (idx, cookie) for one pass over all "live" cookies.

        Adapter usage: for idx, ck in pool.try_cookies(): try; on success mark_ok+break,
        if judged dead, mark_dead and continue to the next."""
        alive = self.alive_indices()
        if not alive:
            return
        n = len(alive)
        start = self._rr % n
        self._rr += 1
        for k in range(n):
            i = alive[(start + k) % n]
            yield i, self.effective[i]   # effective value (possibly renewed by rolling capture)

    def mark_ok(self, idx: int) -> None:
        with _LOCK:
            e = self._entry(idx)
            e["alive"] = True
            e["ok"] = e.get("ok", 0) + 1
            e["last_ok"] = _now()
            e["dead_reason"] = None
            _flush()

    def capture_refresh(self, idx: int, set_cookies: dict) -> None:
        """Rolling capture: if the platform response issues a newer auth token, merge it into the effective cookie and persist the renewal.

        Zero risk - only adopts values the server proactively Set-Cookie'd, never constructs/logs in. set_cookies = the cookies
        of this request's response (name->value). Renewed values are stored by base fingerprint and picked up on next startup."""
        keys = _REFRESH_KEYS.get(self.source)
        if not keys or not set_cookies:
            return
        fresh = {k: v for k, v in set_cookies.items() if k in keys and v}
        if not fresh:
            return
        d = _parse_cookie(self.effective[idx])
        if all(d.get(k) == v for k, v in fresh.items()):
            return                          # no change, do not write
        d.update(fresh)
        newstr = "; ".join(f"{k}={v}" for k, v in d.items())
        with _LOCK:
            _load_refresh().setdefault(self.source, {})[self.fps[idx]] = {
                "cookie": newstr, "ts": _now()}
            _flush_refresh()
        self.effective[idx] = newstr
        print(f"  [cookie_pool] {self.source} cookie#{idx + 1} 捕获到刷新 token"
              f"({','.join(fresh)}),已续期", file=sys.stderr)

    def mark_dead(self, idx: int, reason: str) -> None:
        with _LOCK:
            e = self._entry(idx)
            e["alive"] = False
            e["fail"] = e.get("fail", 0) + 1
            e["last_fail"] = _now()
            e["dead_reason"] = (reason or "")[:80]
            _flush()
        print(f"  [cookie_pool] {self.source} cookie#{idx + 1}(fp {self.fps[idx]}) "
              f"判失效并剔除:{reason}", file=sys.stderr)


# ── Health-check probes ───────────────────────────────────────────────
# One probe(cookie)->(ok:bool, reason:str) per source, a lightweight real request distinguishing login session vs wall.

_PROBE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_PROBE_Q = "Acme Cloud"


def _probe_weibo(cookie: str) -> tuple[bool, str]:
    import requests
    try:
        r = requests.get("https://s.weibo.com/weibo", params={"q": _PROBE_Q},
                         headers={"User-Agent": _PROBE_UA, "Cookie": cookie,
                                  "Referer": "https://s.weibo.com/"}, timeout=20)
    except Exception as e:
        return False, f"网络异常:{str(e)[:40]}"
    h = r.text
    has_feed = ("card-wrap" in h) or ("feed_list_content" in h)
    if "passport.weibo.com" in h or (not has_feed and len(h) < 3000):
        return False, "被跳登录页/新浪通行证(SUB 过期)"
    if has_feed:
        return True, "ok"
    # Large page but no cards = cookie is fine, the term just has no results - do not mark dead
    return True, "ok(无结果)"


def _probe_zhihu(cookie: str) -> tuple[bool, str]:
    import requests
    try:
        r = requests.get("https://www.zhihu.com/api/v4/search_v3",
                         params={"t": "general", "q": _PROBE_Q, "limit": 3},
                         headers={"User-Agent": _PROBE_UA, "Cookie": cookie,
                                  "Accept": "application/json", "x-api-version": "3.0.91"},
                         timeout=20)
    except Exception as e:
        return False, f"网络异常:{str(e)[:40]}"
    if r.status_code in (401, 403):
        return False, f"鉴权失败 {r.status_code}(z_c0 过期)"
    try:
        j = r.json()
    except Exception:
        return False, f"非JSON响应(status={r.status_code})"
    if isinstance(j, dict) and j.get("error"):
        return False, f"API error:{str(j['error'])[:40]}"
    if isinstance(j, dict) and "data" in j:
        return True, "ok"
    return False, f"无 data 字段(status={r.status_code})"


def _probe_blocked(source: str):
    """Baidu/Sogou: cookies mainly bypass the verification page; probe = whether a non-verification page is returned."""
    def _p(cookie: str) -> tuple[bool, str]:
        import requests
        if source == "baidu":
            from .baidu import _is_blocked
            url, params = "https://www.baidu.com/s", {"wd": _PROBE_Q, "rn": 10, "ie": "utf-8"}
            ref = "https://www.baidu.com/"
        else:
            from .sogou import _is_blocked
            url, params = "https://www.sogou.com/web", {"query": _PROBE_Q}
            ref = "https://www.sogou.com/"
        try:
            r = requests.get(url, params=params, headers={
                "User-Agent": _PROBE_UA, "Cookie": cookie,
                "Accept-Language": "zh-CN,zh;q=0.9", "Referer": ref}, timeout=20)
            r.encoding = "utf-8"
        except Exception as e:
            return False, f"网络异常:{str(e)[:40]}"
        if _is_blocked(r.text):
            return False, "验证/验证码页(cookie 失效或 IP 被封)"
        return True, "ok"
    return _p


_PROBES = {
    "weibo": _probe_weibo,
    "zhihu": _probe_zhihu,
    "baidu": _probe_blocked("baidu"),
    "sogou": _probe_blocked("sogou"),
}


def check_all() -> dict:
    """Health-check every cookie of the four sources, update state (live -> mark_ok / dead -> mark_dead), return a report."""
    report: dict[str, list] = {}
    for source, env_var in SOURCES.items():
        pool = CookiePool(source, env_var)
        probe = _PROBES[source]
        rows = []
        for idx in range(pool.count()):
            ok, reason = probe(pool.effective[idx])   # probe the renewed effective value
            if ok:
                pool.mark_ok(idx)
            else:
                pool.mark_dead(idx, reason)
            e = pool._entry(idx)
            rows.append({"idx": idx + 1, "fp": pool.fps[idx], "alive": ok,
                         "reason": reason, "ok": e["ok"], "fail": e["fail"]})
        report[source] = rows
    return report


def _print_report(report: dict) -> int:
    """Print the health-check report and return the total number of dead cookies."""
    dead = 0
    print(f"[cookie-check] {_now()} —— cookie 池巡检")
    for source, rows in report.items():
        if not rows:
            print(f"  · {source:6s}: (未配置 cookie)")
            continue
        alive_n = sum(1 for r in rows if r["alive"])
        print(f"  · {source:6s}: {alive_n}/{len(rows)} 活")
        for r in rows:
            mark = "✓活" if r["alive"] else "✗死"
            line = (f"      #{r['idx']} fp={r['fp']} {mark} "
                    f"(ok={r['ok']} fail={r['fail']})")
            if not r["alive"]:
                dead += 1
                line += f" — {r['reason']}"
            print(line)
    if dead:
        print(f"[cookie-check] ⚠ 共 {dead} 条 cookie 失效,请更新对应环境变量"
              f"(cookies.conf 里用 || 可配多组)")
    else:
        print("[cookie-check] 全部 cookie 健康")
    return dead


def _webhook_text(report: dict, n_dead: int) -> str:
    """Health-check result -> Feishu text message."""
    head = "🍪 cookie 池巡检" + (f" — ⚠ {n_dead} 条失效" if n_dead else " — ✅ 全部健康")
    lines = [head, _now(), ""]
    for source, rows in report.items():
        if not rows:
            lines.append(f"· {source}: 未配置")
            continue
        alive_n = sum(1 for r in rows if r["alive"])
        lines.append(f"· {source}: {alive_n}/{len(rows)} 活")
        for r in rows:
            if not r["alive"]:
                lines.append(f"    ✗ #{r['idx']}({r['fp']}) {r['reason']}")
    if n_dead:
        lines += ["", "请更新对应 cookies.conf 环境变量(多组用 || 分隔)。"]
    return "\n".join(lines)


def _push_webhook(text: str, url: str) -> None:
    import requests
    try:
        r = requests.post(url, json={"msg_type": "text", "content": {"text": text}}, timeout=15)
        j = r.json() if r.status_code == 200 else {}
        # Feishu success: new {"code":0} or old {"StatusCode":0}; default sentinel -1 avoids misjudging
        ok = j.get("code", j.get("StatusCode", -1)) == 0
        print(f"  [cookie-check] webhook 推送 {'成功' if ok else '失败'}: {r.text[:120]}",
              file=sys.stderr)
    except Exception as e:
        print(f"  [cookie-check] webhook 推送异常: {e}", file=sys.stderr)


if __name__ == "__main__":
    if "--check" in sys.argv:
        rep = check_all()
        n_dead = _print_report(rep)          # always write the full report to the log (journal)
        # Only push the webhook when weibo/zhihu fail; stay quiet when all green (Baidu/Sogou are optional, no alert)
        alert_dead = sum(1 for s in ("weibo", "zhihu") for r in rep.get(s, []) if not r["alive"])
        hook = os.environ.get("COOKIE_CHECK_WEBHOOK", "").strip()
        if hook and alert_dead:
            _push_webhook(_webhook_text(rep, alert_dead), hook)
        else:
            print("[cookie-check] 全绿或无需告警,跳过 webhook 推送", file=sys.stderr)
        sys.exit(1 if n_dead else 0)
    # Default: print the current persisted state (no requests sent)
    st = _load()
    print(json.dumps(st, ensure_ascii=False, indent=2))
