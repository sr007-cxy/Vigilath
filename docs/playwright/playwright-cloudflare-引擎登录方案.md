# Playwright Cloudflare 引擎(ChatGPT / Gemini / Grok)登录与运行方案

> **已废弃 (2026-04-29)**：经实际部署验证，Cloudflare Turnstile 在 AWS 服务器环境下无法稳定通过（IP 信誉低 + 跨机器 fingerprint 复用失败），101 服务器上的 browser-service (global) 已下线清理。海外引擎（ChatGPT / Claude / Gemini / Grok）改为走官方 API（OpenAI / Anthropic / Perplexity）。本方案保留仅供参考，不再执行。
>
> 最后更新: 2026-04-29 · 分支 `feature/playwright`
>
> 适用范围: ChatGPT、Gemini、Grok 三个被 Cloudflare Turnstile 把守的国际引擎。
> 中文引擎(豆包、文心、千问、元宝、DeepSeek)不在本方案范围,继续沿用现状(豆包模式 = headed via Xvfb + 现有 stealth 已经跑通)。

---

## 一、背景与问题

### 1.1 现象

`venv/bin/python backend/scripts/chatgpt_login.py` 启动后:
- chatgpt.com 弹出 Cloudflare Turnstile 人机验证
- 即便手动点击复选框,验证不过,反复刷新

后续即便用 cookie 导入(Mode 3)绕过登录这一步,在服务端 runtime 跑 Playwright 时,Cloudflare 仍然会重新挑战,挑战失败,流程走不通。

### 1.2 部署目标

- **登录**: 在本地 Mac 完成(headed,人工过 Cloudflare + 输 OpenAI 密码)
- **运行**: 在 AWS EC2 上完成(Xvfb 虚拟桌面已就绪,豆包/文心已跑通)
- **数据流**: 本地登录 → 上传 storage_state → 服务器加载 → 跑 query

### 1.3 Cloudflare Turnstile 的特殊性

Cloudflare 跟 ByteDance 的 anti-bot 不是同一类:

| 维度 | ByteDance(豆包) | Cloudflare(ChatGPT/Gemini/Grok) |
|---|---|---|
| 主要检测点 | navigator.webdriver / 行为模式 / 提交节奏 | UA + Sec-CH-UA 一致性 + 浏览器指纹 |
| `cf_clearance` 类似物 | 无,每次请求都过 | 有,过一次 challenge 拿 cookie 用一段时间 |
| IP 信誉 | 弱影响 | 强影响,AWS / 机房 IP 段评分低 |
| Xvfb headed 是否够 | 够 | 不够,需要再叠 fingerprint 一致性 |

豆包跑通 = Xvfb + 现有 stealth + 行为模拟。
ChatGPT/Gemini/Grok 跑通 = 上面这一套 + **跨机器 fingerprint 一致** + **真 Chrome** + **persistent profile** + (退路) **住宅代理**。

---

## 二、现有 stack 的洞

读 `backend/browser_engine/anti_detect.py` + `browser.py` + `services/browser-service/app/engines/chatgpt_browser.py`,问题如下:

| 问题 | 位置 | 影响 |
|---|---|---|
| profile 池只有 Win32 / MacIntel,无 Linux | `anti_detect.py:14-43` | 服务器 Linux 上无论选哪个,Sec-CH-UA-Platform 头会自动写 "Linux",跟 navigator.platform 矛盾 |
| `_pick_profile()` 每次随机 | `anti_detect.py:46` | 登录用 profile A、runtime 用 profile B → cf_clearance 立刻失效 |
| `get_launch_options()` 写死 `headless=False` | `anti_detect.py:75` | `headless` 参数完全没生效,服务器侧默认 headed,本地 headless 模式无法切换 |
| `--user-agent=` 启动参数改 UA 不改 client hints | `anti_detect.py:70` | UA 字符串和 Sec-CH-UA 不同步,CF 立刻识别 |
| 每次 `new_context()` 全新 profile,无 cache/history | `browser.py:127` | CF 给"全新空白浏览器"低 trust 分 |
| 没有持久化 profile dict 到磁盘 | `session_store.py` | session 跨机器复用时 fingerprint 失配 |
| browser-service `PUT /sessions/{engine}` 只接 storage_state | `main.py:231-240` | profile 没法跟 session 一起传 |

