"""Amazon SES rewrites Message-ID per SMTP transaction; replies must still reach the original thread."""

import json
import smtplib
import socket
import sqlite3
import threading

import pytest
from sqlalchemy import select, update

import app.migrate as migrate
from app.database import AsyncSessionLocal
from app.models import Agent, Message, MessageRecipient
from app.services.email_service import EmailService, _send_smtp_sync, ses_message_id, ses_reference_ids
from app.services.outbound_worker import _dispatch_one
from tests.conftest import ADMIN, make_agent
from tests.test_recipients import SENDER, _enable_relay, _send, _sender

# Real ids from the live test on 2026-11-14 (Gmail copies of msg_24559429b629).
SES_A = "010701a1dc401b9d-23f99874-2111-42ed-b53f-91adc30335ea-000000"
SES_B = "010701a1dc401de4-d080bfe2-3d45-4102-b81c-a4fe26bac955-000000"
SES_C = "010701a1dc401f00-00000000-1111-2222-3333-444444444444-000000"


def _fake_relay(data_reply: bytes):
    """A real TCP SMTP peer, so the real smtplib.sendmail path runs; returns (port, received envelopes)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    received = []

    def serve():
        conn, _ = listener.accept()
        f = conn.makefile("rwb")
        f.write(b"220 fake ESMTP\r\n"); f.flush()
        while line := f.readline():
            cmd = line.strip().upper()
            if cmd.startswith(b"EHLO"):
                f.write(b"250-fake\r\n250 8BITMIME\r\n")
            elif cmd.startswith((b"MAIL", b"RCPT")):
                received.append(line.strip().decode())
                f.write(b"250 Ok\r\n")
            elif cmd == b"DATA":
                f.write(b"354 End data with <CR><LF>.<CR><LF>\r\n"); f.flush()
                while f.readline() not in (b".\r\n", b""):
                    pass
                f.write(data_reply + b"\r\n")
            elif cmd == b"QUIT":
                f.write(b"221 Bye\r\n"); f.flush()
                break
            else:
                f.write(b"250 Ok\r\n")
            f.flush()
        conn.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], received


def _smtp(port, relay_reply):
    return _send_smtp_sync("127.0.0.1", port, "", "", False, False, "s@mindwerk.example", "a@example.net", "Hi",
                           "body", rcpt_to="a@example.net", relay_reply=relay_reply)


def test_real_smtplib_conversation_captures_the_ses_id_from_the_data_reply():
    port, received = _fake_relay(f"250 Ok {SES_A}".encode())
    reply = {}
    assert _smtp(port, reply) == (True, "Accepted by upstream SMTP relay")
    assert reply == {"data": f"Ok {SES_A}", "ses_message_id": SES_A}
    assert received[-1].upper() == "RCPT TO:<A@EXAMPLE.NET>"


def test_other_relays_stay_accepted_without_an_id():
    port, _ = _fake_relay(b"250 2.0.0 Ok: queued as 4XyZ12")
    reply = {}
    assert _smtp(port, reply)[0] is True
    assert reply["ses_message_id"] is None


def test_data_rejection_is_still_a_failure():
    port, _ = _fake_relay(b"554 Message rejected")
    reply = {}
    ok, info = _smtp(port, reply)
    assert not ok and "554" in info and reply == {}


@pytest.mark.parametrize("text, expected", [
    (f"Ok {SES_A}", SES_A),
    (f"Ok <{SES_A}@eu-central-1.amazonses.com>", SES_A),
    (f"Ok {SES_A.upper()}", SES_A),
    ("2.0.0 Ok: queued as ABC", None),
    ("Ok", None),
    ("Ok not-an-ses-id", None),
    (None, None),
])
def test_ses_reply_parsing(text, expected):
    assert ses_message_id(text) == expected


def test_reference_ids_only_accept_amazonses_domains():
    tokens = [f"<{SES_A}@eu-central-1.amazonses.com>", f"<{SES_B}@email.amazonses.com>",
              f"<{SES_B}@amazonses.com.evil.example>", "<msg_1@mindwerk.dev>"]
    assert ses_reference_ids(tokens) == [SES_A, SES_B]


class SesLikeSMTP:
    """smtplib.SMTP stand-in whose sendmail calls self.data like the real one; one SES id per transaction."""
    ids = []

    def __init__(self, *args, **kwargs):
        pass

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def data(self, payload):
        return 250, f"Ok {self.ids.pop(0)}".encode()

    def sendmail(self, frm, to, data):
        code, _resp = self.data(data)
        assert code == 250
        return {}

    def quit(self):
        pass


async def _send_via_ses(client, monkeypatch, ids):
    await _sender(client)
    await _enable_relay()
    SesLikeSMTP.ids = list(ids)
    monkeypatch.setattr(smtplib, "SMTP", SesLikeSMTP)
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com"], bcc=["hidden@example.com"])).json()
    assert await _dispatch_one()
    return sent


async def _inbound_reply(agent_email, ses_id, msgid):
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == agent_email))
        message, _scan, _review = await EmailService.process_inbound(
            db, agent, sender="a@example.com", subject="Re: Hello", body_text="Antwort", dmarc_status="pass",
            raw_message_id=msgid, in_reply_to=f"<{ses_id}@eu-central-1.amazonses.com>",
            references=f"<{ses_id}@eu-central-1.amazonses.com>",
            to_recipients=[agent_email], cc_recipients=["b@example.com"])
        return message


async def test_reply_to_an_ses_rewritten_copy_joins_the_original_thread(client, monkeypatch):
    third = SES_C
    sent = await _send_via_ses(client, monkeypatch, [SES_A, SES_B, third])
    detail = (await client.get(f"/v1/agents/{SENDER}/messages/{sent['id']}", headers=ADMIN)).json()
    assert {d["address"]: d["relay_message_id"] for d in detail["deliveries"]} == {
        "a@example.com": SES_A, "b@example.com": SES_B, "hidden@example.com": third}
    assert detail["status"] == "accepted_by_relay"

    reply = await _inbound_reply(SENDER, SES_B, "<r1@mail.example.com>")
    assert reply.thread_id == sent["thread_id"]
    # The Bcc recipient's copy also threads, and the inbound record exposes no relay ids or Bcc.
    bcc_reply = await _inbound_reply(SENDER, third, "<r2@mail.example.com>")
    assert bcc_reply.thread_id == sent["thread_id"]
    stored = (await client.get(f"/v1/agents/{SENDER}/messages/{reply.id}", headers=ADMIN)).json()
    assert stored["deliveries"] == [] and stored["bcc"] == []
    assert SES_A not in json.dumps(stored) and "hidden@example.com" not in json.dumps(stored)


async def test_ses_alias_never_crosses_inbox_or_tenant(client, monkeypatch):
    sent = await _send_via_ses(client, monkeypatch, [SES_A, SES_B, SES_C])
    await make_agent(client, "other@agents.example.com")
    foreign = await _inbound_reply("other@agents.example.com", SES_A, "<r3@mail.example.com>")
    assert foreign.thread_id != sent["thread_id"]

    async with AsyncSessionLocal() as db:
        await db.execute(update(Message).where(Message.id == sent["id"]).values(tenant_id="another-tenant"))
        await db.execute(update(MessageRecipient).where(MessageRecipient.message_id == sent["id"])
                         .values(tenant_id="another-tenant"))
        await db.commit()
    unrelated = await _inbound_reply(SENDER, SES_A, "<r4@mail.example.com>")
    assert unrelated.thread_id != sent["thread_id"]


async def test_unknown_ses_id_starts_a_new_thread(client, monkeypatch):
    sent = await _send_via_ses(client, monkeypatch, [SES_A, SES_B, SES_C])
    other = await _inbound_reply(SENDER, "0107ffffffffffff-00000000-0000-0000-0000-000000000000-000000",
                                 "<r5@mail.example.com>")
    assert other.thread_id != sent["thread_id"]


async def test_migration_0014_adds_the_column_to_populated_data_and_downgrades(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(migrate.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "0013")
    con = sqlite3.connect(path)
    con.execute("insert into messages (id, agent_email, direction, is_read, sender, recipient, subject, body_text, "
                "thread_id, rfc_message_id, status, labels, created_at, tenant_id) values ('m1', 'a@x', 'outbound', 1, "
                "'a@x', 'p@y', 's', 'b', 't', '<m1@x>', 'accepted_by_relay', '[]', '2026-01-01', 'default')")
    con.execute("insert into message_recipients (tenant_id, message_id, address, kind, status, attempts, updated_at) "
                "values ('default', 'm1', 'p@y', 'to', 'accepted_by_relay', 1, '2026-01-01')")
    con.commit()
    con.close()
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "head")
    con = sqlite3.connect(path)
    assert con.execute("select address, status, relay_message_id from message_recipients").fetchall() == [
        ("p@y", "accepted_by_relay", None)]
    con.close()
    await migrate.asyncio.to_thread(migrate.command.downgrade, migrate._config(), "0013")
    con = sqlite3.connect(path)
    assert "relay_message_id" not in {r[1] for r in con.execute("pragma table_info(message_recipients)")}
    assert con.execute("select count(*) from message_recipients").fetchone() == (1,)
    con.close()
