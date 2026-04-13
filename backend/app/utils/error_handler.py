from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from jose import JWTError
from sqlalchemy.exc import SQLAlchemyError
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 统一错误响应模型
class ErrorResponse:
    def __init__(self, code: int, message: str, details: dict = None):
        self.code = code
        self.message = message
        self.details = details
    
    def dict(self):
        response = {
            "error": {
                "code": self.code,
                "message": self.message
            }
        }
        if self.details:
            response["error"]["details"] = self.details
        return response

# 自定义异常类
class AppException(Exception):
    def __init__(self, status_code: int, message: str, details: dict = None):
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(self.message)

# 全局异常处理
async def global_exception_handler(request: Request, exc: Exception):
    # 记录异常信息
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    # 处理不同类型的异常
    if isinstance(exc, AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.status_code,
                message=exc.message,
                details=exc.details
            ).dict()
        )
    elif isinstance(exc, RequestValidationError):
        # 处理请求参数验证错误
        details = []
        for error in exc.errors():
            details.append({
                "field": error["loc"],
                "message": error["msg"],
                "type": error["type"]
            })
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Validation error",
                details=details
            ).dict()
        )
    elif isinstance(exc, JWTError):
        # 处理 JWT 错误
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                code=status.HTTP_401_UNAUTHORIZED,
                message="Invalid or expired token"
            ).dict(),
            headers={"WWW-Authenticate": "Bearer"}
        )
    elif isinstance(exc, SQLAlchemyError):
        # 处理数据库错误
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Database error"
            ).dict()
        )
    else:
        # 处理其他未预期的错误
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Internal server error"
            ).dict()
        )
