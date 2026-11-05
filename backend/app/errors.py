import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)

HTTP_ERROR_CODES = {
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        # 비밀번호·API 키가 되돌아가지 않도록 입력값 없이 필드 이름만 알려준다.
        fields = sorted({".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()})
        return error_response(422, "VALIDATION_ERROR", f"입력값을 확인해 주세요: {', '.join(fields)}")

    @app.exception_handler(HTTPException)
    async def handle_http_error(_: Request, exc: HTTPException):
        code = HTTP_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.error("처리되지 않은 오류: %s %s (%s)", request.method, request.url.path, type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "서버 오류가 발생했습니다.")
