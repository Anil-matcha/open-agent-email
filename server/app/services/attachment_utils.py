"""Validation and decoding for API/MCP supplied outbound attachments."""

import base64
import binascii
import hashlib
import hmac
import os
import re
import time
from typing import Any

from fastapi import HTTPException

from app.config import settings


ATTACHMENT_URL_TTL_SECONDS = 300


def _url_signing_key() -> bytes:
    value = (settings.ATTACHMENT_URL_SIGNING_KEY or settings.SMTP_CONFIG_ENCRYPTION_KEY
             or settings.ADMIN_API_KEY).strip()
    if not value:
        raise RuntimeError("An attachment URL signing key is required")
    return value.encode("utf-8")


def attachment_download_signature(agent_email: str, message_id: str, attachment_id: str, expires: int) -> str:
    payload = f"{agent_email}\n{message_id}\n{attachment_id}\n{expires}".encode("utf-8")
    return hmac.new(_url_signing_key(), payload, hashlib.sha256).hexdigest()


def make_attachment_download_url(agent_email: str, message_id: str, attachment_id: str,
                                 *, now: int | None = None) -> str:
    expires = (int(time.time()) if now is None else now) + ATTACHMENT_URL_TTL_SECONDS
    signature = attachment_download_signature(agent_email, message_id, attachment_id, expires)
    return f"/v1/attachments/{message_id}/{attachment_id}/download?expires={expires}&signature={signature}"


def verify_attachment_download_signature(agent_email: str, message_id: str, attachment_id: str,
                                         expires: int, signature: str, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else now
    if expires < current or expires > current + ATTACHMENT_URL_TTL_SECONDS:
        return False
    expected = attachment_download_signature(agent_email, message_id, attachment_id, expires)
    return hmac.compare_digest(expected, signature)


def decode_attachment_inputs(items: Any) -> list[dict[str, Any]]:
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > settings.OUTBOUND_MAX_ATTACHMENTS:
        raise HTTPException(status_code=413, detail="Too many attachments")

    decoded: list[dict[str, Any]] = []
    total_bytes = 0
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="Each attachment must be an object")
        filename = item.get("filename")
        content_type = item.get("content_type", "application/octet-stream")
        encoded = item.get("content_base64")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
            raise HTTPException(status_code=422, detail="Attachment filename must contain 1 to 255 characters")
        if not isinstance(content_type, str) or len(content_type) > 255:
            raise HTTPException(status_code=422, detail="Invalid attachment content type")
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", content_type):
            content_type = "application/octet-stream"
        if not isinstance(encoded, str):
            raise HTTPException(status_code=422, detail="Attachment content_base64 is required")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="Attachment content must be valid base64") from None
        if len(content) > settings.OUTBOUND_MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="Attachment exceeds configured size limit")
        total_bytes += len(content)
        if total_bytes > settings.OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="Attachments exceed configured total size limit")
        safe_name = os.path.basename(filename.replace("\\", "/"))
        safe_name = "".join(char if char >= " " and char not in "\x7f" else "_" for char in safe_name)[:255]
        decoded.append({"filename": safe_name or "attachment", "content_type": content_type, "content": content})
    return decoded


def validate_composed_message_size(subject: str, body_text: str, body_html: str | None,
                                  attachments: list[dict[str, Any]] | None) -> None:
    actual = len(subject.encode("utf-8")) + len(body_text.encode("utf-8"))
    if body_html:
        actual += len(body_html.encode("utf-8"))
    actual += sum(len(item["content"]) for item in attachments or [])
    maximum = settings.OUTBOUND_MAX_COMPOSED_BYTES
    if actual > maximum:
        raise HTTPException(status_code=413, detail={
            "scope": "composed_message", "actual_bytes": actual, "max_bytes": maximum,
        })
