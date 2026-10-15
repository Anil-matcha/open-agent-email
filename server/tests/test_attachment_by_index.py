import base64
import datetime
from urllib.parse import urlsplit

from app.database import AsyncSessionLocal
from app.models import Message, MessageAttachment
from tests.conftest import ADMIN


BASE = "/v1/agents/attachment-index@agents.local"


async def _seed(client):
    response = await client.post(
        "/v1/agents", headers=ADMIN,
        json={"email": "attachment-index@agents.local", "display_name": "Attachment index test"},
    )
    assert response.status_code == 201
    async with AsyncSessionLocal() as db:
        db.add(Message(
            id="msg_index", agent_email="attachment-index@agents.local", direction="inbound",
            sender="from@example.com", recipient="attachment-index@agents.local", subject="files",
            body_text="body", thread_id="thread_index", rfc_message_id="<index@example.com>",
            status="delivered", created_at=datetime.datetime.utcnow(),
        ))
        await db.flush()
        db.add_all([
            MessageAttachment(id="att_index_0", message_id="msg_index", filename="first.txt",
                              content_type="text/plain", size_bytes=5, content=b"first",
                              created_at=datetime.datetime(2026, 1, 1)),
            MessageAttachment(id="att_index_1", message_id="msg_index", filename="second.bin",
                              content_type="application/octet-stream", size_bytes=6, content=b"second",
                              created_at=datetime.datetime(2026, 1, 2)),
        ])
        await db.commit()


async def test_attachment_by_index_returns_metadata_signed_url_and_inline_bytes(client):
    await _seed(client)
    response = await client.get(f"{BASE}/messages/msg_index/attachments/1", headers=ADMIN)
    assert response.status_code == 200
    item = response.json()
    assert item["index"] == 1
    assert item["filename"] == "second.bin"
    assert item["content_type"] == "application/octet-stream"
    assert item["size_bytes"] == 6
    assert "data" not in item
    assert item["download_url"].startswith("/v1/attachments/msg_index/att_index_1/download?")
    assert item["expires_at"]
    download = await client.get(item["download_url"])
    assert download.status_code == 200 and download.content == b"second"

    inline = await client.get(f"{BASE}/messages/msg_index/attachments/0?inline=true", headers=ADMIN)
    assert inline.status_code == 200
    assert inline.json()["data"] == base64.b64encode(b"first").decode("ascii")


async def test_attachment_by_index_limits_inline_size_and_preserves_id_route(client):
    await _seed(client)
    async with AsyncSessionLocal() as db:
        large = await db.get(MessageAttachment, "att_index_1")
        large.content = b"x" * (256 * 1024 + 1)
        large.size_bytes = len(large.content)
        await db.commit()

    rejected = await client.get(f"{BASE}/messages/msg_index/attachments/1?inline=true", headers=ADMIN)
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "attachment_too_large"

    by_id = await client.get(f"{BASE}/messages/msg_index/attachments/att_index_1", headers=ADMIN)
    assert by_id.status_code == 200 and by_id.content == b"x" * (256 * 1024 + 1)


async def test_attachment_by_index_is_inbox_scoped_and_rejects_missing_index(client):
    await _seed(client)
    assert (await client.get(f"{BASE}/messages/msg_index/attachments/2", headers=ADMIN)).status_code == 404
    assert (await client.get("/v1/agents/other@agents.local/messages/msg_index/attachments/0",
                            headers=ADMIN)).status_code == 404
    assert (await client.get(f"{BASE}/messages/msg_index/attachments/-1", headers=ADMIN)).status_code == 404