---

## 三、Cloudflare cf_clearance 校验范围(经验值)

`cf_clearance` cookie **不强绑 IP**,但**强绑指纹**。跨机器复用时哪些维度必须对齐:

| 维度 | 跨机器变化时 | 我们能否对齐 |
|---|---|---|
| `User-Agent` 字符串 | 立刻失效 | 全程统一即可 |
| `Sec-CH-UA-Platform` HTTP 头 | 立刻失效 | Chromium 按真实 OS 写,需要 `extra_http_headers` 覆写 |
| `Sec-CH-UA` HTTP 头(brand 列表) | 大概率失效 | 同上,需要覆写 |
| `navigator.userAgentData.platform` JS 字段 | 大概率失效(CF JS challenge 读) | stealth JS 可以改 |
| WebGL renderer | 影响 challenge,不影响 cookie 校验 | stealth JS 已改 |
| Canvas 指纹 | 同上 | stealth JS 已改 |
| JA3 TLS 指纹 | 一般不影响 cookie 校验 | 不可行,需要 patchright/curl_cffi 级别工具 |
| IP / ASN | 跨国/跨 ASN 大概率失效 | Mac 住宅 → AWS 机房,跨 ASN(高风险) |
| 字体列表 | 影响 challenge,不影响 cookie 校验 | stealth JS 可加 |

**结论**: 跨机器复用 `cf_clearance` 必须做到 (UA, Sec-CH-UA, Sec-CH-UA-Platform, navigator.userAgentData) 四件套**完全一致**。本方案的核心动作就是让本地 Chromium **整体伪装成 Linux Chrome**,跟服务器侧用同一份指纹。

---

## 四、目标架构

```
┌──────────────────┐                          ┌──────────────────┐
│  本地 Mac        │                          │  AWS EC2 + Xvfb  │
│  (登录环境)      │                          │  (runtime 环境)  │
│                  │                          │                  │
│  Chromium        │                          │  Chrome(Linux)  │
│  + Linux 伪装    │  ─── upload(rsync) ──►   │  + 同一 profile  │
│  + 通过 CF       │       session.json       │  + Xvfb headed   │
│  + OpenAI 登录   │       profile.json       │  + 加载 session  │
│                  │                          │  + 跑 query      │
└──────────────────┘                          └──────────────────┘
       ▲                                              ▲
       │ 这两侧的 navigator/headers 像素级一致 ────────┘
```

### 关键设计原则

1. **本地 Chromium 全程伪装成 Linux**(UA、Sec-CH-UA-Platform、navigator.userAgentData 全部对齐 Linux)
2. **profile dict 跟 storage_state 一起序列化**,服务器加载 session 时同时读 profile,起浏览器用同一份
3. **服务器侧用真 Chrome on Linux**(`playwright install chrome`),不用 bundled Chromium
4. **服务器侧用 `launch_persistent_context`** + 持久 user_data_dir,让 CF 看到"成熟 profile",而不是每次都"全新空白"
5. **session + profile 一并通过 `PUT /sessions/{engine}` 上传**,不再只传 storage_state

---

## 五、代码改动清单

### 5.1 `backend/browser_engine/anti_detect.py`(同步到 `services/browser-service/app/anti_detect.py`)

**A. 加 Linux profile**

```python
_UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "platform": "Linux x86_64",
        "ua_platform_label": "Linux",
        "ua_brands": '"Chromium";v="135", "Not-A.Brand";v="24", "Google Chrome";v="135"',
        "vendor": "Google Inc.",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)",
    },
    # 保留 MacIntel / Win32(中文引擎本地调试用)
]
```

**B. `_pick_profile()` 接受 `platform_filter` 参数**

```python
def _pick_profile(platform_filter: str | None = None) -> dict:
    """platform_filter: "Linux x86_64" 强制走 Linux profile,确保跨机器一致。"""
    pool = _UA_PROFILES if not platform_filter else [
        p for p in _UA_PROFILES if p["platform"] == platform_filter
    ]
    return random.choice(pool) if pool else _UA_PROFILES[0]
```

**C. `get_launch_options()` 修 bug + 加 `channel` 选项**

