"""Safe, bounded HTTPS webhook transport and durable delivery worker."""

import asyncio
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import ssl
import time
import uuid
from urllib.parse import quote, urlsplit

from sqlalchemy import or_, select, update

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import WebhookDelivery, WebhookEndpoint
from app.services.smtp_secrets import decrypt_secret

logger = logging.getLogger("webhook_delivery")
MAX_ATTEMPTS = 8
LEASE_SECONDS = 45
CONNECT_TIMEOUT_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 10
MAX_RESPONSE_HEADERS = 16 * 1024


# Eight attempts in total. Entry n is the wait after the nth failed attempt before attempt n+1;
# entry 0 is unused because the first attempt is immediate. The last wait is 16 hours.
RETRY_DELAYS_SECONDS = (0, 60, 300, 900, 3600, 14400, 28800, 57600)


async def prune_expired_deliveries(db, *, now: datetime.datetime | None = None) -> int:
    """Drop retained payloads after the configured window while keeping ID tombstones."""
    current = now or datetime.datetime.utcnow()
    cutoff = current - datetime.timedelta(days=settings.WEBHOOK_DELIVERY_RETENTION_DAYS)
    result = await db.execute(update(WebhookDelivery).where(
        WebhookDelivery.status.in_({"delivered", "failed"}),
        WebhookDelivery.created_at < cutoff,
    ).values(status="expired", payload={}, last_error=None, locked_at=None,
             lease_token=None, replayed_by=None, replayed_at=None, replay_count=0))
    return int(result.rowcount or 0)


def retry_delay_seconds(failed_attempts: int) -> int:
    """Seconds to wait after `failed_attempts` consecutive failures (1-based) before retrying."""
    return RETRY_DELAYS_SECONDS[max(0, min(failed_attempts, len(RETRY_DELAYS_SECONDS) - 1))]


