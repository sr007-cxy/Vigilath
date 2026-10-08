from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from jwt import PyJWTError as JWTError
from sqlalchemy.exc import SQLAlchemyError
import logging

from geo.utils.error_i18n import localize

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _request_lang(request: Request) -> str:
    """Best-effort language pick for outgoing error messages.

    Prefers an explicit `X-Locale` header (set by the frontend API clients)
    and falls back to the browser's `Accept-Language`. The value is passed
    verbatim to `localize()`, which handles normalization and unknown locales.
    """
    return (
        request.headers.get("x-locale")
        or request.headers.get("accept-language")
        or "en"
    )

# Unified error response model
class ErrorResponse:
    def __init__(self, code: int, message: str, details: dict = None, key: str = None):
        self.code = code
        self.message = message
        self.details = details
        self.key = key
    
    def dict(self):
        response = {
            "error": {
                "code": self.code,
                "message": self.message
            }
        }
        if self.key:
            response["error"]["key"] = self.key
        if self.details:
            response["error"]["details"] = self.details
        return response

# Custom exception classes
class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        details: dict = None,
        key: str = None,
    ):
        self.status_code = status_code
        self.message = message
        self.details = details
        self.key = key
        super().__init__(self.message)

# Global exception handling
async def global_exception_handler(request: Request, exc: Exception):
    # Log level differs by exception type:
    # - AppException / RequestValidationError / JWTError are expected business/validation failures;
    #   info is enough, avoiding full stack traces in logs on every 402/429/401 that cause misjudgment.
    # - Everything else is a genuinely unknown exception; err + exc_info is kept for ops troubleshooting.
    if isinstance(exc, (AppException, RequestValidationError, JWTError)):
        logger.info(f"Handled business exception: {type(exc).__name__}: {exc}")
    else:
        logger.error(f"Unhandled exception: {exc}", exc_info=True)

    lang = _request_lang(request)

    # Handle different exception types
    if isinstance(exc, AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.status_code,
                message=localize(exc.message, lang),
                details=exc.details,
                key=exc.key,
            ).dict()
        )
    elif isinstance(exc, RequestValidationError):
        # Handle request parameter validation errors
        details = []
        for error in exc.errors():
            details.append({
                "field": error["loc"],
                "message": error["msg"],
                "type": error["type"]
            })
        # A generic "request parameter validation failed" can't be debugged -- include the first failing field and reason in the message
        message = localize("Validation error", lang)
        if details:
            first = details[0]
            loc = [str(x) for x in (first["field"] or []) if str(x) != "body"]
            fld = loc[-1] if loc else ""
            zh = not str(lang or "").lower().startswith("en")
            if "email" in fld or "email" in str(first["type"]):
                hint = "邮箱格式不正确" if zh else "invalid email format"
            else:
                hint = str(first["message"])
            message = f"{message}:{fld} {hint}".strip()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=message,
                details=details
            ).dict()
        )
    elif isinstance(exc, JWTError):
        # Handle JWT errors
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                code=status.HTTP_401_UNAUTHORIZED,
                message=localize("Invalid or expired token", lang)
            ).dict(),
            headers={"WWW-Authenticate": "Bearer"}
        )
    elif isinstance(exc, SQLAlchemyError):
        # Handle database errors
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=localize("Database error", lang)
            ).dict()
        )
    else:
        # Handle other unexpected errors
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=localize("Internal server error", lang)
            ).dict()
        )
