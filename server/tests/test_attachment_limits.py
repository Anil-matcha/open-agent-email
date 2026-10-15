import base64
import datetime
from urllib.parse import urlsplit

import pytest

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Message, MessageAttachment
from app.services.attachment_utils import decode_attachment_inputs
from tests.conftest import ADMIN


BASE = "/v1/agents/attach@agents.local"


async def _agent(client):
    response = await client.post("/v1/agents", headers=ADMIN,
                                 json={"email": "attach@agents.local", "display_name": "Attachment test"})
    assert response.status_code == 201


def test_decoded_total_attachment_limit(monkeypatch):
    monkeypatch.setattr(settings, "OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES", 4)
    with pytest.raises(Exception) as exc:
        decode_attachment_inputs([{"filename": "x", "content_base64": base64.b64encode(b"12345").decode()}])
    assert getattr(exc.value, "status_code", None) == 413


async def test_composed_message_ceiling_is_checked_before_queueing(client, monkeypatch):
    await _agent(client)
    monkeypatch.setattr(settings, "OUTBOUND_MAX_COMPOSED_BYTES", 6)
    response = await client.post(f"{BASE}/messages", headers=ADMIN,
                                 json={"recipient": "to@example.com", "subject": "abc", "body_text": "defg"})
    assert response.status_code == 413
    assert response.json()["error"]["details"] == {
        "scope": "composed_message", "actual_bytes": 7, "max_bytes": 6,
    }


async def test_attachment_metadata_includes_short_lived_signed_download(client):
    await _agent(client)
    async with AsyncSessionLocal() as db:
        message = Message(
            id="msg_attach", agent_email="attach@agents.local", direction="inbound", sender="from@example.com",
            recipient="attach@agents.local", subject="file", body_text="body", thread_id="thread_attach",
            rfc_message_id="<attach@example.com>", status="delivered", created_at=datetime.datetime.utcnow(),
        )
        db.add(message)
        await db.flush()
        db.add(MessageAttachment(id="att_attach", message_id=message.id, filename="note.txt",
                                 content_type="text/plain", size_bytes=4, content=b"file"))
        await db.commit()

    listed = await client.get(f"{BASE}/messages/msg_attach/attachments", headers=ADMIN)
    assert listed.status_code == 200
    item = listed.json()[0]
    assert item["size_bytes"] == 4 and item["download_url"].startswith("/v1/attachments/")
    download = await client.get(item["download_url"])
    assert download.status_code == 200 and download.content == b"file"
    assert download.headers["cache-control"] == "private, no-store"
    parsed = urlsplit(item["download_url"])
    expired = await client.get(parsed.path, params={"expires": 1, "signature": "0" * 64})
    assert expired.status_code == 404
