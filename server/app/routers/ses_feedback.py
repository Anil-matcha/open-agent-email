"""Fail-closed AWS SES feedback ingestion via signed SNS notifications."""

import base64
import datetime
import json
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Message, ProviderFeedbackReceipt, RecipientSuppression
from app.services.recipient_suppressions import canonicalize_recipient, get_recipient_suppression
from app.services.webhook_events import (
    enqueue_domain_suppression_added_webhook_event,
    enqueue_outbound_status_webhook_event,
    enqueue_suppression_added_webhook_event,
)

router = APIRouter(tags=["Provider feedback"])
MAX_BODY_BYTES = 256 * 1024
SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?$")
SNS_CERT_PATH = re.compile(r"^/SimpleNotificationService-[A-Za-z0-9]+\.pem$")
MESSAGE_ID_HEADER = "x-open-agent-email-message-id"


def _sns_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https" and bool(parsed.hostname)
            and SNS_HOST.fullmatch(parsed.hostname) is not None
            and parsed.port is None and parsed.username is None and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        return False


def _canonical_message(m: dict) -> bytes:
    typ = m.get("Type")
    if typ == "Notification":
        fields = ["Message", "MessageId"]
        if m.get("Subject"):
            fields.append("Subject")
        fields.extend(["Timestamp", "TopicArn", "Type"])
    elif typ in {"SubscriptionConfirmation", "UnsubscribeConfirmation"}:
        fields = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]
    else:
        raise ValueError("unsupported SNS message type")
    return "".join(f"{key}\n{m.get(key, '')}\n" for key in fields).encode("utf-8")


async def _verify_sns(m: dict) -> None:
    expected_topic = settings.SES_FEEDBACK_TOPIC_ARN.strip()
    if not expected_topic or m.get("TopicArn") != expected_topic:
        raise HTTPException(status_code=403, detail="Untrusted feedback topic")
    cert_url = m.get("SigningCertURL")
    if not isinstance(cert_url, str) or not _sns_url(cert_url):
        raise HTTPException(status_code=403, detail="Invalid SNS signing certificate URL")
    arn_parts = expected_topic.split(":", 5)
    parsed_cert = urlsplit(cert_url)
    if len(arn_parts) != 6 or arn_parts[2] != "sns":
        raise HTTPException(status_code=503, detail="Configured SNS topic ARN is invalid")
    suffix = ".amazonaws.com.cn" if arn_parts[1] == "aws-cn" else ".amazonaws.com"
    if parsed_cert.hostname != f"sns.{arn_parts[3]}{suffix}" or not SNS_CERT_PATH.fullmatch(parsed_cert.path):
        raise HTTPException(status_code=403, detail="SNS certificate region does not match the configured topic")
    try:
        timestamp = datetime.datetime.fromisoformat(m["Timestamp"].replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()
        if age < -300 or age > 30 * 86400:
            raise ValueError("stale SNS timestamp")
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            async with client.stream("GET", cert_url) as response:
                response.raise_for_status()
                cert_body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    cert_body.extend(chunk)
                    if len(cert_body) > 1024 * 1024:
                        raise ValueError("oversize SNS certificate")
                pem = bytes(cert_body)
        cert = x509.load_pem_x509_certificate(pem)
        now = datetime.datetime.now(datetime.timezone.utc)
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
            raise ValueError("expired SNS certificate")
        public_key = cert.public_key()
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ValueError("SNS signing key is not RSA")
        signature = base64.b64decode(m["Signature"], validate=True)
        version = m.get("SignatureVersion")
        digest = hashes.SHA1() if version == "1" else hashes.SHA256() if version == "2" else None
        if digest is None:
            raise ValueError("unsupported SNS signature version")
        public_key.verify(signature, _canonical_message(m), padding.PKCS1v15(), digest)
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, InvalidSignature, httpx.HTTPError) as exc:
        raise HTTPException(status_code=403, detail="SNS signature verification failed") from exc


async def _confirm_subscription(m: dict) -> None:
    url = m.get("SubscribeURL")
    parsed = urlsplit(url) if isinstance(url, str) else None
    parts = settings.SES_FEEDBACK_TOPIC_ARN.strip().split(":", 5)
    suffix = ".amazonaws.com.cn" if len(parts) == 6 and parts[1] == "aws-cn" else ".amazonaws.com"
    expected_host = f"sns.{parts[3]}{suffix}" if len(parts) == 6 else ""
    if (
        not parsed or not _sns_url(url) or parsed.hostname != expected_host
        or parsed.path not in {"", "/"}
        or parse_qs(parsed.query).get("Action") != ["ConfirmSubscription"]
    ):
        raise HTTPException(status_code=403, detail="Invalid SNS confirmation URL")
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        async with client.stream("GET", url) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise HTTPException(status_code=502, detail="SNS subscription confirmation failed")
            async for _ in response.aiter_bytes(chunk_size=1024):
                break


