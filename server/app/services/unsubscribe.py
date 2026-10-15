"""Public, opaque unsubscribe capabilities for external outbound email."""

import hashlib
import ipaddress
import secrets
from urllib.parse import urlsplit, urlunsplit

from app.config import settings


def public_base_url() -> str | None:
    value = settings.PUBLIC_BASE_URL.strip()
    if not value or len(value) > 2048 or "\\" in value or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in value or "#" in value
            or parsed.port not in (None, 443)
        ):
            return None
        # Avoid advertising a link that can only be reached on this host.
        host = parsed.hostname.lower().rstrip(".")
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost", ".test", ".invalid")):
            return None
        path = parsed.path.rstrip("/")
        return urlunsplit(("https", parsed.netloc, path, "", ""))
    except ValueError:
        return None


def issue_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("ascii")).hexdigest()


