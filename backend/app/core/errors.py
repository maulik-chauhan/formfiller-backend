"""RFC 7807 ``application/problem+json`` error model and handlers.

All error responses are sanitised: no PII, stack traces, or raw request bodies
are echoed back to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PROBLEM_CONTENT_TYPE = "application/problem+json"


class ProblemDetail(BaseModel):
    """RFC 7807 problem document."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: str | None = None
    instance: str | None = None


class AppError(Exception):
    """Base application error carrying an RFC 7807 payload."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "APP_ERROR"
    title: str = "Application error"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        self.detail = detail
        if code is not None:
            self.code = code
        super().__init__(detail or self.title)

    def to_problem(self, instance: str | None = None) -> ProblemDetail:
        return ProblemDetail(
            title=self.title,
            status=self.status_code,
            detail=self.detail,
            code=self.code,
            instance=instance,
        )


class PrivacyBoundaryViolation(AppError):
    status_code = 422
    code = "PRIVACY_BOUNDARY_VIOLATION"
    title = "Request violated the zero-knowledge privacy boundary"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    title = "Conflict"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    title = "Resource not found"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    title = "Forbidden"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    title = "Unauthorized"


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    title = "Too many requests"


class ValidationFailedError(AppError):
    # 422: the request is well-formed but semantically invalid (e.g. references
    # an unapproved domain or a non-existent parent resource).
    status_code = 422
    code = "INVALID_REQUEST"
    title = "Invalid request"


def _json(problem: ProblemDetail) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True),
        media_type=PROBLEM_CONTENT_TYPE,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach sanitised RFC 7807 handlers to ``app``."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:  # type: ignore[unused-ignore]
        return _json(exc.to_problem())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Surface field locations only — never the submitted values.
        fields: list[dict[str, Any]] = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        problem = ProblemDetail(
            title="Request validation failed",
            status=422,
            code="VALIDATION_ERROR",
            detail=str(fields),
        )
        return _json(problem)
