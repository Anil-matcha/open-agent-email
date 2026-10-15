"""Helpers for validating signed Open Agent Email webhook requests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time


def verify_webhook_signature(
    raw_body: bytes,
    *,
    event_id: str,
    timestamp: str,
    signature: str,
    secret: str,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a webhook MAC and timestamp before parsing or processing its body.

    The signature covers the exact raw request bytes. A valid signature does not
    prevent replay within the timestamp window; receivers should also persist
    processed event IDs and reject duplicates.
    """
    if (
        not isinstance(raw_body, bytes)
        or not isinstance(event_id, str)
        or not isinstance(timestamp, str)
        or not isinstance(signature, str)
        or not isinstance(secret, str)
        or not event_id
        or len(event_id) > 256
        or not secret
    ):
        return False
    if (
        isinstance(tolerance_seconds, bool)
        or not isinstance(tolerance_seconds, int)
        or not 0 <= tolerance_seconds <= 86400
        or not timestamp.isascii()
        or not timestamp.isdigit()
    ):
        return False
    if len(timestamp) > 12:
        return False
    candidates = [candidate.strip() for candidate in signature.split(",")]
    if not 1 <= len(candidates) <= 2 or any(
        len(candidate) != 71
        or not candidate.startswith("sha256=")
        or any(char not in "0123456789abcdef" for char in candidate[7:])
        for candidate in candidates
    ):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > tolerance_seconds:
        return False
    try:
        secret_bytes = secret.encode("utf-8")
        message = timestamp.encode("ascii") + b"." + event_id.encode("utf-8") + b"." + raw_body
    except UnicodeEncodeError:
        return False
    digest = hmac.new(secret_bytes, message, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    matches = [hmac.compare_digest(expected, candidate) for candidate in candidates]
    return any(matches)


class WebhookSignatureError(ValueError):
    """Raised when a webhook delivery cannot be authenticated."""


def sign_webhook(secret: str, timestamp: str, event_id: str, raw_body: bytes) -> str:
    """Legacy signature for one secret: HMAC-SHA256 over ``<ts>.<event id>.<body>``, ``sha256=`` + hex."""
    message = timestamp.encode("ascii") + b"." + event_id.encode("utf-8") + b"." + raw_body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_webhook_v1(secret: str, timestamp: str, raw_body: bytes) -> str:
    """Current signature for one secret: HMAC-SHA256 over ``<ts>.<body>``, lowercase hex (the ``v1=`` value)."""
    message = timestamp.encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _header(headers, name: str):
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value[0] if isinstance(value, (list, tuple)) else value
    return None


def _any_match(expected: str, candidates) -> bool:
    # Compare against every candidate so timing does not reveal which one matched.
    hit = False
    for candidate in candidates:
        hit = hmac.compare_digest(expected.encode("utf-8"), candidate.encode("utf-8")) or hit
    return hit


def construct_event(
    raw_body: bytes,
    headers,
    secrets,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> dict:
    """Authenticate a delivery and return ``{"id", "type", "timestamp", "payload"}``.

    Pass the RAW request body and the request headers (any mapping, names case-insensitive).
    When ``Webhook-Signature`` (``t=<unix>,v1=<hex>[,v1=<hex>]``) is present it is used and must
    verify; otherwise the legacy ``X-Webhook-Event-Id``, ``X-Webhook-Timestamp`` and
    ``X-Webhook-Signature`` headers are used. ``secrets`` may be one string or a list, to cover
    the 24 hour overlap while a secret is rotated. Raises :class:`WebhookSignatureError`.
    """
    secret_list = [secrets] if isinstance(secrets, str) else list(secrets or [])
    secret_list = [s for s in secret_list if isinstance(s, str) and s]
    if not secret_list:
        raise WebhookSignatureError("No signing secret provided")
    if not isinstance(raw_body, (bytes, bytearray)):
        raise WebhookSignatureError("raw_body must be the exact request bytes")
    raw_body = bytes(raw_body)
    if isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, int) or tolerance_seconds < 0:
        raise WebhookSignatureError("tolerance_seconds must be a non-negative integer")
    current_time = time.time() if now is None else now

    def check_timestamp(value) -> str:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 12:
            raise WebhookSignatureError("Invalid webhook timestamp")
        if abs(current_time - int(value)) > tolerance_seconds:
            raise WebhookSignatureError("Webhook timestamp is outside the allowed window")
        return value

    event_id = _header(headers, "x-webhook-event-id")
    current = _header(headers, "webhook-signature")
    if current:
        timestamp = None
        v1: list[str] = []
        for part in current.split(","):
            key, sep, value = part.partition("=")
            if not sep:
                continue
            key, value = key.strip(), value.strip()
            if key == "t":
                timestamp = value
            elif key == "v1":
                v1.append(value)
        if timestamp is None or not v1:
            raise WebhookSignatureError("Malformed Webhook-Signature header")
        timestamp = check_timestamp(timestamp)
        matched = any(_any_match(sign_webhook_v1(s, timestamp, raw_body), v1) for s in secret_list)
    else:
        timestamp = _header(headers, "x-webhook-timestamp")
        signature = _header(headers, "x-webhook-signature")
        if not event_id or not timestamp or not signature:
            raise WebhookSignatureError("Missing webhook signature headers")
        timestamp = check_timestamp(timestamp)
        provided = [c.strip() for c in signature.split(",") if c.strip()]
        matched = any(_any_match(sign_webhook(s, timestamp, event_id, raw_body), provided) for s in secret_list)
    if not matched:
        raise WebhookSignatureError("Webhook signature does not match")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebhookSignatureError("Webhook body is not valid JSON") from exc
    fields = payload if isinstance(payload, dict) else {}
    event_type = (
        _header(headers, "webhook-event-type") or _header(headers, "x-webhook-event")
        or (fields.get("type") if isinstance(fields.get("type"), str) else "")
    )
    return {
        "id": event_id or (fields.get("id") if isinstance(fields.get("id"), str) else ""),
        "type": event_type,
        "timestamp": int(timestamp),
        "payload": payload,
    }
