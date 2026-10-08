from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Site region — the same code, with each server's .env setting REGION=global / REGION=cn.
    # Regional differences (which engines/payments/compliance/default locale are enabled) all converge under this switch,
    # no long-lived branches. Check with `settings.is_cn` / `settings.is_global`.
    REGION: str = "global"  # "global" (global site) | "cn" (CN site)

    # Master gate for agency organization management and workbench. Backend capabilities deploy first and are explicitly enabled after pilot acceptance.
    AGENCY_WORKBENCH_ENABLED: bool = False

    # USD → CNY conversion rate. Tier prices are stored in USD in the DB (memberships.currency='usd');
    # the CN site's displayed prices and the WeChat Pay amount charged are both converted by this one constant, ensuring "what you see =
    # what the QR payment charges". Change the rate only here (or USD_TO_CNY_RATE in .env).
    USD_TO_CNY_RATE: float = 7.2

    DATABASE_URL: str = "sqlite:///./data/geo_checker.db"
    GOOGLE_CLIENT_ID: str = "test-google-client-id"
    SECRET_KEY: str = "your-secret-key-for-jwt"

    # Resend transactional email (password reset, consultation ack, etc.)
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "GEO Readiness Checker <noreply@vigilath.com>"
    FRONTEND_URL: str = "https://www.vigilath.com"
    # Inbox that receives /contact + /contact-sales submission notifications.
    SALES_NOTIFY_EMAIL: str = "support@zen7.com"

    # Stripe (used for overseas / English-locale credit card subscriptions)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_SUCCESS_URL: str = "http://localhost:5173/checkout/success"
    STRIPE_CANCEL_URL: str = "http://localhost:5173/checkout/cancel"
    STRIPE_TEST_MODE: bool = False

    # MoltsPay (USDC via x402 protocol)
    MOLTSPAY_ENABLED: bool = False
    MOLTSPAY_WALLET_ADDRESS: str = ""
    MOLTSPAY_CHAIN: str = "base"
    MOLTSPAY_SERVER_URL: str = "http://127.0.0.1:3010"

    # WeChat Pay (WeChat Pay Native QR payment)
    WECHAT_PAY_ENABLED: bool = False
    WECHAT_PAY_APP_ID: str = ""             # Official account/mini program AppID
    WECHAT_PAY_MCH_ID: str = ""             # Merchant ID
    WECHAT_PAY_API_KEY_V3: str = ""         # APIv3 key (32 bytes)
    WECHAT_PAY_CERT_SERIAL: str = ""        # Merchant certificate serial number
    WECHAT_PAY_PRIVATE_KEY_PATH: str = ""   # Path to the merchant private key apiclient_key.pem
    WECHAT_PAY_NOTIFY_URL: str = ""         # Payment result callback notification URL
    # Official account AppSecret — JSAPI payment (WeChat built-in browser) relies on it for web authorization to get openid.
    # Found under Official Account Platform > Development > Basic Configuration. Empty = JSAPI channel unavailable, falls back to QR automatically.
    WECHAT_MP_APP_SECRET: str = ""

    # ── WeChat official account QR login (follow the official account → bind phone number) ────────────────────
    # Uses "temporary QR codes with parameters": backend creates scene → frontend renders the QR code → user scans and follows
    # → the official account pushes subscribe / SCAN events to WECHAT_MP_CALLBACK_PATH →
    # frontend polls for bind_token → SMS verifies the phone number → JWT issued.
    # The temporary QR code API requires a "verified service account"; subscription accounts lack this permission.
    WECHAT_LOGIN_ENABLED: bool = False
    # AppID of the login official account. Only falls back to WECHAT_PAY_APP_ID when empty — omit only when payment and login
    # really use the same account.
    WECHAT_MP_APP_ID: str = ""
    # AppSecret of the login official account. **Must be set explicitly when the payment and login accounts differ**,
    # otherwise the payment account's secret is used to get the login account's token, WeChat returns 40001, and the error looks a lot like
    # "config not taking effect". Empty only falls back to WECHAT_MP_APP_SECRET in the single-account case.
    WECHAT_LOGIN_APP_SECRET: str = ""
    # Token / EncodingAESKey under Official Account Platform > Settings & Development > Basic Configuration > Server Configuration.
    # Empty AES Key = plaintext mode; when set, decrypt in secure mode (compatible mode accepts both).
    WECHAT_MP_TOKEN: str = ""
    WECHAT_MP_AES_KEY: str = ""
    # QR code validity (seconds). WeChat's max is 2592000; shorter is safer for login.
    WECHAT_QR_EXPIRE_SECONDS: int = 300
    # Welcome message the official account replies after a successful scan (empty = no reply)
    # Website PC QR login: replied when the phone follows/scans a parameterized QR code (matching a login session),
    # guiding the user back to the PC web page to finish login. **Login scenario only**; do not use it as the follow welcome message.
    WECHAT_LOGIN_WELCOME_TEXT: str = "扫码成功,请回到网页继续完成登录。"
    # Acquisition follow: invitation replied when following via menu/card code/search (no login scene);
    # {link} is replaced with the landing page URL and rendered by WeChat as a clickable link. Empty = no reply.
    # The official account targets China, so the engines named are the ones CN users actually use — not ChatGPT / Perplexity
    # (CN users do not use them; mentioning them makes it look not built for them).
    WECHAT_SUBSCRIBE_WELCOME_TEXT: str = (
        "欢迎关注 Vigilath 👋\n\n"
        "点这里,免费给你的品牌做一次 AI 可见性检测、拿到全量报告 —— "
        "看看豆包、DeepSeek、通义千问、文心一言、元宝这些 AI 搜索会不会推荐你:\n{link}"
    )

    # ── H5 single-device channel (field promotion: scan to follow on the phone → tap the official account menu → authorize → bind phone number) ──
    # The QR flow above is a two-device model ("PC shows code + phone scans"); field promotion has only one phone on site,
    # so it does not work (bind_token only comes from PC polling). This one uses official account web authorization instead:
    # the menu opens the landing page → snsapi_base silently gets openid → identify the user directly or bind a phone number.
    #
    # Base URL of the landing page site (with scheme, no trailing slash). Its **domain** must be on both the
    # official account platform's "web authorization domains" and "JS API secure domains" whitelists, otherwise WeChat reports
    # a redirect_uri parameter error at the redirect step and the page never opens. Empty falls back to FRONTEND_URL
    # — but the CN official account uses vigilath.cn, a different domain from the global site, so this almost always needs to be set.
    WECHAT_H5_BASE_URL: str = ""
    # Path the "start audit" item in the official account's bottom menu points to. The script scripts/setup_wechat_menu.py
    # writes BASE_URL + this path into the menu.
    # WeChat authorization return destination + target of the official account menu "free audit". No dedicated landing page; the home page is used directly
    # (it already has an audit entry); the authorization return is caught on the home page by the site-wide guard WechatSilentAuth to identify the user.
    WECHAT_H5_LANDING_PATH: str = "/"
    # Page the field-promotion entry (official account menu "free audit" + follow invitation link) points to. Separate from the return destination
    # (WECHAT_H5_LANDING_PATH, fixed to the home page for code exchange): the destination must be light and login-free,
    # while the field-promotion entry should take people straight to the audit page. Default /app/diagnosis.
    WECHAT_PROMO_PATH: str = "/app/diagnosis"

    # ── SMS verification code channel (phone binding / phone login) ────────────────────────
    # Empty credentials = SMS channel not enabled; the phone binding endpoint returns 503 directly (never silently allows).
    # SMS_PROVIDER selects the channel: aliyun (default, since 2026-09) / tencent (old channel, kept for switching back).
    # Both share the same pair of signature and template fields; switching channels only changes SMS_PROVIDER + credentials + these two.
    SMS_PROVIDER: str = "aliyun"
    SMS_SIGN_NAME: str = ""
    SMS_TEMPLATE_ID: str = ""       # Aliyun SMS_xxx / Tencent Cloud numeric template ID
    # International/HK-Macau-Taiwan numbers (a non +86 country selected on the login page) use this pair; both vendors require separate filing
    # of signatures and templates for international SMS; if unset, fall back to the pair above — whether it sends is up to the provider, and failures are reported to the user as-is.
    SMS_INTL_SIGN_NAME: str = ""
    SMS_INTL_TEMPLATE_ID: str = ""
    # Aliyun: dysmsapi.aliyuncs.com; test host's NO_PROXY already includes .aliyuncs.com.
    # SMS prefers the SMS_ALIYUN_* pair (a sub-account granted only AliyunDysmsFullAccess); only if unset does it fall back to
    # the ALIBABA_CLOUD_* pair of compliance external moderation (external_moderation). The two businesses' credentials are independent,
    # so rotating/revoking one pair does not break the other.
    SMS_ALIYUN_ACCESS_KEY_ID: str = ""
    SMS_ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIBABA_CLOUD_ACCESS_KEY_ID: str = ""
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: str = ""
    SMS_ALIYUN_REGION: str = "cn-hangzhou"
    # Aliyun template variables are named, mapped in order to [code, valid minutes]. Default is only ${code}
    # (the console's built-in verification template looks like this); if the template also has minutes, set "code,min".
    SMS_TEMPLATE_PARAM_NAMES: str = "code"
    # Tencent Cloud (old): sms.tencentcloudapi.com; test host deployments must add .tencentcloudapi.com to NO_PROXY.
    # Template parameters are positional {1}=code {2}=valid minutes.
    TENCENT_SECRET_ID: str = ""
    TENCENT_SECRET_KEY: str = ""
    SMS_REGION: str = "ap-guangzhou"
    SMS_APP_ID: str = ""            # SdkAppId, starts with 1400
    SMS_CODE_TTL_SECONDS: int = 300
    SMS_RESEND_INTERVAL_SECONDS: int = 60   # Minimum interval between two sends to the same number
    SMS_DAILY_LIMIT_PER_PHONE: int = 10
    SMS_DAILY_LIMIT_PER_IP: int = 30
    # Hourly IP gate. The daily limit of 30 cannot stop "bombing 30 numbers within an hour", and the phone login
    # send-code endpoint is public (no bind_token guard), so a short-window limit is needed too.
    SMS_HOURLY_LIMIT_PER_IP: int = 8
    # Daily limit counted by WeChat openid (for the H5 channel). A field-promotion booth shares one egress IP, and per-IP limits
    # would block everyone collectively from the 9th person on; openid is a strong identity only obtainable after web authorization, so it is used as the key.
    # Tighter than per-phone: per-phone stops "one number being bombed", this one stops
    # "one person sending one message each to a bunch of strangers' numbers".
    SMS_DAILY_LIMIT_PER_IDENTITY: int = 5
    # Debug switch: when on, codes are only logged, no real SMS sent (for local integration; must be off in production)
    SMS_DEBUG_ECHO: bool = False

    # Phone number + SMS code login/registration. A number that does not exist registers, an existing one logs in.
    # A separate channel from WeChat QR: until the service account is approved, this is the only way to register.
    PHONE_LOGIN_ENABLED: bool = True

    # Master gate for email registration. When off, /api/auth/register only accepts admin tokens (workbench creating accounts for customers),
    # and anonymous registration always gets 403 — registration goes through WeChat QR. Email **login** is unaffected.
    EMAIL_REGISTER_ENABLED: bool = False

    # AI engine keys (paid detection modes — entity / ai-visibility / citation-check).
    # Read by both the backend `geo` package and the standalone CLI tool spawned
    # as a subprocess; declared here so pydantic-settings won't drop them as
    # `extra` and so callers can use either `settings.OPENAI_API_KEY` or
    # `os.environ.get('OPENAI_API_KEY')` (systemd EnvironmentFile injects them
    # into the process environment).
    OPENAI_API_KEY: str = ""
    PERPLEXITY_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Mediumsly internal publish API. See backend/.env.example for the full
    # writeup. Empty token disables the publisher (publish flow degrades
    # gracefully — doc.mediumsly_last_error gets the reason).
    MEDIUMSLY_API_URL: str = "https://www.mediumsly.com"
    MEDIUMSLY_INTERNAL_API_TOKEN: str = ""
    MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST: str = ""  # comma-separated

    # Meijiexing agency publishing API (docs/04-开发实现/内容与发布/媒体API/产品文档-媒介星发文对接.md).
    # Empty key = channel not enabled; the client refuses to send any request.
    MJX_API_BASE: str = "https://www.2016ruanwen.com"
    MJX_SECRET_ID: str = ""
    MJX_SECRET_KEY: str = ""
    MJX_PUBLISH_ENABLED: bool = False  # Independent gate for real ordering, dual gate with AGENT_ALLOW_EXTERNAL_PUBLISH

    # Ruanwenjiang paid sponsored-article channel (docs/04-开发实现/研发计划/M2-软文匠通道接入计划.md / 发文对接.md).
    # Empty credentials = channel not enabled; RWJ_PUBLISH_ENABLED is off by default — only draft pushing (free) is allowed,
    # real ordering (/Article.{Type}/add, actually charged) requires explicitly opening the gate, following the MJX dual-gate pattern.
    # Note: tenant.meijieku.com is a CN API; add it to NO_PROXY for test host deployments.
    RWJ_API_BASE: str = "https://tenant.meijieku.com/api"
    RWJ_MOBILE: str = ""
    RWJ_PASSWORD: str = ""
    RWJ_TOKEN_PATH: str = "data/rwj_tokens.json"
    RWJ_PUBLISH_ENABLED: bool = False
    # How many media can be selected at once (shared cap for draft pushing / batch ordering). Each media needs a live price check
    # (paged matching, up to 5 list requests), so a larger number noticeably lengthens a single request.
    RWJ_MULTI_MAX: int = 10
    # AI automatic media selection (services/media_select.py): after approval, automatically pick Ruanwenjiang media by attribution recommendations,
    # push drafts and create tasks; only up to planned, real charging still requires manual second confirmation
    RWJ_AUTO_SELECT_ENABLED: bool = True
    RWJ_AUTO_PRICE_CAP_YUAN: int = 20   # Unit price cap for automatic media selection (yuan)
    # Daily media resource library sync (services/rwj_media_sync.py). Type order is priority —
    # Website / Wemedia are the two types that can carry GEO content and the only orderable ones, so they come first,
    # getting the most important ones even if later ones are blocked by risk control.
    RWJ_SYNC_ENABLED: bool = True
    RWJ_SYNC_TYPES: str = "Website,Wemedia,Shortvideo,Question,Ghostwrite"
    RWJ_SYNC_PAGE_SIZE: int = 50        # Platform cap (only 10/20/30/40/50 supported)
    RWJ_SYNC_PAGE_SLEEP: float = 1.2    # Base interval between pages (seconds), actual with ±30% jitter
    # The sync process uses a separate token file: refresh() rotates both token and refreshToken,
    # and sharing with the web process would knock out each other's login sessions, causing pointless password-login storms.
    RWJ_SYNC_TOKEN_PATH: str = "data/rwj_tokens_sync.json"
    # How to bind Ruanwenjiang resources at scheduling time: select = write the chosen resource into the plan row but **do not push drafts** (default);
    # draft = push drafts on approval (free, but 150 accumulate per month and Ruanwenjiang has no cleanup entry).
    RWJ_PLAN_BIND_MODE: str = "select"
    RWJ_SYNC_MISS_THRESHOLD: int = 2    # Rounds of full sync without seeing it before judging delisted (prevents false kills from paging drift)
    RWJ_REF_PRICE_STALE_HOURS: int = 36  # Beyond this age the reference price is marked "possibly outdated" in the frontend
    # Published link liveness checks + automatic appeals (services/rwj_liveness.py). The platform's default article validity is
    # 30 days; within it, links that fail to open / deleted content can get an after-sales refund, after the window the platform does not accept it.
    # Appeals are external actions and false reports damage account reputation, so **by default only check, no appeal**; opening the gate requires explicit config.
    RWJ_LIVENESS_ENABLED: bool = True
    RWJ_APPEAL_ENABLED: bool = False
    RWJ_APPEAL_WINDOW_DAYS: int = 30    # Platform after-sales acceptance window (default article validity)
    RWJ_LIVENESS_STRIKES: int = 2       # Consecutive dead verdicts before appealing (a single network glitch does not count)
    RWJ_LIVENESS_RECHECK_HOURS: int = 6  # Minimum interval between two checks
    RWJ_LIVENESS_TIMEOUT: float = 20.0
    # Plain HTTP fetching of CN media sites is widely blocked by anti-scraping (first round: 63 of 95 undeterminable);
    # undeterminable ones are rendered once more with headless chromium; degrades automatically when playwright is not installed.
    RWJ_LIVENESS_BROWSER: bool = True

    # Meijieku (vip.meijieku.com / api.meijieku.com) — Ruanwenjiang's upstream site, with an isomorphic protocol,
    # sharing rwj_client's SiteSpec mechanism, just one more set of credentials. Resource pools share a source but prices follow each one's membership
    # level; both sync into rwj_media_resources (distinguished by the provider column) for price comparison.
    # **The charging gate reuses RWJ_PUBLISH_ENABLED**, no separate one — one switch controls all paid publishing outbound points.
    # api.meijieku.com is also a CN API; add it to NO_PROXY for test host deployments.
    MJK_API_BASE: str = "https://api.meijieku.com"
    MJK_MOBILE: str = ""
    MJK_PASSWORD: str = ""
    MJK_TOKEN_PATH: str = "data/mjk_tokens.json"
    MJK_SYNC_TOKEN_PATH: str = "data/mjk_tokens_sync.json"
    # Site scope of resource mirror sync (comma-separated providers); sites without credentials are skipped automatically
    RWJ_SYNC_PROVIDERS: str = "rwj,mjk"

    # aitoearn multi-platform distribution channel (publish via own authorized accounts, free, no charging gate).
    # Empty key = channel not enabled; accounts are authorized in the aitoearn back office (https://aitoearn.cn Settings → API key).
    # aitoearn.cn is a CN API; add it to NO_PROXY for test host deployments.
    AITOEARN_API_BASE: str = "https://aitoearn.cn"
    AITOEARN_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def is_cn(self) -> bool:
        return self.REGION.strip().lower() == "cn"

    @property
    def is_global(self) -> bool:
        return not self.is_cn

