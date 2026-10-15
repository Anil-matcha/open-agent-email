"""Server settings that an administrator can change from the dashboard.

Precedence for an editable setting: a value saved here (table `server_settings`) beats the
environment, which beats the built-in default. Saved values are copied onto the live `settings`
object when saved and again at startup, so code that reads `settings.X` at call time sees them
immediately. Settings that are read only once at startup (ports, middleware limits, the database
URL) are listed read-only. Secrets are never returned, only whether they are configured.
"""

import datetime
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.models import Domain, ServerSetting, SmtpConfig
from app.services.unsubscribe import normalize_public_base_url

_HOSTNAME_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_SNS_ARN = re.compile(r"^arn:aws[a-z-]*:sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9_-]{1,256}$")
_CONFIG_SET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MIB = 1024 * 1024


class SettingError(ValueError):
    """A submitted value is not acceptable."""


def _hostname(value: str, *, allow_empty: bool) -> str:
    value = value.strip().lower().rstrip(".")
    if not value:
        if allow_empty:
            return ""
        raise SettingError("must not be empty")
    labels = value.split(".")
    if len(value) > 253 or not all(_HOSTNAME_LABEL.match(label) for label in labels):
        raise SettingError("must be a hostname such as mail.example.com")
    return value


def _optional_host(value: str) -> str:
    return _hostname(value, allow_empty=True)


def _required_host(value: str) -> str:
    return _hostname(value, allow_empty=False)


def _public_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    normalized = normalize_public_base_url(value)
    if normalized is None:
        raise SettingError("must be a public https:// origin on port 443 without credentials, query or fragment")
    return normalized


def _topic_arn(value: str) -> str:
    value = value.strip()
    if value and not _SNS_ARN.match(value):
        raise SettingError("must be an SNS topic ARN such as arn:aws:sns:us-east-1:123456789012:ses-feedback")
    return value


def _config_set(value: str) -> str:
    value = value.strip()
    if value and not _CONFIG_SET.match(value):
        raise SettingError("may contain letters, digits, hyphens and underscores (up to 64 characters)")
    return value


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    label: str
    description: str
    type: str  # "string" | "integer"
    clean: Callable[[Any], Any]
    minimum: int | None = None
    maximum: int | None = None
    example: str = ""


