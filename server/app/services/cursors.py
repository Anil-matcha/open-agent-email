"""Opaque, request-bound cursors for collection endpoints."""
import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException


def encode_cursor(*, scope: str, key: str, item_id: str) -> str:
    raw = json.dumps({"v": 1, "scope": scope, "key": key, "id": item_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, scope: str) -> tuple[str, str]:
    try:
        if not cursor or len(cursor) > 2048:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value: Any = json.loads(raw)
        if (not isinstance(value, dict) or value.get("v") != 1 or value.get("scope") != scope
                or not isinstance(value.get("key"), str) or not isinstance(value.get("id"), str)):
            raise ValueError
        return value["key"], value["id"]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Cursor is invalid or does not match this collection and its filters"}) from None