async def _apply_feedback(db: AsyncSession, event: dict) -> None:
    event_type = event.get("eventType") or event.get("notificationType")
    if event_type == "Bounce":
        bounce = event.get("bounce") or {}
        recipients = [r.get("emailAddress") for r in bounce.get("bouncedRecipients", []) if isinstance(r, dict)]
        bounce_type = str(bounce.get("bounceType", "")).lower()
        status = "bounced"
        reason = "SES permanent bounce" if bounce_type == "permanent" else "SES transient bounce"
        webhook_type = "email.bounced"
    elif event_type == "Complaint":
        complaint = event.get("complaint") or {}
        recipients = [r.get("emailAddress") for r in complaint.get("complainedRecipients", []) if isinstance(r, dict)]
        reason = "SES complaint"
        status = "complained"
        webhook_type = "email.complained"
    elif event_type == "Delivery":
        delivery = event.get("delivery") or {}
        recipients = delivery.get("recipients", [])
        reason = ""
        status = "delivered"
        webhook_type = "email.delivered"
    elif event_type == "DeliveryDelay":
        delay = event.get("deliveryDelay") or {}
        recipients = delay.get("delayedRecipients", [])
        reason = "SES delivery delayed"
        status = "delayed"
        webhook_type = "email.delivery_delayed"
    elif event_type == "Reject":
        rejected = event.get("reject") or {}
        mail = event.get("mail") or {}
        recipients = mail.get("destination", [])
        detail = rejected.get("reason")
        reason = f"SES rejected message: {detail}" if isinstance(detail, str) and detail else "SES rejected message after acceptance"
        status = "rejected"
        webhook_type = "email.failed"
    else:
        return

    if not isinstance(recipients, list):
        return
    recipients = [
        item.get("emailAddress") if isinstance(item, dict) else item
        for item in recipients
    ]

    mail = event.get("mail")
    headers = mail.get("headers") or [] if isinstance(mail, dict) else []
    correlation_id = next((
        str(h.get("value", "")).strip()
        for h in headers if isinstance(h, dict) and str(h.get("name", "")).lower() == MESSAGE_ID_HEADER
    ), "")
    if not correlation_id or len(correlation_id) > 64:
        return
    message = await db.scalar(select(Message).where(
        Message.id == correlation_id,
        Message.direction == "outbound",
    ))
    if message is None:
        return
    for raw_recipient in recipients:
        if not isinstance(raw_recipient, str):
            continue
        try:
            recipient = canonicalize_recipient(raw_recipient)
            expected_recipient = canonicalize_recipient(message.recipient)
        except ValueError:
            continue
        # Only apply an address that exactly matches the correlated outbound message.
        if recipient != expected_recipient:
            continue
        current = message.provider_delivery_status
        # Provider notifications can arrive out of order. Preserve compliance
        # outcomes over informational success/delay notifications.
        if current not in {"bounced", "complained", "rejected"} or status in {"bounced", "complained", "rejected"}:
            precedence = {"delayed": 1, "delivered": 2, "rejected": 3, "bounced": 4, "complained": 5}
            if current is None or precedence.get(status, 0) >= precedence.get(current, 0):
                message.provider_delivery_status = status
                bounded_reason = " ".join(str(reason).split())[:500] if reason else None
                message.provider_delivery_error = bounded_reason
        await enqueue_outbound_status_webhook_event(
            db,
            event_type=webhook_type,
            message_id=message.id,
            agent_email=message.agent_email,
            recipient=recipient,
            status=status,
            created_at=datetime.datetime.utcnow(),
            conversation_id=message.conversation_id,
        )
        # Only permanent bounces and complaints are terminal opt-outs.
        should_suppress = event_type == "Complaint" or (event_type == "Bounce" and bounce_type == "permanent")
        if not should_suppress or await get_recipient_suppression(db, message.agent_email, recipient):
            continue
        db.add(RecipientSuppression(
            id=f"sup_{uuid.uuid4().hex[:24]}", agent_email=message.agent_email,
            email=recipient, reason=reason,
        ))
        await enqueue_suppression_added_webhook_event(
            db, agent_email=message.agent_email, recipient=recipient,
            source="complaint" if event_type == "Complaint" else "bounce",
            reason=reason, created_at=datetime.datetime.utcnow(),
        )
        await enqueue_domain_suppression_added_webhook_event(
            db, agent_email=message.agent_email, address=recipient,
            source="complaint" if event_type == "Complaint" else "bounce",
            reason=reason, message_id=message.id, created_at=datetime.datetime.utcnow(),
        )


@router.post("/webhooks/ses", include_in_schema=True)
async def receive_ses_feedback(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.SES_FEEDBACK_TOPIC_ARN.strip():
        raise HTTPException(status_code=404, detail="SES feedback is not configured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="SNS request is too large")
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict):
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid SNS JSON") from exc
    await _verify_sns(envelope)
    event_id = envelope.get("MessageId")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 255:
        raise HTTPException(status_code=400, detail="Invalid SNS message ID")
    if envelope.get("Type") == "SubscriptionConfirmation":
        await _confirm_subscription(envelope)
        return Response(status_code=200)
    if envelope.get("Type") != "Notification":
        return Response(status_code=200)
    try:
        event = json.loads(envelope.get("Message", ""))
        if not isinstance(event, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid SES feedback payload") from exc

    db.add(ProviderFeedbackReceipt(provider_event_id=event_id, provider="ses"))
    try:
        await _apply_feedback(db, event)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(ProviderFeedbackReceipt).where(
            ProviderFeedbackReceipt.provider_event_id == event_id
        ))
        if existing is not None:
            # SNS retries are expected; its globally unique MessageId is the dedup key.
            return Response(status_code=200)
        raise HTTPException(status_code=500, detail="Could not persist SES feedback")
    except Exception:
        await db.rollback()
        raise
    return Response(status_code=200)
