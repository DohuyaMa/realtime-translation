"""OpenAI-style error responses."""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .worker import WorkerBusy


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def error_body(status: int, code: str, message: str) -> dict:
    if status == 401:
        type_ = "authentication_error"
    elif status >= 500:
        type_ = "server_error"
    else:
        type_ = "invalid_request_error"
    return {"error": {"message": message, "type": type_, "code": code}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_body(exc.status, exc.code, exc.message),
            headers=exc.headers,
        )

    @app.exception_handler(WorkerBusy)
    async def _busy(_: Request, exc: WorkerBusy) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_body(503, "server_busy", str(exc)),
            headers={"Retry-After": "1"},
        )
