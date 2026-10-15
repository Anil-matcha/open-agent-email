"""Stable machine-readable error codes.

Every error response keeps its historical `detail` field and HTTP status and additionally carries

    "error": {"code": "<catalog code>", "message": "<human text>", "details": {...} | [...] | null}

Codes are derived centrally from the status and message so existing raise sites are unchanged.
The catalog below is published in docs/errors.md; a test keeps the two in sync.
"""

import re
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@dataclass(frozen=True)
class ErrorCode:
    status: int
    meaning: str
    retry: str


CATALOG: dict[str, ErrorCode] = {
    # authentication and authorization
    "unauthorized": ErrorCode(401, "Missing, malformed, expired or revoked credential.", "Do not retry; supply a valid key."),
    "forbidden": ErrorCode(403, "The credential is valid but the action is not allowed.", "Do not retry."),
    "forbidden_scope": ErrorCode(403, "The key's scope does not permit this operation (for example a read-only key sending mail).", "Use a key with the needed scope."),
    "forbidden_inbox": ErrorCode(403, "The key belongs to a different inbox.", "Use the key issued for this inbox."),
    "admin_required": ErrorCode(403, "Only the administrator credential may do this.", "Use the administrator key."),
    "reviewer_required": ErrorCode(403, "A reviewer or administrator credential is required.", "Use a reviewer key."),
    "invalid_provider_signature": ErrorCode(403, "A provider (SNS) notification failed signature, topic or certificate checks.", "Do not retry; fix provider configuration."),
    # not found
    "not_found": ErrorCode(404, "The resource does not exist or is not visible to this credential.", "Do not retry unchanged."),
    "feature_not_configured": ErrorCode(404, "The optional feature is not configured on this server.", "Configure the feature."),
    # validation and request shape
    "validation_error": ErrorCode(422, "The request body, query or path failed validation; `error.details` lists each field.", "Fix the request."),
    "bad_request": ErrorCode(400, "The request is malformed.", "Fix the request."),
    "invalid_cursor": ErrorCode(400, "The pagination cursor is malformed or was issued for different filters.", "Restart from the first page."),
    "invalid_filter": ErrorCode(400, "A filter value (for example status) is not recognized.", "Use a documented value."),
    "invalid_action": ErrorCode(400, "The requested action is not supported.", "Use a documented action."),
    "invalid_csv": ErrorCode(400, "A CSV upload is malformed, has bad headers, or has no usable rows.", "Fix the file."),
    "invalid_domain": ErrorCode(400, "The domain name is not valid.", "Fix the domain."),
    "invalid_provider_payload": ErrorCode(400, "A provider notification payload is malformed.", "Do not retry."),
    "invalid_template": ErrorCode(422, "A template has invalid placeholder syntax, data, or rendered size.", "Fix the template or data."),
    "invalid_attachment": ErrorCode(422, "An attachment is missing fields, has a bad type, or is not valid base64.", "Fix the attachment."),
    "invalid_request": ErrorCode(422, "The request is understood but semantically invalid.", "Fix the request."),
    "confirmation_required": ErrorCode(400, "A destructive action needs an explicit confirmation parameter.", "Resend with the confirmation."),
    "smtp_not_configured": ErrorCode(400, "The SMTP relay is not configured.", "Configure the relay."),
    "suppressed_recipient": ErrorCode(422, "The inbox has suppressed email to this recipient.", "Do not retry; remove the suppression first if appropriate."),
    "idempotency_key_invalid": ErrorCode(400, "The Idempotency-Key header is empty or longer than 128 characters.", "Send a key of 1-128 characters."),
    # conflicts and state
    "conflict": ErrorCode(409, "The request conflicts with the current state.", "Re-read the resource, then retry if still valid."),
    "already_exists": ErrorCode(409, "A resource with the same unique value already exists.", "Use the existing resource or a different value."),
    "idempotency_key_reuse": ErrorCode(422, "The Idempotency-Key was already used with a different request.", "Use a fresh key for a different request."),
    "review_already_resolved": ErrorCode(409, "The review was already approved or rejected.", "Do not retry."),
    "review_pending": ErrorCode(409, "The message is waiting for review and cannot be changed that way.", "Resolve the review first."),
    "send_in_progress": ErrorCode(409, "The scheduled message is already being submitted.", "Do not retry; check message status."),
    "concurrent_modification": ErrorCode(409, "The resource changed while handling the request.", "Retry once."),
    "invalid_state": ErrorCode(409, "The resource is not in a state that allows this action.", "Check its status first."),
    "webhook_disabled": ErrorCode(409, "The webhook endpoint is disabled.", "Enable it, then retry."),
    "limit_exceeded": ErrorCode(409, "A per-inbox limit was reached.", "Remove an item or ask for a higher limit."),
    "delivery_state_uncertain": ErrorCode(409, "Delivery of the message is unknown; retrying may send a duplicate.", "Retry only with confirm_duplicate_risk=true."),
    "ambiguous_target": ErrorCode(409, "The address exists in several inboxes; name the inbox.", "Pass agent_email."),
    # size and rate limits
    "payload_too_large": ErrorCode(413, "The request body or upload exceeds the size limit.", "Send less data."),
    "attachment_limit_exceeded": ErrorCode(413, "Too many or too large attachments.", "Reduce attachments."),
    "attachment_too_large": ErrorCode(413, "Inline attachment exceeds the size limit.", "Use the signed download URL instead."),
    "rate_limited": ErrorCode(429, "Too many requests from this client.", "Wait `Retry-After` seconds, then retry."),
    # server side
    "encryption_key_missing": ErrorCode(503, "A required encryption key is not configured or unusable.", "Operator must set SMTP_CONFIG_ENCRYPTION_KEY."),
    "service_unavailable": ErrorCode(503, "The service or a dependency is temporarily unavailable.", "Retry with backoff."),
    "not_implemented": ErrorCode(501, "The operation is not implemented.", "Do not retry."),
    "upstream_error": ErrorCode(502, "An upstream service failed.", "Retry with backoff."),
    "internal_error": ErrorCode(500, "Unexpected server error.", "Retry with backoff; report if it persists."),
}

