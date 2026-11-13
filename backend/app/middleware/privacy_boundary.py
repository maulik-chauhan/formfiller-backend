"""Route-aware ``PrivacyBoundaryMiddleware`` (spec Section 6).

Classifies every request into one of three deterministic policy profiles and
enforces the matching payload-size, content-type, and payload-inspection rules
BEFORE the request body reaches any route handler. The admin blank-PDF upload
route is explicitly carved out without weakening citizen privacy controls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Body-less mutating routes that may arrive with an empty body / no content-type.
_EMPTY_BODY_ALLOWED = re.compile(r"^/api/v1/auth/logout$|^/api/v1/.*$")

# --- Forbidden embedded-binary markers scanned inside citizen JSON values -----
_FORBIDDEN_MARKERS: tuple[bytes, ...] = (
    b"data:image/",
    b"data:application/pdf",
    b"%PDF-",
    b"JVBERi0",  # base64("%PDF-")
    b"iVBORw0KGgo",  # base64 PNG magic
    b".govvault",
)

# Admin blank-PDF uploads: create a version, dry-run validate one, or replace the
# artifacts of a DRAFT version. These are the only routes that accept file bytes.
_UUID = r"[0-9a-fA-F-]{36}"
_ADMIN_UPLOAD_RE = re.compile(
    rf"^/api/v1/admin/templates/{_UUID}/versions(/validate|/{_UUID}/artifacts)?$"
)
_WEBHOOK_PATH = "/api/v1/payments/webhooks/razorpay"


class PolicyProfile(Enum):
    CITIZEN_DEFAULT = auto()
    ADMIN_TEMPLATE_UPLOAD = auto()
    SIGNED_WEBHOOK = auto()


@dataclass(frozen=True)
class PolicyDecision:
    profile: PolicyProfile
    max_body_bytes: int


def classify(path: str, method: str) -> PolicyDecision:
    """Deterministically map ``(path, method)`` to a policy profile."""
    settings = get_settings()
    if method == "POST" and _ADMIN_UPLOAD_RE.match(path):
        return PolicyDecision(
            PolicyProfile.ADMIN_TEMPLATE_UPLOAD, settings.admin_upload_max_body_bytes
        )
    if method == "POST" and path == _WEBHOOK_PATH:
        return PolicyDecision(PolicyProfile.SIGNED_WEBHOOK, settings.webhook_max_body_bytes)
    return PolicyDecision(PolicyProfile.CITIZEN_DEFAULT, settings.citizen_max_body_bytes)


def _problem(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": detail,
            "status": status_code,
            "code": code,
            "detail": detail,
        },
    )


def _content_type(headers: Headers) -> str:
    return (headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def _precheck(request_headers: Headers, decision: PolicyDecision, method: str) -> Response | None:
    """Header-only checks runnable before buffering the body. ``None`` == pass."""
    ctype = _content_type(request_headers)

    if decision.profile is PolicyProfile.ADMIN_TEMPLATE_UPLOAD:
        if not request_headers.get("authorization"):
            return _problem(401, "UNAUTHORIZED", "Admin upload requires a bearer token")
        if ctype != "multipart/form-data":
            return _problem(
                415, "UNSUPPORTED_MEDIA_TYPE", "Admin upload must be multipart/form-data"
            )
        return None

    if decision.profile is PolicyProfile.SIGNED_WEBHOOK:
        if not request_headers.get("x-razorpay-signature"):
            return _problem(401, "UNAUTHORIZED", "Webhook requires X-Razorpay-Signature")
        if ctype and ctype != "application/json":
            return _problem(415, "UNSUPPORTED_MEDIA_TYPE", "Webhook body must be application/json")
        return None

    # Citizen / default profile. Empty body (e.g. DELETE / logout) is allowed
    # with no content-type; a present content-type must be application/json.
    if method in MUTATING_METHODS and ctype and ctype != "application/json":
        return _problem(
            415, "UNSUPPORTED_MEDIA_TYPE", "Only application/json is accepted on this route"
        )
    return None


def _scan_citizen_body(body: bytes) -> Response | None:
    lowered = body.lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker.lower() in lowered:
            return _problem(
                422,
                "PRIVACY_BOUNDARY_VIOLATION",
                "Embedded binary/PDF/image payloads are not permitted on citizen routes",
            )
    return None


class PrivacyBoundaryMiddleware:
    """Pure-ASGI middleware enforcing the three policy profiles."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        decision = classify(request.url.path, request.method)

        precheck = _precheck(request.headers, decision, request.method)
        if precheck is not None:
            await precheck(scope, receive, send)
            return

        # Stream the body chunk-by-chunk, aborting the moment the cap is exceeded.
        buffered = bytearray()
        too_large = False
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if chunk:
                buffered.extend(chunk)
                if len(buffered) > decision.max_body_bytes:
                    too_large = True
                    break
            more_body = message.get("more_body", False)

        if too_large:
            await _problem(
                413,
                "CONTENT_TOO_LARGE",
                f"Request body exceeds the {decision.max_body_bytes}-byte limit for this route",
            )(scope, receive, send)
            return

        if decision.profile is PolicyProfile.CITIZEN_DEFAULT and buffered:
            violation = _scan_citizen_body(bytes(buffered))
            if violation is not None:
                await violation(scope, receive, send)
                return

        # Replay the fully-buffered body to the downstream app.
        body_bytes = bytes(buffered)
        sent = False

        async def replay_receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)
