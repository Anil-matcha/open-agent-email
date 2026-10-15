"""Optional bounded ClamAV attachment scanning over the clamd INSTREAM protocol."""

import asyncio
import struct
from typing import Any, Iterable

from fastapi import HTTPException

from app.config import settings


class AttachmentInfectedError(HTTPException):
    """A permanent rejection: ClamAV reported malware in an attachment."""

    def __init__(self, filename: str):
        super().__init__(
            status_code=422,
            detail=f"Attachment {filename!r} was rejected because malware was detected",
        )


class AttachmentScannerUnavailableError(HTTPException):
    """A transient rejection: the required scanner could not complete the scan."""

    def __init__(self, detail: str = "Attachment scanner is temporarily unavailable"):
        super().__init__(status_code=503, detail=detail)


class AttachmentScanLimitError(HTTPException):
    """A permanent rejection: the file exceeds the configured scan limit."""

    def __init__(self, filename: str):
        super().__init__(
            status_code=413,
            detail=f"Attachment {filename!r} exceeds the configured malware scan size limit",
        )


_SCAN_SEMAPHORE = asyncio.Semaphore(settings.ATTACHMENT_SCAN_MAX_CONCURRENT)


async def _scan_bytes(content: bytes) -> bytes:
    """Send one byte string to clamd; return its bounded terminated reply."""
    reader = writer = None
    try:
        reader, writer = await asyncio.open_connection(
            settings.ATTACHMENT_SCAN_HOST,
            settings.ATTACHMENT_SCAN_PORT,
            limit=4096,
        )
        writer.write(b"zINSTREAM\0")
        for offset in range(0, len(content), settings.ATTACHMENT_SCAN_CHUNK_BYTES):
            chunk = content[offset : offset + settings.ATTACHMENT_SCAN_CHUNK_BYTES]
            writer.write(struct.pack("!I", len(chunk)))
            writer.write(chunk)
            await writer.drain()
        writer.write(b"\0\0\0\0")
        await writer.drain()
        response = bytearray()
        while len(response) <= 4096:
            char = await reader.readexactly(1)
            if char in {b"\0", b"\n"}:
                return bytes(response)
            response.extend(char)
        raise AttachmentScannerUnavailableError("Attachment scanner returned an oversized response")
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as exc:
        raise AttachmentScannerUnavailableError() from exc
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass


async def scan_attachments(attachments: Iterable[dict[str, Any]] | None) -> None:
    """Scan attachments before persistence or dispatch; disabled unless explicitly enabled."""
    if not settings.ATTACHMENT_SCAN_ENABLED:
        return
    try:
        async with asyncio.timeout(settings.ATTACHMENT_SCAN_TIMEOUT_SECONDS):
            for attachment in attachments or ():
                content = attachment.get("content", b"")
                filename = str(attachment.get("filename") or "attachment")
                if not isinstance(content, bytes):
                    raise AttachmentScannerUnavailableError("Attachment scanner received invalid attachment data")
                if len(content) > settings.ATTACHMENT_SCAN_MAX_BYTES:
                    raise AttachmentScanLimitError(filename)
                async with _SCAN_SEMAPHORE:
                    response = await _scan_bytes(content)

                if not response or len(response) > 4096:
                    raise AttachmentScannerUnavailableError("Attachment scanner returned an invalid response")
                normalized = response.rstrip(b"\r\n")
                if normalized.endswith(b" OK"):
                    continue
                if normalized.endswith(b" FOUND"):
                    raise AttachmentInfectedError(filename)
                # clamd ERROR, an unknown response, and malformed replies all fail closed.
                raise AttachmentScannerUnavailableError("Attachment scanner could not complete the scan")
    except TimeoutError as exc:
        raise AttachmentScannerUnavailableError() from exc
