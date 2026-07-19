from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def payload(code: str, message: str, request: Request, details: Any = None) -> dict:
    body = {"error": {"code": code, "message": message, "request_id": request.state.request_id}}
    if details is not None:
        body["error"]["details"] = details
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=payload(exc.code, exc.message, request, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=payload("validation_error", "Request validation failed", request, exc.errors()),
        )

    @app.exception_handler(DuplicateKeyError)
    async def duplicate_error(request: Request, _exc: DuplicateKeyError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=payload("duplicate", "A record with that unique value already exists", request),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=payload("internal_error", "An unexpected server error occurred", request),
        )
