import asyncio
import struct
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.attachment_scan import (
    AttachmentInfectedError,
    AttachmentScanLimitError,
    AttachmentScannerUnavailableError,
    scan_attachments,
)


async def _fake_clamd(monkeypatch, response=b"stream: OK\0", *, delay=0):
    scans = []
    largest_chunk = 0
    active = 0
    max_active = 0

    async def handle(reader, writer):
        nonlocal largest_chunk, active, max_active
        try:
            assert await reader.readexactly(len(b"zINSTREAM\0")) == b"zINSTREAM\0"
            content = bytearray()
            while True:
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                if size == 0:
                    break
                largest_chunk = max(largest_chunk, size)
                content.extend(await reader.readexactly(size))
            scans.append(bytes(content))
            if delay:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(delay)
                active -= 1
            if response is not None:
                writer.write(response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_PORT", server.sockets[0].getsockname()[1])
    return server, scans, lambda: largest_chunk, lambda: max_active


@pytest.fixture(autouse=True)
def enabled_scanner(monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_ENABLED", True)
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_CHUNK_BYTES", 7)
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_TIMEOUT_SECONDS", 1)


@pytest.mark.asyncio
async def test_scan_uses_bounded_instream_chunks_and_accepts_clean_file(monkeypatch):
    server, scans, largest_chunk, _ = await _fake_clamd(monkeypatch)
    try:
        await scan_attachments([{"filename": "hello.txt", "content": b"hello world"}])
        assert scans == [b"hello world"]
        assert largest_chunk() <= 7
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_malware_response_is_permanent_validation_error(monkeypatch):
    server, scans, _, _ = await _fake_clamd(monkeypatch, b"stream: Eicar-Test-Signature FOUND\0")
    try:
        with pytest.raises(AttachmentInfectedError) as error:
            await scan_attachments([{"filename": "bad.bin", "content": b"infected"}])
        assert error.value.status_code == 422
        assert "bad.bin" in error.value.detail
        assert scans == [b"infected"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_daemon_error_fails_closed_as_transient_service_error(monkeypatch):
    server, _, _, _ = await _fake_clamd(monkeypatch, b"stream: daemon ERROR\0")
    try:
        with pytest.raises(AttachmentScannerUnavailableError) as error:
            await scan_attachments([{"filename": "file.bin", "content": b"content"}])
        assert error.value.status_code == 503
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_scanner_timeout_is_transient_and_fails_closed(monkeypatch):
    server, _, _, _ = await _fake_clamd(monkeypatch, delay=0.2)
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_TIMEOUT_SECONDS", 0.02)
    try:
        with pytest.raises(AttachmentScannerUnavailableError) as error:
            await scan_attachments([{"filename": "slow.bin", "content": b"content"}])
        assert error.value.status_code == 503
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_size_limit_rejects_without_connecting():
    settings.ATTACHMENT_SCAN_MAX_BYTES = 3
    with pytest.raises(AttachmentScanLimitError) as error:
        await scan_attachments([{"filename": "large.bin", "content": b"four"}])
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_concurrent_requests_are_bounded_by_scanner_semaphore(monkeypatch):
    server, _, _, max_active = await _fake_clamd(monkeypatch, delay=0.03)
    try:
        await asyncio.gather(*[
            scan_attachments([{"filename": f"{index}.bin", "content": b"data"}])
            for index in range(8)
        ])
        assert max_active() <= settings.ATTACHMENT_SCAN_MAX_CONCURRENT
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_service_boundaries_scan_before_database_reads_or_writes(monkeypatch):
    from app.services import email_service

    called = []

    async def reject(_attachments):
        called.append(True)
        raise AttachmentInfectedError("infected.bin")

    monkeypatch.setattr(email_service, "scan_attachments", reject)
    db = SimpleNamespace(execute=lambda *_args, **_kwargs: pytest.fail("database accessed before scan"))
    agent = SimpleNamespace(email="agent@example.test")

    with pytest.raises(AttachmentInfectedError):
        await email_service.EmailService.process_inbound(
            db=db, agent=agent, sender="sender@example.test", subject="subject",
            body_text="body", attachments=[{"filename": "infected.bin", "content": b"bad"}],
        )
    with pytest.raises(AttachmentInfectedError):
        await email_service.EmailService.send_outbound(
            db=db, agent=agent, recipient="recipient@example.test", subject="subject",
            body_text="body", attachments=[{"filename": "infected.bin", "content": b"bad"}],
        )
    assert called == [True, True]


@pytest.mark.asyncio
async def test_disabled_scanning_does_not_contact_daemon(monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENT_SCAN_ENABLED", False)
    await scan_attachments([{"filename": "file.bin", "content": b"content"}])