def _int_cleaner(minimum: int, maximum: int) -> Callable[[Any], int]:
    def clean(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingError("must be a whole number")
        if not minimum <= value <= maximum:
            raise SettingError(f"must be between {minimum} and {maximum}")
        return value
    return clean


def _str_cleaner(inner: Callable[[str], str]) -> Callable[[Any], str]:
    def clean(value: Any) -> str:
        if not isinstance(value, str):
            raise SettingError("must be text")
        return inner(value)
    return clean


def _spec_int(key, group, label, description, minimum, maximum, example=""):
    return Spec(key, group, label, description, "integer", _int_cleaner(minimum, maximum), minimum, maximum, example)


def _spec_str(key, group, label, description, inner, example=""):
    return Spec(key, group, label, description, "string", _str_cleaner(inner), example=example)


EDITABLE: tuple[Spec, ...] = (
    _spec_str("PUBLIC_BASE_URL", "Links", "Public base URL",
              "The public https address of this server. Used for unsubscribe and review links in email; links are omitted while it is empty.",
              _public_url, "https://mail.example.com"),
    _spec_str("SHARED_DOMAIN", "Domains", "Shared domain",
              "The default domain for inbox addresses and the base for the MX target when no mail hostname is set.",
              _required_host, "agents.example.com"),
    _spec_str("MAIL_HOSTNAME", "Domains", "Mail hostname",
              "The host that runs the SMTP listener, published as the MX target for custom domains. Empty means mx.<shared domain>.",
              _optional_host, "mx.example.com"),
    _spec_str("SPF_INCLUDE", "Domains", "SPF include",
              "Optional SPF include for your outbound relay, for example amazonses.com. Empty publishes a plain mx record.",
              _optional_host, "amazonses.com"),
    _spec_str("SES_FEEDBACK_TOPIC_ARN", "Amazon SES", "SES feedback topic",
              "The one SNS topic trusted for delivery, bounce and complaint notifications. Empty turns SES feedback off.",
              _topic_arn, "arn:aws:sns:us-east-1:123456789012:ses-feedback"),
    _spec_str("SES_CONFIGURATION_SET", "Amazon SES", "SES configuration set",
              "Added to outbound mail so SES publishes events through that set's destination.",
              _config_set, "my-config-set"),
    _spec_int("TRASH_RETENTION_DAYS", "Retention", "Trash retention (days)",
              "Trashed messages are permanently removed after this many days. 0 keeps them until deleted by hand.", 0, 3650),
    _spec_int("WEBHOOK_DELIVERY_RETENTION_DAYS", "Retention", "Webhook delivery retention (days)",
              "Delivery payloads and retry details are erased after this many days.", 1, 3650),
    _spec_int("OUTBOUND_MAX_ATTACHMENTS", "Limits", "Attachments per message",
              "Most files one outgoing message may carry.", 1, 50),
    _spec_int("OUTBOUND_MAX_ATTACHMENT_BYTES", "Limits", "Largest attachment (bytes)",
              "Largest single outgoing attachment.", 1, 100 * MIB),
    _spec_int("OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES", "Limits", "Total attachments (bytes)",
              "Largest combined size of attachments on one outgoing message.", 1, 200 * MIB),
    _spec_int("OUTBOUND_MAX_COMPOSED_BYTES", "Limits", "Largest composed message (bytes)",
              "Largest outgoing message after encoding. The request size cap (read-only below) must also allow it.", 1, 200 * MIB),
)
EDITABLE_BY_KEY = {spec.key: spec for spec in EDITABLE}

# Read once at startup (listening sockets, middleware, database engine), so shown but not editable.
READ_ONLY: tuple[tuple[str, str, str], ...] = (
    ("HOST", "Server", "HTTP bind address"),
    ("PORT", "Server", "HTTP port"),
    ("DATABASE_URL", "Server", "Database (password hidden)"),
    ("SMTP_HOST", "Server", "Mail listener bind address"),
    ("SMTP_PORT", "Server", "Mail listener port"),
    ("API_RATE_LIMIT_REQUESTS", "Limits", "API requests allowed per window"),
    ("API_RATE_LIMIT_WINDOW_SECONDS", "Limits", "API rate-limit window (seconds)"),
    ("HTTP_MAX_REQUEST_BYTES", "Limits", "Largest request body (bytes)"),
    ("HTTP_MAX_CONCURRENCY", "Limits", "Concurrent connections allowed"),
    ("CORS_ORIGINS", "Server", "Allowed dashboard origins"),
)

SECRETS: tuple[tuple[str, str], ...] = (
    ("ADMIN_API_KEY", "Administrator key"),
    ("SMTP_CONFIG_ENCRYPTION_KEY", "Encryption key for stored credentials"),
    ("METRICS_TOKEN", "Metrics token"),
    ("ATTACHMENT_URL_SIGNING_KEY", "Attachment link signing key"),
)

# What the environment and defaults say, captured before any saved override is applied.
_BASELINE: dict[str, Any] = {spec.key: getattr(settings, spec.key) for spec in EDITABLE}
_ENV_SET: set[str] = set(settings.model_fields_set)


def _default(key: str) -> Any:
    field = Settings.model_fields.get(key)
    return field.default if field is not None else None


def _smtp_listener_value(key: str) -> Any:
    try:
        import smtp_receiver
        return smtp_receiver.HOST if key == "SMTP_HOST" else smtp_receiver.PORT
    except Exception:  # pragma: no cover - the receiver module is always importable in the app
        return None


def _read_only_value(key: str) -> Any:
    if key in {"SMTP_HOST", "SMTP_PORT"}:
        return _smtp_listener_value(key)
    value = getattr(settings, key, None)
    if key == "DATABASE_URL" and isinstance(value, str):
        try:
            return make_url(value).render_as_string(hide_password=True)
        except Exception:
            return "(hidden)"
    return value


def _merged_error(effective: dict[str, Any]) -> tuple[str, str] | None:
    if effective["OUTBOUND_MAX_ATTACHMENT_BYTES"] > effective["OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES"]:
        return "OUTBOUND_MAX_ATTACHMENT_BYTES", "must not be larger than the total attachment limit"
    return None


def validate_updates(values: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return (cleaned values, errors). A value of None removes the saved override."""
    cleaned: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    read_only = {key for key, _, _ in READ_ONLY}
    secrets = {key for key, _ in SECRETS}
    for key, value in values.items():
        if key in secrets:
            errors.append({"key": key, "message": "secrets are set in the environment and cannot be changed here"})
        elif key in read_only:
            errors.append({"key": key, "message": "requires a restart; change it in the environment"})
        elif key not in EDITABLE_BY_KEY:
            errors.append({"key": key, "message": "unknown setting"})
        elif value is None:
            cleaned[key] = None
        else:
            try:
                cleaned[key] = EDITABLE_BY_KEY[key].clean(value)
            except SettingError as exc:
                errors.append({"key": key, "message": str(exc)})
    if not errors:
        effective = {spec.key: (_BASELINE[spec.key] if cleaned.get(spec.key, ...) is None
                                else cleaned.get(spec.key, getattr(settings, spec.key)))
                     for spec in EDITABLE}
        problem = _merged_error(effective)
        if problem:
            errors.append({"key": problem[0], "message": problem[1]})
    return cleaned, errors


def _apply(key: str, value: Any) -> None:
    setattr(settings, key, _BASELINE[key] if value is None else value)


async def load_overrides(db: AsyncSession) -> int:
    """Copy saved values onto the live settings. Called at startup after migrations."""
    applied = 0
    for row in (await db.execute(select(ServerSetting))).scalars().all():
        spec = EDITABLE_BY_KEY.get(row.key)
        if spec is None:
            continue  # a setting from a newer or older version; ignore it
        try:
            _apply(row.key, spec.clean(json.loads(row.value)))
            applied += 1
        except (SettingError, ValueError):
            continue  # a value that no longer validates must not stop the server starting
    return applied


async def save_updates(db: AsyncSession, cleaned: dict[str, Any], updated_by: str) -> None:
    now = datetime.datetime.utcnow()
    for key, value in cleaned.items():
        row = await db.get(ServerSetting, key)
        if value is None:
            if row is not None:
                await db.delete(row)
        elif row is None:
            db.add(ServerSetting(key=key, value=json.dumps(value), updated_at=now, updated_by=updated_by))
        else:
            row.value, row.updated_at, row.updated_by = json.dumps(value), now, updated_by
    await db.commit()
    for key, value in cleaned.items():
        _apply(key, value)


async def describe(db: AsyncSession) -> list[dict[str, Any]]:
    saved = {row.key: row for row in (await db.execute(select(ServerSetting))).scalars().all()}
    items: list[dict[str, Any]] = []
    for spec in EDITABLE:
        row = saved.get(spec.key)
        source = "ui" if row else ("env" if spec.key in _ENV_SET else "default")
        items.append({
            "key": spec.key, "group": spec.group, "label": spec.label, "description": spec.description,
            "kind": "setting", "type": spec.type, "editable": True, "requires_restart": False,
            "value": getattr(settings, spec.key), "default": _default(spec.key), "source": source,
            "minimum": spec.minimum, "maximum": spec.maximum, "example": spec.example,
            "updated_at": row.updated_at.isoformat() if row else None, "updated_by": row.updated_by if row else None,
        })
    for key, group, label in READ_ONLY:
        value = _read_only_value(key)
        items.append({
            "key": key, "group": group, "label": label, "description": "Read at startup. Change it in the environment and restart.",
            "kind": "readonly", "type": "list" if isinstance(value, list) else ("integer" if isinstance(value, int) else "string"),
            "editable": False, "requires_restart": True, "value": value, "default": _default(key),
            "source": "env" if (key in _ENV_SET or key in os.environ) else "default",
        })
    for key, label in SECRETS:
        items.append({
            "key": key, "group": "Secrets", "label": label,
            "description": "Set in the environment. The value is never shown.",
            "kind": "secret", "type": "secret", "editable": False, "requires_restart": True,
            "configured": bool(str(getattr(settings, key, "") or "").strip()),
        })
    return items


_listener_running = False


def set_listener_running(value: bool) -> None:
    global _listener_running
    _listener_running = value


async def readiness(db: AsyncSession) -> dict[str, Any]:
    """Checklist of what a working installation needs, with where to fix each gap."""
    relay = (await db.execute(select(SmtpConfig).where(SmtpConfig.id == "default"))).scalar_one_or_none()
    verified = (await db.execute(select(Domain.domain).where(Domain.status == "verified"))).scalars().all()

    def item(id_, label, ok, severity, detail, fix=None, link=None):
        return {"id": id_, "label": label, "ok": ok, "severity": severity, "detail": detail,
                "fix": None if ok else fix, "link": None if ok else link}

    checks = [
        item("admin_key", "Administrator key is set", bool(settings.ADMIN_API_KEY.strip()), "required",
             "Used to sign in to this dashboard and manage the server.",
             "Set ADMIN_API_KEY in the environment and restart."),
        item("encryption_key", "Encryption key is set", bool(settings.SMTP_CONFIG_ENCRYPTION_KEY.strip()), "required",
             "Protects relay passwords, webhook secrets and domain signing keys at rest.",
             "Set SMTP_CONFIG_ENCRYPTION_KEY in the environment, back it up, and restart."),
        item("inbound_listener", "Mail listener is running", _listener_running, "required",
             "Accepts incoming mail over SMTP.",
             "Free the SMTP port or change SMTP_PORT in the environment, then restart."),
        item("relay", "Outgoing mail provider is configured", bool(relay and relay.enabled and relay.host), "required",
             "Needed to send to people outside this server.",
             "Add your provider's SMTP details.", "/settings"),
        item("public_url", "Public base URL is set", normalize_public_base_url(settings.PUBLIC_BASE_URL) is not None, "recommended",
             "Without it, unsubscribe and review links are left out of email.",
             "Enter your public https address.", "/settings?tab=server&key=PUBLIC_BASE_URL"),
        item("domain", "A domain is verified", bool(verified), "recommended",
             f"Verified: {', '.join(verified)}" if verified else "Lets you send and receive from your own domain.",
             "Add a domain and publish its DNS records.", "/domains"),
        item("ses_feedback", "Amazon SES feedback is connected", bool(settings.SES_FEEDBACK_TOPIC_ARN.strip()), "optional",
             "Records delivered, bounced and complained outcomes. Only needed when sending through SES.",
             "Enter your SNS feedback topic.", "/settings?tab=server&key=SES_FEEDBACK_TOPIC_ARN"),
    ]
    return {"items": checks, "ready": all(c["ok"] for c in checks if c["severity"] == "required")}
