"""
Real-world Inbound SMTP Receiver Daemon for Open Agent Email.
Listens on TCP port 2525 (in dev/local) or port 25 (in production) to receive real SMTP emails
from external mail transfer agents (MTAs) such as Gmail, Outlook, or test scripts.
"""

import asyncio
import email
import hashlib
from email import policy
from email.utils import parseaddr
import logging
import re
import ssl
import sys
import os

# Add server directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal
from app.models import Agent
from app.services.email_service import EmailService
from app.services.mail_auth import evaluate_inbound_authentication, inbound_dmarc_disposition
from app.services.attachment_scan import (
    AttachmentInfectedError,
    AttachmentScanLimitError,
    AttachmentScannerUnavailableError,
)
from sqlalchemy import select, func

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SMTP] %(message)s")
logger = logging.getLogger("smtp_receiver")

PORT = int(os.environ.get("SMTP_PORT", 2525))
HOST = os.environ.get("SMTP_HOST", "127.0.0.1")
MAX_MESSAGE_BYTES = int(os.environ.get("SMTP_MAX_MESSAGE_BYTES", 20 * 1024 * 1024))
MAX_ATTACHMENT_BYTES = int(os.environ.get("SMTP_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024))
MAX_ATTACHMENTS = int(os.environ.get("SMTP_MAX_ATTACHMENTS", 10))
READ_TIMEOUT_SECONDS = float(os.environ.get("SMTP_READ_TIMEOUT_SECONDS", 60))
DATA_TIMEOUT_SECONDS = float(os.environ.get("SMTP_DATA_TIMEOUT_SECONDS", 300))
MAX_CONNECTIONS = int(os.environ.get("SMTP_MAX_CONNECTIONS", 100))
STREAM_LINE_LIMIT = int(os.environ.get("SMTP_LINE_LIMIT_BYTES", 16 * 1024))
TLS_CERT_FILE = os.environ.get("SMTP_TLS_CERT_FILE", "").strip()
TLS_KEY_FILE = os.environ.get("SMTP_TLS_KEY_FILE", "").strip()
REQUIRE_STARTTLS = os.environ.get("SMTP_REQUIRE_STARTTLS", "false").strip().lower() in {"1", "true", "yes"}
if not 1 <= PORT <= 65535:
    raise ValueError("SMTP_PORT must be between 1 and 65535")
if min(
    READ_TIMEOUT_SECONDS,
    DATA_TIMEOUT_SECONDS,
    MAX_CONNECTIONS,
    STREAM_LINE_LIMIT,
    MAX_MESSAGE_BYTES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
) <= 0:
    raise ValueError("SMTP limits and timeouts must be greater than zero")
if MAX_ATTACHMENT_BYTES > MAX_MESSAGE_BYTES:
    raise ValueError("SMTP_MAX_ATTACHMENT_BYTES cannot exceed SMTP_MAX_MESSAGE_BYTES")
if bool(TLS_CERT_FILE) != bool(TLS_KEY_FILE):
    raise ValueError("Set both SMTP_TLS_CERT_FILE and SMTP_TLS_KEY_FILE to enable STARTTLS")
TLS_CONTEXT = None
if TLS_CERT_FILE and TLS_KEY_FILE:
    TLS_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    TLS_CONTEXT.load_cert_chain(TLS_CERT_FILE, TLS_KEY_FILE)
_ACTIVE_SMTP_CONNECTIONS = 0

def safe_attachment_name(filename: str) -> str:
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(ch if ch >= " " and ch not in "\x7f" else "_" for ch in filename)
    return filename[:255] or "attachment"

def safe_content_type(content_type: str) -> str:
    import re
    if re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", content_type or ""):
        return content_type
    return "application/octet-stream"


def extract_smtp_path(line: str, command: str) -> str | None:
    """Parse an SMTP reverse/forward path while ignoring ESMTP parameters."""
    match = re.match(rf"^{re.escape(command)}:\s*(<[^>\r\n]*>|[^\s]+)(?:\s+.*)?$", line, re.IGNORECASE)
    if not match:
        return None
    path = match.group(1)
    if path.startswith("<"):
        if not path.endswith(">"):
            return None
        return path[1:-1]
    if any(char in path for char in "<>"):
        return None
    return path


class SmtpMessageRejected(ValueError):
    """A permanent size or MIME-policy rejection suitable for SMTP 552."""


class SmtpPolicyRejected(ValueError):
    """A permanent sender-authentication policy rejection suitable for SMTP 550."""


class SmtpHandler:
    """Async SMTP hooks that ingest validated messages into agent inboxes."""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Agent).where(
                func.lower(Agent.email) == address.lower(),
                Agent.status == "active",
                Agent.deleted_at.is_(None),
            ))
            known_agent = res.scalar_one_or_none()
        if not known_agent:
            return "550 No active agent inbox for recipient"
        # Store the canonical inbox address, which also ensures duplicate RCPTs
        # differing only in case are deduplicated during delivery.
        envelope.rcpt_tos.append(known_agent.email)
        return "250 Recipient OK"

    async def handle_DATA(self, server, session, envelope):
        raw_bytes = envelope.original_content
        if isinstance(raw_bytes, str):
            raw_bytes = raw_bytes.encode("utf-8")
        try:
            timeout_handle = getattr(server, "_data_timeout_handle", None)
            if timeout_handle:
                timeout_handle.cancel()
                server._data_timeout_handle = None
            await self.process_email(raw_bytes, envelope, session)
        except SmtpMessageRejected as exc:
            return f"552 {exc}"
        except SmtpPolicyRejected as exc:
            return f"550 {exc}"
        except AttachmentInfectedError:
            return "554 Attachment rejected by malware scanner"
        except AttachmentScanLimitError as exc:
            return f"552 {exc.detail}"
        except AttachmentScannerUnavailableError:
            return "451 Attachment scanner temporarily unavailable; please retry"
        except Exception:
            logger.exception("Transient SMTP message processing failure")
            return "451 Temporary message processing failure; please retry"
        return "250 Message accepted for agent delivery"

    async def process_email(self, raw_bytes: bytes, envelope, session):
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
        sender_header = str(msg.get("From", envelope.mail_from or "unknown@sender.com"))
        sender = parseaddr(sender_header)[1] or sender_header
        subject = msg.get("Subject", "(No Subject)")
        raw_message_id = str(msg.get("Message-ID", "")).strip() or None
        in_reply_to = str(msg.get("In-Reply-To", "")).strip() or None
        references = str(msg.get("References", "")).strip() or None

        # Extract text and html
        body_text = ""
        body_html = None
        attachments = []
        attachment_total = 0

        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                ctype = part.get_content_type()
                filename = part.get_filename()
                disposition = part.get_content_disposition()
                if filename or disposition == "attachment":
                    payload = part.get_payload(decode=True) or b""
                    if len(attachments) >= MAX_ATTACHMENTS:
                        raise SmtpMessageRejected(f"Message contains more than {MAX_ATTACHMENTS} attachments")
                    if len(payload) > MAX_ATTACHMENT_BYTES:
                        raise SmtpMessageRejected(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} byte limit")
                    attachment_total += len(payload)
                    if attachment_total > MAX_MESSAGE_BYTES:
                        raise SmtpMessageRejected("Attachments exceed configured total size limit")
                    attachments.append({"filename": safe_attachment_name(filename or "attachment"), "content_type": safe_content_type(ctype), "content": payload})
                elif ctype == "text/plain" and not body_text:
                    payload = part.get_payload(decode=True)
                    body_text = payload.decode("utf-8", errors="ignore") if payload else ""
                elif ctype == "text/html" and not body_html:
                    payload = part.get_payload(decode=True)
                    body_html = payload.decode("utf-8", errors="ignore") if payload else ""
        else:
            payload = msg.get_payload(decode=True)
            filename = msg.get_filename()
            if filename or msg.get_content_disposition() == "attachment":
                payload = payload or b""
                if len(payload) > MAX_ATTACHMENT_BYTES:
                    raise SmtpMessageRejected(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} byte limit")
                if len(attachments) >= MAX_ATTACHMENTS:
                    raise SmtpMessageRejected(f"Message contains more than {MAX_ATTACHMENTS} attachments")
                attachments.append({"filename": safe_attachment_name(filename or "attachment"), "content_type": safe_content_type(msg.get_content_type()), "content": payload})
            else:
                body_text = payload.decode("utf-8", errors="ignore") if payload else ""

        if not body_text and body_html:
            body_text = body_html

        spf_status, dkim_status, dmarc_status, auth_evidence = await evaluate_inbound_authentication(
            raw_message=raw_bytes,
            peer_ip=session.peer[0] if isinstance(session.peer, tuple) and session.peer else None,
            mail_from=envelope.mail_from,
            helo_domain=getattr(session, "host_name", "") or "",
        )
        dmarc_disposition = inbound_dmarc_disposition(dmarc_status, auth_evidence)
        if dmarc_disposition == "reject":
            raise SmtpPolicyRejected("DMARC policy rejects this unauthenticated message")
        inbound_dedup_hash = hashlib.sha256(
            envelope.mail_from.strip().lower().encode("utf-8", errors="replace") + b"\0" + raw_bytes
        ).hexdigest()

        # An SMTP transaction can have multiple RCPT TO commands. Deliver one
        # copy into each accepted inbox and avoid duplicate copies for repeated RCPTs.
        for target_recipient in dict.fromkeys(envelope.rcpt_tos):
            logger.info(f"Ingesting real SMTP mail from '{sender}' to '{target_recipient}'")
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Agent).where(Agent.email == target_recipient, Agent.deleted_at.is_(None)))
                agent = res.scalar_one_or_none()
                if not agent:
                    logger.warning(f"No configured agent found for recipient {target_recipient}. Dropping recipient copy.")
                    continue
                message, scan_result, review = await EmailService.process_inbound(
                    db=db,
                    agent=agent,
                    sender=sender,
                    subject=subject,
                    body_text=body_text or "(Empty Body)",
                    body_html=body_html,
                    spf_status=spf_status,
                    dkim_status=dkim_status,
                    dmarc_status=dmarc_status,
                    auth_evidence=auth_evidence,
                    force_review=dmarc_disposition == "quarantine",
                    inbound_dedup_hash=inbound_dedup_hash,
                    raw_message_id=raw_message_id,
                    in_reply_to=in_reply_to,
                    references=references,
                    attachments=attachments,
                )
                logger.info(f"Delivered message {message.id} to agent {agent.email}. Verdict: {scan_result.verdict}")

