from dotenv import load_dotenv
load_dotenv()

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from jose import JWTError
from geo.api import geo, auth, membership, oauth, contact, payment, moltspay_payment, advanced, account
from geo.utils.error_handler import global_exception_handler, AppException
from geo.utils.request_log import configure_request_log

_timing_logger = logging.getLogger("geo.timing")

# Per-request JSONL log for /api/check* — 独立于 geo.timing 的文件,
# 每个 API 请求一行,含 user / tier / url / duration / status / score。
# 日志路径 backend/logs/requests.jsonl(运行时 CWD = /backend)。
configure_request_log()

app = FastAPI(
    title="GEO Readiness Checker API",
    description="API for checking website readiness for Generative Engine Optimization (GEO)",
    version="0.1.0"
)

# CORS. IMPORTANT: do not combine `allow_origins=["*"]` with
# `allow_credentials=True` — starlette's CORSMiddleware silently drops the
# Access-Control-Allow-Origin header in that case (the CORS spec forbids
# wildcard + credentials). Symptom: browser fetch gets "No
# 'Access-Control-Allow-Origin' header is present" even though the endpoint
# itself works fine from curl. Fix: enumerate the dev origins explicitly,
# and use `allow_origin_regex` as a safety net for LAN IPs during testing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8070",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 全局异常处理器。注意:在 Starlette 里 `Exception` 基类会被 ServerErrorMiddleware
# 拦截并在调用 handler 之后仍然 `raise exc`,导致 uvicorn 把 AppException(例如 429
# 配额用尽、402 会员门槛)当作"未捕获异常"记录为 ERROR 栈。业务异常的正确归宿
# 是 ExceptionMiddleware —— 这一层只会处理**显式注册**的异常类,并且成功处理后
# 不会 re-raise。所以下面每个业务异常都要单独注册。
app.add_exception_handler(AppException, global_exception_handler)
app.add_exception_handler(RequestValidationError, global_exception_handler)
app.add_exception_handler(JWTError, global_exception_handler)
# `Exception` 基类作为最后兜底,只有真正没预料到的错误才会走到这里。
app.add_exception_handler(Exception, global_exception_handler)

# Include API routes
app.include_router(geo.router, prefix="/api", tags=["geo"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(membership.router, prefix="/api", tags=["membership"])
app.include_router(oauth.router, prefix="/api/oauth", tags=["oauth"])
app.include_router(contact.router, prefix="/api", tags=["contact"])
app.include_router(payment.router, prefix="/api/payment", tags=["payment"])
app.include_router(moltspay_payment.router, prefix="/api/payment", tags=["moltspay"])
app.include_router(advanced.router, prefix="/api", tags=["advanced"])
app.include_router(account.router, prefix="/api/account", tags=["account"])

# Endpoint-level timing. Only logs /api/check* to keep noise down — other
# endpoints (auth, usage polling, account pages) are fast and not interesting
# for perf analysis. Also stamps the response with X-Process-Time in ms so
# the browser network tab / curl can see it even without tail'ing logs.
@app.middleware("http")
async def _log_check_timing(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    response.headers["X-Process-Time"] = str(elapsed_ms)
    path = request.url.path
    if path.startswith("/api/check"):
        _timing_logger.info(
            f"http method={request.method} path={path} "
            f"status={response.status_code} elapsed_ms={elapsed_ms}"
        )
    return response


@app.get("/")
async def root():
    return {"message": "GEO Readiness Checker API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