_STATUS_DEFAULT = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
    413: "payload_too_large", 422: "invalid_request", 429: "rate_limited", 500: "internal_error",
    501: "not_implemented", 502: "upstream_error", 503: "service_unavailable",
}

# (status or None for any, regex on the lower-cased message, code). First match wins.
_RULES: list[tuple[int | None, str, str]] = [
    (None, r"idempotency-key must", "idempotency_key_invalid"),
    (None, r"idempotency-key was already used", "idempotency_key_reuse"),
    (None, r"already exists|already registered", "already_exists"),
    (403, r"not authorized for this agent inbox", "forbidden_inbox"),
    (403, r"does not permit", "forbidden_scope"),
    (403, r"administrator token required", "admin_required"),
    (403, r"reviewer credential required", "reviewer_required"),
    (403, r"sns|untrusted feedback topic", "invalid_provider_signature"),
    (404, r"is not configured", "feature_not_configured"),
    (409, r"already resolved", "review_already_resolved"),
    (409, r"already being submitted", "send_in_progress"),
    (409, r"pending review", "review_pending"),
    (409, r"duplicate", "delivery_state_uncertain"),
    (409, r"several inboxes", "ambiguous_target"),
    (409, r"enable the webhook", "webhook_disabled"),
    (409, r"at most \d+ webhook endpoints", "limit_exceeded"),
    (409, r"changed; retry|secret changed", "concurrent_modification"),
    (409, r"only failed|can be retried|can be replayed", "invalid_state"),
    (400, r"cursor", "invalid_cursor"),
    (400, r"csv", "invalid_csv"),
    (400, r"invalid domain", "invalid_domain"),
    (400, r"invalid (sns|ses)", "invalid_provider_payload"),
    (400, r"invalid action", "invalid_action"),
    (400, r"invalid .*status", "invalid_filter"),
    (400, r"confirm=delete", "confirmation_required"),
    (400, r"smtp relay host is not configured", "smtp_not_configured"),
    (413, r"inline attachment", "attachment_too_large"),
    (413, r"attachment", "attachment_limit_exceeded"),
    (422, r"suppress", "suppressed_recipient"),
    (422, r"attachment", "invalid_attachment"),
    (422, r"template|preview", "invalid_template"),
    (503, r"encryption_key", "encryption_key_missing"),
]
_COMPILED = [(s, re.compile(p), c) for s, p, c in _RULES]


def classify(status: int, message: str = "") -> str:
    text = (message or "").lower()
    for rule_status, pattern, code in _COMPILED:
        if (rule_status is None or rule_status == status) and pattern.search(text):
            return code
    return _STATUS_DEFAULT.get(status, "internal_error" if status >= 500 else "bad_request")


def error_object(code: str, message: str, details=None) -> dict:
    return {"code": code, "message": message, "details": details}


def error_body(status: int, message: str, *, code: str | None = None, details=None, detail=None) -> dict:
    """Response body: legacy `detail` plus the structured `error` object."""
    return {
        "detail": message if detail is None else detail,
        "error": error_object(code or classify(status, message), message, details),
    }


def _validation_details(errors: list[dict]) -> list[dict]:
    return [
        {"path": ".".join(str(part) for part in err.get("loc", ())), "message": err.get("msg", ""), "type": err.get("type", "")}
        for err in errors
    ]


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    explicit_code = None
    if isinstance(exc.detail, dict) and isinstance(exc.detail.get("code"), str):
        message = exc.detail.get("message") if isinstance(exc.detail.get("message"), str) else "Request failed"
        explicit_code = exc.detail["code"]
        details = {key: value for key, value in exc.detail.items() if key not in {"code", "message"}}
        details = jsonable_encoder(details) or None
    else:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        details = None if isinstance(exc.detail, str) else jsonable_encoder(exc.detail)
    body = error_body(exc.status_code, message, code=explicit_code, details=details, detail=exc.detail)
    headers = dict(exc.headers) if exc.headers else None
    if exc.status_code == 429 and headers:
        retry = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        if retry is not None:
            body["error"]["details"] = {"retry_after_seconds": int(retry)}
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = jsonable_encoder(exc.errors())
    body = {
        "detail": errors,  # unchanged FastAPI shape
        "error": error_object("validation_error", "Request validation failed", _validation_details(errors)),
    }
    return JSONResponse(body, status_code=422)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
