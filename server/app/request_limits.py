"""HTTP request body size guard for REST and MCP endpoints."""

import json
from collections.abc import Callable
from typing import Any

from app.errors import error_body


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Callable, public_unsubscribe: bool = False) -> None:
        body = json.dumps(
            error_body(413, "Request body exceeds configured size limit", code="payload_too_large")
        ).encode("utf-8")
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if public_unsubscribe:
            headers.extend([
                (b"cache-control", b"no-store, max-age=0"),
                (b"pragma", b"no-cache"),
                (b"referrer-policy", b"no-referrer"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-frame-options", b"DENY"),
                (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
            ])
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": headers,
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        public_unsubscribe = scope.get("path", "").startswith("/unsubscribe/")
        if content_lengths:
            try:
                if any(int(value) > self.max_bytes for value in content_lengths):
                    await self._reject(send, public_unsubscribe)
                    return
            except ValueError:
                pass  # The streaming check below still enforces the cap.

        consumed = 0

        async def receive_limited():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, receive_limited, send)
        except RequestBodyTooLarge:
            await self._reject(send, public_unsubscribe)
