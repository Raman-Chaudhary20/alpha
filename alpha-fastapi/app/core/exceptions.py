"""Custom exceptions and FastAPI exception handlers for Alpha."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SlackNotConfiguredError(RuntimeError):
    """Raised when a Slack operation is attempted but credentials are absent."""

    def __init__(self, message: str = "Slack is not configured on this instance") -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Handler helpers
# ---------------------------------------------------------------------------


def _error_body(error: str, detail: str | None = None) -> dict:
    payload: dict = {"error": error}
    if detail is not None:
        payload["detail"] = detail
    return payload


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to *app*."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            msg = "The requested resource was not found"
        elif exc.status_code == 405:
            msg = "Method not allowed"
        else:
            msg = str(exc.detail) if exc.detail else "HTTP error"
        logger.warning("HTTP %s on %s: %s", exc.status_code, request.url.path, msg)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(msg),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Flatten validation errors into a readable string
        errors = "; ".join(
            f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        logger.warning("Validation error on %s: %s", request.url.path, errors)
        return JSONResponse(
            status_code=422,
            content=_error_body("Validation error", errors),
        )

    @app.exception_handler(SlackNotConfiguredError)
    async def slack_not_configured_handler(
        request: Request, exc: SlackNotConfiguredError
    ) -> JSONResponse:
        logger.warning("SlackNotConfiguredError: %s", exc.message)
        return JSONResponse(
            status_code=400,
            content=_error_body("Slack not configured", exc.message),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body("Internal server error", str(exc)),
        )