```python
def get_launch_options(headless: bool = True, profile: dict | None = None,
                      channel: str | None = None) -> dict:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        # 删除 --user-agent= arg(由 context.user_agent 统一管理)
    ]
    opts = {"headless": headless, "args": args}  # 不再写死 False
    if channel:
        opts["channel"] = channel  # "chrome" 走 playwright install chrome 装的真 Chrome
    return opts
```

**D. `get_context_options()` 加 `extra_http_headers` 同步 client hints**

```python
def get_context_options(locale="zh-CN", timezone_id="Asia/Shanghai",
                       profile: dict | None = None) -> dict:
    if profile is None:
        profile = _pick_profile()
    return {
        "viewport": {"width": 1920, "height": 1080},
        "locale": locale,
        "timezone_id": timezone_id,
        "user_agent": profile["ua"],
        "color_scheme": "light",
        "device_scale_factor": 1.0,
        "has_touch": False,
        "java_script_enabled": True,
        # 关键:覆写 Chromium 自带的 client hints
        # Mac 上跑 Linux profile 时,这是唯一能让 Sec-CH-UA-Platform="Linux" 的方法
        "extra_http_headers": {
            "sec-ch-ua": profile.get("ua_brands", ""),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{profile.get("ua_platform_label", "Linux")}"',
        },
    }
```

**E. stealth JS 加 `navigator.userAgentData` 覆写**

CF 的 challenge JS 用 `navigator.userAgentData.getHighEntropyValues()` 交叉校验,这一项不改就跟 Sec-CH-UA-Platform 头矛盾:

```javascript
// 注入到 _build_stealth_js() 末尾
if (navigator.userAgentData) {
    Object.defineProperty(navigator.userAgentData, 'platform', {
        get: () => '{ua_platform_label}',
    });
    const _origGetHighEntropy = navigator.userAgentData.getHighEntropyValues;
    navigator.userAgentData.getHighEntropyValues = function(hints) {
        return _origGetHighEntropy.call(this, hints).then(d => {
            d.platform = '{ua_platform_label}';
            d.platformVersion = '6.5.0';
            return d;
        });
    };
}
```

`build_stealth_js_for_profile()` 增加 `ua_platform_label` 形参,从 profile 读出。

### 5.2 `backend/browser_engine/browser.py`(同步 services 副本)

**A. 新增 `create_persistent_chrome_context()`**

CF-gated 引擎专用,走 `launch_persistent_context` + `channel="chrome"`:

```python
async def create_persistent_chrome_context(
    engine_name: str,
    *,
    profile: dict,
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
):
    """For Cloudflare-gated engines: real Chrome + persistent user_data_dir.

    Each engine gets its own user_data_dir so cookies/cache/history persist
    across runs, giving CF a "mature profile" trust signal instead of a
    fresh blank browser every time.
    """
    from playwright.async_api import async_playwright
    from pathlib import Path
    import os

    user_data_dir = Path(os.environ.get(
        "BROWSER_PROFILE_DIR",
        os.path.join(os.path.dirname(__file__), "..", "data", "browser_profiles"),
    )) / engine_name
    user_data_dir.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()

    # AWS 服务器走 channel="chrome";本地登录脚本同样走 channel="chrome" 保持一致
    use_headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0"
    launch_opts = get_launch_options(
        headless=use_headless, profile=profile, channel="chrome",
    )
    ctx_opts = get_context_options(
        locale=locale, timezone_id=timezone_id, profile=profile,
    )
    # launch_persistent_context 同时是 launch + new_context,合并参数
    ctx_opts.update({k: v for k, v in launch_opts.items() if k not in ctx_opts})

    context = await pw.chromium.launch_persistent_context(
        str(user_data_dir), **ctx_opts,
    )

    # storage_state 不能直接传给 launch_persistent_context,需要事后注入
    state = load_storage_state(engine_name)
    if state:
        if state.get("cookies"):
            await context.add_cookies(state["cookies"])
        # localStorage 注入需要先开页面:见调用方处理

    await apply_stealth_to_context(context, international=True, profile=profile)
    page = await context.new_page()

    # 处理 localStorage 注入(如果 storage_state 有的话)
    if state and state.get("origins"):
        for origin in state["origins"]:
            origin_url = origin.get("origin")
            if not origin_url:
                continue
            await page.goto(origin_url, wait_until="domcontentloaded", timeout=15000)
            for item in origin.get("localStorage", []):
                await page.evaluate(
                    "([k, v]) => localStorage.setItem(k, v)",
                    [item["name"], item["value"]],
                )

    page._pw_ref = pw  # 保留引用,关闭时 pw.stop()
    return page, context
```

