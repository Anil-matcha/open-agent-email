"""Wire-level coverage for the inbound SMTP listener."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import ipaddress
import ssl
import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import smtp_receiver
from app.services.attachment_scan import (
    AttachmentInfectedError,
    AttachmentScanLimitError,
    AttachmentScannerUnavailableError,
)
from tests.conftest import ADMIN, make_agent


@pytest.fixture
async def smtp_listener(monkeypatch):
    monkeypatch.setattr(smtp_receiver, "MAX_MESSAGE_BYTES", 1024 * 1024)
    monkeypatch.setattr(smtp_receiver, "MAX_ATTACHMENT_BYTES", 1024 * 1024)
    monkeypatch.setattr(smtp_receiver, "MAX_ATTACHMENTS", 10)
    monkeypatch.setattr(smtp_receiver, "READ_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(smtp_receiver, "DATA_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(smtp_receiver, "TLS_CONTEXT", None)
    monkeypatch.setattr(smtp_receiver, "REQUIRE_STARTTLS", False)
    server = await smtp_receiver.start_smtp_server("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()
        for _ in range(20):
            if smtp_receiver._ACTIVE_SMTP_CONNECTIONS == 0:
                break
            await asyncio.sleep(0.01)
        assert smtp_receiver._ACTIVE_SMTP_CONNECTIONS == 0


def _smtp_call(port, callback):
    smtp = smtplib.SMTP("127.0.0.1", port, timeout=5)
    try:
        smtp.ehlo("test.example")
        return callback(smtp)
    finally:
        smtp.close()


def _capture_delivery(monkeypatch):
    deliveries = []

    async def process_inbound(**kwargs):
        deliveries.append(kwargs)
        return SimpleNamespace(id=len(deliveries)), SimpleNamespace(verdict="allow"), None

    monkeypatch.setattr(smtp_receiver.EmailService, "process_inbound", process_inbound)
    monkeypatch.setattr(
        smtp_receiver,
        "evaluate_inbound_authentication",
        AsyncMock(return_value=("none", "none", "none", {})),
    )
    return deliveries


async def test_ehlo_mail_rcpt_data_and_mime_attachments(client, smtp_listener, monkeypatch):
    await make_agent(client, "one@agents.local")
    deliveries = _capture_delivery(monkeypatch)

    message = EmailMessage()
    message["From"] = "sender@example.org"
    message["To"] = "ONE@agents.local"
    message["Subject"] = "Protocol test"
    message.set_content("Plain body")
    message.add_alternative("<p>HTML body</p>", subtype="html")
    message.add_attachment(b"file-data", maintype="application", subtype="octet-stream", filename="sample.bin")

    def send(smtp):
        code, ehlo = smtp.ehlo("test.example")
        assert code == 250
        assert b"SIZE" in ehlo.upper()
        assert b"8BITMIME" in ehlo.upper()
        code, _ = smtp.mail("sender@example.org", options=[f"SIZE={len(message.as_bytes())}"])
        assert code == 250
        assert smtp.rcpt("ONE@agents.local")[0] == 250
        assert smtp.rcpt("one@agents.local")[0] == 250
        return smtp.data(message.as_bytes())

    result = await asyncio.to_thread(_smtp_call, smtp_listener, send)
    assert result[0] == 250
    assert len(deliveries) == 1
    assert deliveries[0]["agent"].email == "one@agents.local"
    assert deliveries[0]["body_text"].strip() == "Plain body"
    assert "HTML body" in deliveries[0]["body_html"]
    assert deliveries[0]["attachments"] == [{
        "filename": "sample.bin",
        "content_type": "application/octet-stream",
        "content": b"file-data",
    }]


async def test_dmarc_reject_policy_returns_550_without_persistence(client, smtp_listener, monkeypatch):
    await make_agent(client, "reject@agents.local")
    deliveries = _capture_delivery(monkeypatch)
    evidence = {"dmarc": {"status": "fail", "policy": {"domain": "example.org", "p": "reject", "effective": "reject"}}}
    monkeypatch.setattr(
        smtp_receiver, "evaluate_inbound_authentication",
        AsyncMock(return_value=("fail", "none", "fail", evidence)),
    )
    raw = b"From: sender@example.org\r\nSubject: reject\r\n\r\nbody\r\n"

    def send(smtp):
        smtp.mail("bounce@example.org")
        smtp.rcpt("reject@agents.local")
        return smtp.data(raw)

    assert (await asyncio.to_thread(_smtp_call, smtp_listener, send))[0] == 550
    assert deliveries == []


async def test_dmarc_quarantine_policy_creates_pending_review(client, smtp_listener, monkeypatch):
    await make_agent(client, "quarantine@agents.local")
    deliveries = _capture_delivery(monkeypatch)
    evidence = {"dmarc": {"status": "fail", "policy": {"domain": "example.org", "p": "quarantine", "effective": "quarantine"}}}
    monkeypatch.setattr(
        smtp_receiver, "evaluate_inbound_authentication",
        AsyncMock(return_value=("fail", "none", "fail", evidence)),
    )
    raw = b"From: sender@example.org\r\nSubject: quarantine\r\n\r\nbody\r\n"

    def send(smtp):
        smtp.mail("bounce@example.org")
        smtp.rcpt("quarantine@agents.local")
        return smtp.data(raw)

    assert (await asyncio.to_thread(_smtp_call, smtp_listener, send))[0] == 250
    assert len(deliveries) == 1
    assert deliveries[0]["force_review"] is True


async def test_unknown_and_soft_deleted_recipients_are_rejected(client, smtp_listener):
    await make_agent(client, "deleted@agents.local")
    response = await client.delete("/v1/agents/deleted@agents.local", headers=ADMIN)
    assert response.status_code == 204

    def check_recipients(smtp):
        assert smtp.mail("sender@example.org")[0] == 250
        assert smtp.rcpt("missing@agents.local")[0] == 550
        assert smtp.rcpt("deleted@agents.local")[0] == 550
        with pytest.raises(smtplib.SMTPDataError) as exc:
            smtp.data(b"Subject: rejected\r\n\r\nbody\r\n")
        assert exc.value.smtp_code == 503

    await asyncio.to_thread(_smtp_call, smtp_listener, check_recipients)


async def test_rset_clears_transaction_and_quit_closes_session(smtp_listener):
    def commands(smtp):
        assert smtp.mail("sender@example.org")[0] == 250
        assert smtp.rcpt("recipient@agents.local")[0] == 550
        assert smtp.rset()[0] == 250
        with pytest.raises(smtplib.SMTPDataError) as exc:
            smtp.data(b"Subject: no transaction\r\n\r\nbody\r\n")
        assert exc.value.smtp_code == 503
        return smtp.quit()[0]

    assert await asyncio.to_thread(_smtp_call, smtp_listener, commands) == 221


async def test_raw_data_size_limit_is_enforced(client, smtp_listener, monkeypatch):
    await make_agent(client, "size@agents.local")
    monkeypatch.setattr(smtp_receiver, "MAX_MESSAGE_BYTES", 120)
    # Aiosmtpd reads the configured bound when each protocol instance is made.
    deliveries = _capture_delivery(monkeypatch)

    def send(smtp):
        smtp.mail("sender@example.org")
        smtp.rcpt("size@agents.local")
        return smtp.data(b"Subject: large\r\n\r\n" + (b"x" * 500) + b"\r\n")

    response = await asyncio.to_thread(_smtp_call, smtp_listener, send)
    assert response[0] == 552
    assert deliveries == []


async def test_attachment_limit_is_enforced_as_data_reply(client, smtp_listener, monkeypatch):
    await make_agent(client, "attachment@agents.local")
    monkeypatch.setattr(smtp_receiver, "MAX_ATTACHMENT_BYTES", 3)
    message = EmailMessage()
    message["From"] = "sender@example.org"
    message.set_content("body")
    message.add_attachment(b"four", maintype="application", subtype="octet-stream", filename="large.bin")
    deliveries = _capture_delivery(monkeypatch)

    def send(smtp):
        smtp.mail("sender@example.org")
        smtp.rcpt("attachment@agents.local")
        return smtp.data(message.as_bytes())

    response = await asyncio.to_thread(_smtp_call, smtp_listener, send)
    assert response[0] == 552
    assert b"Attachment exceeds" in response[1]
    assert deliveries == []


def _write_test_certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256()))
    key_path = tmp_path / "smtp-test-key.pem"
    cert_path = tmp_path / "smtp-test-cert.pem"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


async def test_starttls_negotiates_and_required_policy_is_enforced(monkeypatch, tmp_path):
    key_path, cert_path = _write_test_certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    monkeypatch.setattr(smtp_receiver, "TLS_CONTEXT", context)
    monkeypatch.setattr(smtp_receiver, "REQUIRE_STARTTLS", True)
    server = await smtp_receiver.start_smtp_server("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        def negotiate(smtp):
            _, features = smtp.ehlo("test.example")
            assert b"STARTTLS" in features.upper()
            code, _ = smtp.mail("sender@example.org")
            assert code == 530
            smtp.starttls(context=ssl._create_unverified_context())
            smtp.ehlo("test.example")
            assert smtp.rset()[0] == 250
            assert smtp.mail("sender@example.org")[0] == 250
            return smtp.quit()[0]

        assert await asyncio.to_thread(_smtp_call, port, negotiate) == 221
    finally:
        server.close()
        await server.wait_closed()
        for _ in range(20):
            if smtp_receiver._ACTIVE_SMTP_CONNECTIONS == 0:
                break
            await asyncio.sleep(0.01)
        assert smtp_receiver._ACTIVE_SMTP_CONNECTIONS == 0


async def test_starttls_requirement_fails_fast_without_certificate(monkeypatch):
    # No test certificate is provisioned in the repository. Required TLS must
    # fail at listener startup rather than accepting connections that cannot
    # negotiate TLS.
    monkeypatch.setattr(smtp_receiver, "REQUIRE_STARTTLS", True)
    monkeypatch.setattr(smtp_receiver, "TLS_CONTEXT", None)
    with pytest.raises(ValueError, match="requires SMTP_TLS_CERT_FILE"):
        await smtp_receiver.start_smtp_server("127.0.0.1", 0)


async def test_total_data_deadline_returns_421_and_closes(client, smtp_listener, monkeypatch):
    await make_agent(client, "timeout@agents.local")
    monkeypatch.setattr(smtp_receiver, "DATA_TIMEOUT_SECONDS", 0.05)
    reader, writer = await asyncio.open_connection("127.0.0.1", smtp_listener)
    try:
        assert (await asyncio.wait_for(reader.readline(), 1)).startswith(b"220 ")
        writer.write(b"EHLO test.example\r\n")
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), 1)
            if not line.startswith(b"250-"):
                break
        assert line.startswith(b"250 ")
        writer.write(b"MAIL FROM:<sender@example.org>\r\n")
        await writer.drain()
        assert (await asyncio.wait_for(reader.readline(), 1)).startswith(b"250 ")
        writer.write(b"RCPT TO:<timeout@agents.local>\r\n")
        await writer.drain()
        assert (await asyncio.wait_for(reader.readline(), 1)).startswith(b"250 ")
        writer.write(b"DATA\r\n")
        await writer.drain()
        assert (await asyncio.wait_for(reader.readline(), 1)).startswith(b"354 ")
        writer.write(b"Subject: unfinished\r\n\r\npartial")
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), 1)
        assert response.startswith(b"421 Message input timed out")
    finally:
        writer.close()
        await writer.wait_closed()


async def test_connection_cap_refuses_extra_session(smtp_listener, monkeypatch):
    monkeypatch.setattr(smtp_receiver, "MAX_CONNECTIONS", 1)
    first_reader, first_writer = await asyncio.open_connection("127.0.0.1", smtp_listener)
    second_reader = second_writer = None
    try:
        assert (await asyncio.wait_for(first_reader.readline(), 1)).startswith(b"220 ")
        second_reader, second_writer = await asyncio.open_connection("127.0.0.1", smtp_listener)
        response = await asyncio.wait_for(second_reader.readline(), 1)
        assert response.startswith(b"421 Too many SMTP connections")
    finally:
        first_writer.close()
        await first_writer.wait_closed()
        if second_writer:
            second_writer.close()
            await second_writer.wait_closed()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AttachmentInfectedError("virus.bin"), "554 Attachment rejected by malware scanner"),
        (AttachmentScanLimitError("large.bin"), "552 Attachment 'large.bin' exceeds the configured malware scan size limit"),
        (AttachmentScannerUnavailableError(), "451 Attachment scanner temporarily unavailable; please retry"),
    ],
)
async def test_attachment_scanner_failures_map_to_retryable_smtp_status(error, expected, monkeypatch):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(smtp_receiver.SmtpHandler, "process_email", fail)
    handler = smtp_receiver.SmtpHandler()
    server = SimpleNamespace(_data_timeout_handle=None)
    result = await handler.handle_DATA(server, SimpleNamespace(), SimpleNamespace(original_content=b"test"))
    assert result == expected