def _smtp_protocol_factory():
    from aiosmtpd.smtp import SMTP

    class ManagedSMTP(SMTP):
        # aiosmtpd 1.4.x exposes this as a class attribute, not a constructor
        # option. Keep the historic receiver's explicit line ceiling.
        line_length_limit = STREAM_LINE_LIMIT

        def connection_made(self, transport):
            global _ACTIVE_SMTP_CONNECTIONS
            # STARTTLS can call connection_made again on the same SMTP
            # protocol after the transport is upgraded. Count the session,
            # rather than each transport attached to it.
            if getattr(self, "_connection_counted", False):
                super().connection_made(transport)
                return
            if _ACTIVE_SMTP_CONNECTIONS >= MAX_CONNECTIONS:
                self._connection_admitted = False
                transport.write(b"421 Too many SMTP connections; try again later\r\n")
                transport.close()
                return
            _ACTIVE_SMTP_CONNECTIONS += 1
            self._connection_counted = True
            self._connection_admitted = True
            try:
                super().connection_made(transport)
            except BaseException:
                _ACTIVE_SMTP_CONNECTIONS -= 1
                self._connection_counted = False
                self._connection_admitted = False
                raise

        def connection_lost(self, exc):
            global _ACTIVE_SMTP_CONNECTIONS
            if not getattr(self, "_connection_admitted", False):
                return
            timeout_handle = getattr(self, "_data_timeout_handle", None)
            if timeout_handle:
                timeout_handle.cancel()
                self._data_timeout_handle = None
            if getattr(self, "_connection_counted", False):
                self._connection_counted = False
                self._connection_admitted = False
                _ACTIVE_SMTP_CONNECTIONS = max(0, _ACTIVE_SMTP_CONNECTIONS - 1)
            super().connection_lost(exc)

        async def push(self, status):
            await super().push(status)
            if isinstance(status, str) and status.startswith("354"):
                loop = asyncio.get_running_loop()
                self._data_started_at = loop.time()
                previous = getattr(self, "_data_timeout_handle", None)
                if previous:
                    previous.cancel()
                self._data_timeout_handle = loop.call_later(DATA_TIMEOUT_SECONDS, self._expire_data)

        def _expire_data(self):
            # DATA has no command boundary until the terminating dot. Send a
            # temporary failure before closing, even if the peer stays active.
            transport = getattr(self, "transport", None)
            if transport is not None and not transport.is_closing():
                asyncio.create_task(self._send_data_timeout())

        async def _send_data_timeout(self):
            try:
                await self.push("421 Message input timed out")
            finally:
                transport = getattr(self, "transport", None)
                if transport is not None:
                    transport.close()

    handler = SmtpHandler()
    loop = asyncio.get_running_loop()
    protocol_options = {
        "data_size_limit": MAX_MESSAGE_BYTES,
        "enable_SMTPUTF8": False,
        "decode_data": False,
        "hostname": "open-agent-email",
        "tls_context": TLS_CONTEXT,
        "require_starttls": REQUIRE_STARTTLS,
        "timeout": READ_TIMEOUT_SECONDS,
        "loop": loop,
    }
    return ManagedSMTP(handler, **protocol_options)


async def start_smtp_server(host: str = HOST, port: int = PORT):
    if REQUIRE_STARTTLS and TLS_CONTEXT is None:
        raise ValueError("SMTP_REQUIRE_STARTTLS requires SMTP_TLS_CERT_FILE and SMTP_TLS_KEY_FILE")
    server = await asyncio.get_running_loop().create_server(
        _smtp_protocol_factory,
        host,
        port,
    )
    logger.info("Open Agent Email SMTP Receiver listening on %s:%s", host, port)
    return server


async def main():
    server = await start_smtp_server()
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