**B. `create_stealth_page` 不改**

中文引擎继续用现有路径,不打扰已经跑通的实现。

### 5.3 `backend/browser_engine/session_store.py`(同步 services 副本)

新增 profile 持久化:

```python
def save_engine_profile(engine_name: str, profile: dict) -> None:
    """Save fingerprint profile alongside storage_state for cross-machine reuse."""
    _ensure_dir()
    path = _SESSION_DIR / f"{engine_name}_profile.json"
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


def load_engine_profile(engine_name: str) -> dict | None:
    path = _SESSION_DIR / f"{engine_name}_profile.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
```

### 5.4 `backend/scripts/chatgpt_login.py`(gemini / grok 同改)

核心改动:
- 启动 Chromium 时**强制使用 Linux profile**(即使本地是 Mac)
- 登录完成后,把 profile dict 一起保存到磁盘
- 增加硬性检查 cf_clearance + session-token,缺一不可,缺了报错并打印诊断

```python
async def interactive_login():
    profile = _pick_profile(platform_filter="Linux x86_64")
    print(f"Cross-machine profile: UA={profile['ua'][:80]}...")
    print(f"  Sec-CH-UA-Platform will be: {profile['ua_platform_label']}")
    print("Note: this Chromium will identify itself as Linux even on macOS.")
    print("This is intentional — the same profile must work on the AWS server.\n")

    pw = await async_playwright().start()
    # 本地登录也用 channel="chrome" 保持跟服务器一致
    # 如果本地没装真 Chrome,fallback 到 bundled chromium(脚本会检测并提示)
    try:
        browser = await pw.chromium.launch(
            **get_launch_options(headless=False, profile=profile, channel="chrome")
        )
    except Exception as e:
        print(f"channel=chrome failed ({e}), falling back to bundled chromium")
        browser = await pw.chromium.launch(
            **get_launch_options(headless=False, profile=profile)
        )

    ctx = await browser.new_context(**get_context_options_international(profile=profile))
    await ctx.add_init_script(build_stealth_js_for_profile(profile, international=True))

    page = await ctx.new_page()
    await page.goto(CHAT_URL, ...)
    # ... 用户操作 ...

    state = await ctx.storage_state()
    save_storage_state(ENGINE, state)
    save_engine_profile(ENGINE, profile)  # 关键:profile 跟 session 配对落盘

    cookie_names = {c.get("name", "") for c in state.get("cookies", [])}
    has_cf = "cf_clearance" in cookie_names
    has_session = any(n.startswith("__Secure-next-auth.session-token") for n in cookie_names)
    if not (has_cf and has_session):
        print("\nERROR: session not viable.")
        print(f"  cf_clearance:                       {'OK' if has_cf else 'MISSING'}")
        print(f"  __Secure-next-auth.session-token:   {'OK' if has_session else 'MISSING'}")
        print("Server side will hit the same Cloudflare wall. Do not upload.")
        sys.exit(1)
    print("\nSession viable. Upload with:")
    print("  bash backend/scripts/upload_chatgpt_session.sh")
```

### 5.5 `services/browser-service/app/engines/chatgpt_browser.py`(gemini / grok 同改)

```python
from ..session_store import load_engine_profile

async def search(self, query: str) -> EngineResult:
    # 1. 加载登录时保存的 profile,而不是重新随机
    profile = load_engine_profile("chatgpt")
    if not profile:
        return EngineResult(
            engine=self.name, query=query,
            error="No profile saved. Run scripts/chatgpt_login.py and upload session+profile."
        )

    # 2. 服务器侧:Xvfb + 真 Chrome + persistent context
    if not os.environ.get("DISPLAY"):
        from ..xvfb import start_xvfb
        start_xvfb()

    page, ctx = await create_persistent_chrome_context(
        "chatgpt", profile=profile,
        locale="en-US", timezone_id="America/New_York",
    )

    # ... 后续 query 流程不变(_check_cloudflare / _find_input / 提交 / 解析等)

    # 关闭逻辑要走 pw.stop()
    pw = getattr(page, "_pw_ref", None)
    await ctx.close()
    if pw:
        await pw.stop()
```