def sign_legacy(secret: str, timestamp: str, event_id: str, body: bytes) -> str:
    """`X-Webhook-Signature` value for one secret: HMAC-SHA256 over `<ts>.<event id>.<body>`."""
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode() + b"." + event_id.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def sign_v1(secret: str, timestamp: str, body: bytes) -> str:
    """`v1` signature for one secret: HMAC-SHA256 over `<ts>.<body>`, lowercase hex."""
    return hmac.new(secret.encode("utf-8"), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def signature_headers(timestamp: str, event_id: str, event_type: str, body: bytes, secrets: list[str]) -> dict[str, str]:
    """Every signing header for a delivery; `secrets` is the current secret then any unexpired previous one."""
    return {
        "X-Webhook-Event-Id": event_id,
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": ",".join(sign_legacy(s, timestamp, event_id, body) for s in secrets),
        "X-Webhook-Event": event_type,
        "Webhook-Signature": f"t={timestamp}," + ",".join(f"v1={sign_v1(s, timestamp, body)}" for s in secrets),
        "Webhook-Event-Type": event_type,
        "Webhook-Schema-Version": "1",
    }


def validate_webhook_url(url: str) -> str:
    """Normalize an HTTPS URL and reject syntactically unsafe destinations."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("Webhook URL is invalid")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Webhook URL is invalid") from exc
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.fragment or (port not in (None, 443))
    ):
        raise ValueError("Webhook URL must be HTTPS on port 443, without credentials or a fragment")
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Webhook hostname is invalid") from exc
    if not host:
        raise ValueError("Webhook hostname is invalid")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Webhook URL must not target a private or reserved IP address")
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="%=&?/:@!$'()*+,;+-._~")
    target = path + (f"?{query}" if query else "")
    if any(ch in target for ch in "\r\n\x00"):
        raise ValueError("Webhook URL path is invalid")
    host_header = f"[{host}]" if ":" in host else host
    return f"https://{host_header}{target}"


async def _resolve_public(host: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [str(literal)]
    except ValueError:
        loop = asyncio.get_running_loop()
        records = await asyncio.wait_for(
            loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise RuntimeError("destination_dns_unavailable")
    if len(addresses) > 32:
        raise RuntimeError("destination_dns_too_many_addresses")
    # Fail closed if any answer is non-public; this also avoids choosing between
    # mixed public/private DNS answers and prevents DNS rebinding at connect time.
    for value in addresses:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if not address.is_global:
            raise RuntimeError("destination_not_public")
    return addresses


async def _post_pinned(url: str, body: bytes, headers: dict[str, str]) -> int:
    canonical = validate_webhook_url(url)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    addresses = await _resolve_public(host)
    context = ssl.create_default_context()
    last_error: Exception | None = None
    for address in addresses:
        writer = None
        try:
            connect_host = f"[{address}]" if ":" in address else address
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, 443, ssl=context, server_hostname=host,
                                        limit=MAX_RESPONSE_HEADERS),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
            host_header = f"[{host}]" if ":" in host else host
            request_headers = {
                "Host": host_header,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
                **headers,
            }
            request = f"POST {parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''} HTTP/1.1\r\n"
            request += "".join(f"{key}: {value}\r\n" for key, value in request_headers.items()) + "\r\n"
            writer.write(request.encode("ascii") + body)
            await asyncio.wait_for(writer.drain(), timeout=TOTAL_TIMEOUT_SECONDS)
            header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=TOTAL_TIMEOUT_SECONDS)
            if len(header_bytes) > MAX_RESPONSE_HEADERS:
                raise RuntimeError("response_headers_too_large")
            first_line = header_bytes.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = first_line.split(" ", 2)
            if len(parts) < 2 or not parts[1].isdigit():
                raise RuntimeError("invalid_response")
            return int(parts[1])
        except Exception as exc:
            last_error = exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
    raise RuntimeError(f"transport_{type(last_error).__name__ if last_error else 'error'}")


async def _deliver(delivery_id: str, lease_token: str) -> None:
    async with AsyncSessionLocal() as db:
        delivery = await db.get(WebhookDelivery, delivery_id)
        endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id) if delivery else None
        if not delivery or not endpoint or not endpoint.enabled:
            endpoint_url = secret_stored = event_type = None
            payload = None
            event_id = delivery_id
        else:
            payload = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            endpoint_url = endpoint.url
            secret_stored = endpoint.signing_secret
            previous_secret_stored = endpoint.previous_signing_secret
            previous_secret_expires_at = endpoint.previous_secret_expires_at
            event_type = delivery.event_type
            event_id = delivery.id

    if payload is None:
        error = "endpoint_unavailable"
    else:
        timestamp = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
        try:
            secrets = [decrypt_secret(secret_stored, "webhook signing secret")]
            if previous_secret_stored and previous_secret_expires_at:
                expiry = previous_secret_expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=datetime.timezone.utc)
                if expiry > datetime.datetime.now(datetime.timezone.utc):
                    secrets.append(decrypt_secret(previous_secret_stored, "previous webhook signing secret"))
            status_code = await asyncio.wait_for(
                _post_pinned(endpoint_url, payload, signature_headers(timestamp, event_id, event_type, payload, secrets)),
                timeout=TOTAL_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS + 2,
            )
            error = None if 200 <= status_code < 300 else f"http_status_{status_code}"
        except Exception as exc:
            # Never persist URL/query, response bodies, exception messages, or secrets.
            logger.warning("Webhook delivery %s failed (%s)", delivery_id, type(exc).__name__)
            error = f"delivery_{type(exc).__name__}"

    async with AsyncSessionLocal() as db:
        now = datetime.datetime.utcnow()
        current = await db.get(WebhookDelivery, delivery_id)
        if not current or current.status != "processing" or current.lease_token != lease_token:
            return
        if error is None:
            result = await db.execute(update(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id, WebhookDelivery.status == "processing",
                WebhookDelivery.lease_token == lease_token,
            ).values(status="delivered", locked_at=None, lease_token=None, last_error=None, delivered_at=now))
        elif error == "endpoint_unavailable":
            result = await db.execute(update(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id, WebhookDelivery.status == "processing",
                WebhookDelivery.lease_token == lease_token,
            ).values(status="failed", locked_at=None, lease_token=None, last_error=error))
        elif current.attempts < MAX_ATTEMPTS:
            delay = retry_delay_seconds(current.attempts)
            result = await db.execute(update(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id, WebhookDelivery.status == "processing",
                WebhookDelivery.lease_token == lease_token,
            ).values(status="pending", available_at=now + datetime.timedelta(seconds=delay), locked_at=None,
                     lease_token=None, last_error=error))
        else:
            result = await db.execute(update(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id, WebhookDelivery.status == "processing",
                WebhookDelivery.lease_token == lease_token,
            ).values(status="failed", locked_at=None, lease_token=None, last_error=error))
        if result.rowcount == 1:
            await db.commit()
        else:
            await db.rollback()


async def _claim_one() -> tuple[str, str] | None:
    now = datetime.datetime.utcnow()
    stale = now - datetime.timedelta(seconds=LEASE_SECONDS)
    async with AsyncSessionLocal() as db:
        await db.execute(update(WebhookDelivery).where(
            WebhookDelivery.status == "processing",
            or_(WebhookDelivery.locked_at.is_(None), WebhookDelivery.locked_at < stale),
            WebhookDelivery.attempts >= MAX_ATTEMPTS,
        ).values(status="failed", locked_at=None, lease_token=None, last_error="worker_lease_expired_max_attempts"))
        await db.commit()
        item = (await db.execute(select(WebhookDelivery).where(or_(
            (WebhookDelivery.status == "pending") & (WebhookDelivery.available_at <= now),
            (WebhookDelivery.status == "processing") & or_(
                WebhookDelivery.locked_at.is_(None), WebhookDelivery.locked_at < stale,
            )
            & (WebhookDelivery.attempts < MAX_ATTEMPTS),
        )).order_by(WebhookDelivery.available_at, WebhookDelivery.created_at).limit(1))).scalar_one_or_none()
        if not item:
            return None
        token = uuid.uuid4().hex
        claim = await db.execute(update(WebhookDelivery).where(
            WebhookDelivery.id == item.id,
            or_(
                (WebhookDelivery.status == "pending") & (WebhookDelivery.available_at <= now),
                (WebhookDelivery.status == "processing") & or_(
                    WebhookDelivery.locked_at.is_(None), WebhookDelivery.locked_at < stale,
                )
                & (WebhookDelivery.attempts < MAX_ATTEMPTS),
            ),
        ).values(status="processing", locked_at=now, lease_token=token, attempts=WebhookDelivery.attempts + 1))
        if claim.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()
        return item.id, token


async def run_webhook_worker() -> None:
    next_prune = 0.0
    while True:
        try:
            now = time.monotonic()
            if now >= next_prune:
                async with AsyncSessionLocal() as db:
                    # Retain compact ID tombstones so known expired events return 410.
                    await prune_expired_deliveries(db, now=datetime.datetime.utcnow())
                    await db.commit()
                next_prune = now + 3600
            claim = await _claim_one()
            if claim is None:
                await asyncio.sleep(1)
            else:
                await _deliver(*claim)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Webhook worker iteration failed")
            await asyncio.sleep(1)