settings = Settings()

_is_sqlite = "sqlite" in settings.DATABASE_URL
# Connection pool configured by env (small pool per worker when geo-agent runs multiple workers, to avoid exhausting PG max_connections=100):
#   DB_POOL_SIZE / DB_MAX_OVERFLOW default 5 / 10 (main backend sets no env → original behavior kept).
#   geo-agent.service sets DB_POOL_SIZE=3 / DB_MAX_OVERFLOW=5 → cap 8 per worker, 64 for 8 workers.
if _is_sqlite:
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # lock_timeout (a fallback added after the 2026-09-21 production incident):
    #   the prompt-expansion coroutine held a FOR UPDATE lock on a users row while waiting on the LLM, and concurrent requests from the same account
    #   inserting into operation_contexts needed a KEY SHARE lock on the same row, queuing indefinitely in PG — and that
    #   INSERT ran on the event loop thread, freezing the whole uvicorn for 50 minutes until a manual restart.
    #   With lock_timeout, the same wait becomes a 500 after 15s + a psycopg
    #   LockNotAvailable in the log; the process stays alive and other requests proceed. **It does not fix the root cause** (holding a lock across a network call,
    #   see the comment on suggest_queries_stream in api/ai_telemetry.py); it only downgrades "the whole site frozen" to
    #   "one request errors". Default 15s: all normal FOR UPDATE / pg_advisory_xact_lock waits in the business
    #   are sub-second, and publish claiming uses skip_locked and never waits; waiting 15s means something is definitely wrong.
    #   0 = unlimited (psycopg options are passed through to PG, effective per connection).
    _lock_timeout_ms = int(os.environ.get("DB_LOCK_TIMEOUT_MS", "15000") or 0)
    _pg_connect_args = (
        {"options": f"-c lock_timeout={_lock_timeout_ms}"} if _lock_timeout_ms > 0 else {}
    )
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_pg_connect_args,
    )