`is_available()` 同样改为读 profile,同样走 persistent_context,但失败时不抛错(返回 False)。

### 5.6 `services/browser-service/app/main.py`

`PUT /sessions/{engine}` 扩展:

```python
class SessionUpload(BaseModel):
    storage_state: dict
    profile: dict | None = None  # 新增:Cloudflare 引擎必传

@app.put("/sessions/{engine}")
async def upload_session(engine: str, body: SessionUpload):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    save_storage_state(engine, body.storage_state)
    if body.profile:
        from .session_store import save_engine_profile
        save_engine_profile(engine, body.profile)
    return {"status": "ok", "engine": engine, "profile_saved": bool(body.profile)}
```

### 5.7 部署:Dockerfile + docker-compose

`backend/Dockerfile`(或专门的 `services/browser-service/Dockerfile`)增加:

```dockerfile
RUN apt-get update && apt-get install -y \
    xvfb \
    fonts-liberation fonts-noto-cjk \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 装真 Chrome(关键:CF 检测 Sec-CH-UA brand 字段)
# --with-deps 自动装 Chrome 需要的所有 Linux libs
RUN pip install playwright \
    && playwright install --with-deps chrome \
    && playwright install --with-deps chromium  # 备选 fallback
```

`docker-compose.yml` 给 browser-service 加共享内存(headed Chrome 必须):

```yaml
browser-service:
  build:
    context: ./services/browser-service
  shm_size: 2gb       # ← 加这一行
  environment:
    - DISPLAY=:99     # Xvfb 自动启
    - PLAYWRIGHT_HEADLESS=0  # CF 引擎要 headed
```

### 5.8 新增辅助脚本

**`backend/scripts/upload_chatgpt_session.sh`**:

```bash
#!/bin/bash
# Upload chatgpt session + profile to browser-service.
# Usage: BROWSER_SERVICE_URL=http://aws-server:8091 bash this.sh
set -euo pipefail

SERVER="${BROWSER_SERVICE_URL:-http://localhost:8091}"
SESSION_FILE="backend/data/browser_sessions/chatgpt.json"
PROFILE_FILE="backend/data/browser_sessions/chatgpt_profile.json"

if [[ ! -f "$SESSION_FILE" ]]; then
    echo "Missing $SESSION_FILE. Run chatgpt_login.py first."
    exit 1
fi
if [[ ! -f "$PROFILE_FILE" ]]; then
    echo "Missing $PROFILE_FILE. Re-run chatgpt_login.py (it saves profile too)."
    exit 1
fi

STATE=$(cat "$SESSION_FILE")
PROFILE=$(cat "$PROFILE_FILE")
PAYLOAD=$(jq -n --argjson s "$STATE" --argjson p "$PROFILE" \
    '{storage_state: $s, profile: $p}')

curl -fsS -X PUT "$SERVER/sessions/chatgpt" \
    -H "content-type: application/json" \
    -d "$PAYLOAD"
echo
```

**`backend/scripts/verify_chatgpt_session.py`**(可选,本地自检):

登录完后先在本地"模拟服务器环境"(强制 Linux profile + 加载刚保存的 session + 真 Chrome + 启动 persistent context)跑一次最小查询,提前发现指纹失配。

---

## 六、AWS 部署步骤

