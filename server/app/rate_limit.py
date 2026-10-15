"""Small, process-local sliding-window limits for public API traffic."""

import asyncio
import json
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from app.errors import error_body


class ApiRateLimitMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        requests: int = 120,
        window_seconds: int = 60,
        max_clients: int = 8192,
    ):
        self.app = app
        self.requests = requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._last_seen: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._requests_seen = 0

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not self._is_limited_path(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        now = time.monotonic()
        retry_after = None

        async with self._lock:
            self._requests_seen += 1
            if self._requests_seen % 128 == 0:
                self._prune(now)

            hits = self._hits.get(client_ip)
            if hits is None:
                if len(self._hits) >= self.max_clients:
                    oldest = min(self._last_seen, key=self._last_seen.get)
                    self._hits.pop(oldest, None)
                    self._last_seen.pop(oldest, None)
                hits = self._hits[client_ip] = deque()

            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            self._last_seen[client_ip] = now
            if len(hits) >= self.requests:
                retry_after = max(1, math.ceil(self.window_seconds - (now - hits[0])))
            else:
                hits.append(now)

        if retry_after is not None:
            body = json.dumps(error_body(
                429, "Rate limit exceeded; try again later.", code="rate_limited",
                details={"retry_after_seconds": retry_after},
            )).encode()
            headers = [
                (b"content-type", b"application/json"),
                (b"retry-after", str(retry_after).encode("ascii")),
                (b"content-length", str(len(body)).encode("ascii")),
            ]
            if scope.get("path", "").startswith("/unsubscribe/"):
                headers.extend([
                    (b"cache-control", b"no-store, max-age=0"),
                    (b"pragma", b"no-cache"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ])
            await send({"type": "http.response.start", "status": 429, "headers": [
                *headers,
            ]})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _is_limited_path(path: str) -> bool:
        return (
            path.startswith("/v1/") or path == "/v1"
            or path.startswith("/mcp/") or path == "/mcp"
            or path.startswith("/unsubscribe/")
            or path == "/webhooks/ses"
        )

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        expired = [client for client, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for client in expired:
            self._hits.pop(client, None)
            self._last_seen.pop(client, None)