# ---------------------------------------------------------------------------
# SQLite pragma tuning
# ---------------------------------------------------------------------------
# Applied to every new connection opened by the engine. No-op for non-SQLite
# backends. The targets:
#
#   journal_mode=WAL      — readers don't block writers and vice versa;
#                           journal_mode is persistent (written once into
#                           the DB file), setting on every open is idempotent
#   synchronous=NORMAL    — fsync on transaction boundary instead of every
#                           write; 2-3x faster writes. A power cut may lose
#                           the last few milliseconds of uncommitted data —
#                           acceptable for this workload (auth / quota /
#                           payment sessions are re-submitted client-side).
#   cache_size=-64000     — 64 MB page cache (default 2 MB). The whole DB
#                           (~1.6 MB) fits here; every subsequent query is
#                           RAM-speed.
#   temp_store=MEMORY     — sort / group-by / view materialization uses RAM
#                           instead of /tmp files.
#   mmap_size=256MB       — read path uses memory-mapped I/O, skipping one
#                           kernel → userland copy per page read.
if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _connection_record):
        # Only touch real SQLite connections — this listener fires for every
        # Engine in the process, so guard by class name to avoid clobbering
        # a future mixed setup.
        if type(dbapi_conn).__module__ != "sqlite3":
            return
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA cache_size=-64000")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA mmap_size=268435456")
        finally:
            cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