```bash
# === 服务器端(一次性) ===

# 1. SSH 到 AWS EC2
ssh aws-server
cd /opt/geo

# 2. 拉新代码(包含本方案改动)
git pull

# 3. 重建 browser-service 镜像(包含 playwright install chrome)
docker compose build browser-service

# 4. 启动
docker compose up -d browser-service

# 5. 确认 Chrome 装好 + Xvfb 起来
docker compose exec browser-service ls ~/.cache/ms-playwright/ | grep chrome
curl -s http://aws-server:8091/debug/env  # 看 DISPLAY 是否非空、xvfb_running=true

# === 本地登录 + 上传 ===

# 6. 本地装真 Chrome(Mac 上自带 /Applications/Google Chrome.app 即可,
#    或者 venv 里跑 playwright install chrome 拉 playwright 版)
venv/bin/playwright install chrome  # 推荐:跟服务器版本严格一致

# 7. 登录 ChatGPT
venv/bin/python backend/scripts/chatgpt_login.py
# → 浏览器打开,过 CF + 输 OpenAI 密码 + Enter
# → 脚本输出 "Session viable. Upload with: ..."

# 8. 上传 session + profile 到服务器
BROWSER_SERVICE_URL=http://aws-server:8091 \
    bash backend/scripts/upload_chatgpt_session.sh

# === 服务器端验证 ===

# 9. 服务器侧跑一个 smoke test
curl -X POST http://aws-server:8091/search \
    -H "content-type: application/json" \
    -d '{"engine": "chatgpt", "query": "what is the weather in NYC"}'

# 10. 看日志
docker compose logs -f browser-service | grep -i chatgpt
# 期望:无 "Blocked by Cloudflare" / "Not logged in" 报错,answer 字段非空
```

Gemini / Grok 重复 6-9 步,引擎名替换。

---

## 七、风险评估与退路

| 风险 | 概率 | 退路 |
|---|---|---|
| AWS IP 被 CF 直接 block(不论指纹) | 30%(AWS 段评分参差) | **加住宅代理**:Bright Data / Oxylabs,代码层 `get_launch_options()` 加 `proxy={"server": "..."}` |
| `extra_http_headers` 没生效(Chromium 自带 hints 优先) | 20% | 实测 Playwright 行为;失效则改用 `--user-agent-client-hints-*` 启动 flag,或换 patchright |
| `cf_clearance` 跨机器复用失败 | 30% | 服务器侧自己重新跑 challenge(Xvfb headed + Linux profile 已经在);最坏退路 = 服务器侧 + VNC 远程登录 |
| OpenAI session-token 跨机器失效 | 5%(OpenAI 不强绑设备) | 重新登录 |
| 真 Chrome 在 AWS 容器里跑不起来(缺 lib) | 10% | `playwright install --with-deps chrome` 通常解决,backup 走 bundled chromium |

### 住宅代理预留接口(默认不接)

`get_launch_options()` 改造成读环境变量:

```python
proxy_url = os.environ.get("PROXY_URL", "").strip()
if proxy_url:
    opts["proxy"] = {"server": proxy_url}
    # PROXY_URL 形如 "http://user:pass@residential.example.com:8080"
```

切换时只需 `docker compose` 加 `PROXY_URL` 环境变量,无需改代码。

### 最坏情况退路:服务器侧自己登录

若本地登录的 cf_clearance 在服务器上反复失效:
1. 服务器开 Xvfb headed + 启动 noVNC / x11vnc(轻量,~30MB)
2. 用户用浏览器远程访问 noVNC,在服务器自己的浏览器里过 CF + 登 OpenAI
3. profile 直接在服务器侧 `_pick_profile(platform_filter="Linux x86_64")`,fingerprint 100% 一致

代码层无大改动,主要是部署多一个 noVNC 容器。

---

## 八、执行顺序

| 阶段 | 内容 | 验证标准 |
|---|---|---|
| **P1** | `anti_detect.py` / `browser.py` / `session_store.py` 改动(加 Linux profile + extra_http_headers + profile 持久化 + create_persistent_chrome_context) | 单元自检:`_pick_profile("Linux x86_64")` 拿到 Linux profile;`save_engine_profile` 落盘 |
| **P2** | `chatgpt_login.py` + `chatgpt_browser.py` 改造 | 本地登录拿到 cf_clearance + session-token,profile.json 落盘;**本地 verify 脚本通过**(在 Mac 上模拟 Linux 跑一次 query 成功) |
| **P3** | Dockerfile 装 Chrome + Xvfb + 部署 AWS | `/debug/env` 看到 DISPLAY、Chrome 二进制路径 |
| **P4** | 上传 session 到 AWS,服务器跑 ChatGPT query | 答案正常返回,无 CF challenge 报错 |
| **P5** | 同样改动复制到 gemini / grok | 三个引擎都跑通 |
| **P6** | (可选)加住宅代理配置入口 | 环境变量 `PROXY_URL` 可切换 |

**估时**: P1-P2 半天,P3-P4 半天调通(主要踩 Docker 坑),P5 复制改动 2 小时,总共约 1 个工作日,±半天看 AWS IP 运气。

---

## 九、对接现有代码的注意事项

### 9.1 不破坏中文引擎

豆包、文心、千问、元宝、DeepSeek 完全不动。
- `create_stealth_page()` 保持原样
- `_pick_profile()` 不传 `platform_filter` 时行为跟现在一致(随机 Win32/MacIntel/Linux)
- 中文引擎不读 `*_profile.json`,继续用 storage_state-only 模式

### 9.2 三份同源 anti_detect.py / browser.py

仓库目前有:
- `backend/browser_engine/anti_detect.py` + `browser.py`(active)
- `services/browser-service/app/anti_detect.py` + `browser.py`(active,跟前者字节相同)

提交时**两边一起改**,跟 `62aaca0 chore(backend/browser_engine): sync browser.py and anti_detect.py from services/` 这次同步保持节奏。

### 9.3 已有的 `playwright-stealth` 依赖

`services/browser-service/pyproject.toml` 列了 `playwright-stealth>=1.0.6`,但 `browser.py` 注释明确说"不再用 playwright-stealth"(它的随机 profile 选择会破坏一致性)。

本方案延续这个决定 — 不引入 playwright-stealth,不引入 patchright(除非 P3-P4 实测发现必须)。

---

## 十、验收清单

### 本地登录环节

- [ ] `chatgpt_login.py` 启动后浏览器 UA 是 Linux x86_64 形态
- [ ] DevTools console `navigator.userAgentData.platform` 返回 `"Linux"`
- [ ] DevTools network 任意请求的 `Sec-CH-UA-Platform` 头 = `"Linux"`
- [ ] CF Turnstile 通过(单次点击复选框即可)
- [ ] 登录后页面正常进入 chatgpt.com 主界面
- [ ] `backend/data/browser_sessions/chatgpt.json` 含 `cf_clearance` cookie
- [ ] `backend/data/browser_sessions/chatgpt.json` 含 `__Secure-next-auth.session-token`
- [ ] `backend/data/browser_sessions/chatgpt_profile.json` 存在且 platform = `Linux x86_64`

### 上传 + 服务器加载

- [ ] `PUT /sessions/chatgpt` 返回 `{"status": "ok", "profile_saved": true}`
- [ ] 服务器 `backend/data/browser_sessions/chatgpt_profile.json` 落盘
- [ ] 服务器 `/debug/env` 报 DISPLAY 非空、xvfb_running=true、Chrome 二进制存在

### 服务器跑 query

- [ ] `POST /search {"engine":"chatgpt","query":"..."}` 不返回 "Blocked by Cloudflare"
- [ ] 不返回 "Not logged in — chatgpt.com redirected to login"
- [ ] `answer` 字段非空,内容跟手动 chatgpt.com 查询近似
- [ ] `citations` 数量 > 0(查询本身需要联网搜索时)
- [ ] 同一 session 连续跑 5 次 query 都成功,无 challenge 重发

### gemini / grok

- [ ] gemini 重复上述全部清单
- [ ] grok 重复上述全部清单

---

## 十一、未尽事项 / 后续

- **session 自动续期**: 当前 cf_clearance 过期后只能手动重登。后续可加定时任务(`schedule` skill)每 24 小时自动重新登录上传。
- **多账号轮换**: 为反封禁,后续可同时持有多个 OpenAI 账号 session,每次 query 随机挑一个。代码层 `load_storage_state` / `load_engine_profile` 接受 `account_id` 参数即可。
- **patchright 评估**: 若 P3-P4 实测 cf_clearance 跨机器复用率 < 50%,引入 `pip install patchright` 替换 `playwright`(API 兼容)。代码改动量 = 2 个 import 行。
- **TLS JA3**: 极端情况下 CF 可能开始校验 JA3,届时 Playwright 这条路就走不通,需要换 `curl_cffi` + 完全自研 ChatGPT API 客户端,代价非常高。
