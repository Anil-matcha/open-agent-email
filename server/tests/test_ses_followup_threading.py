"""Follow-ups to our own SES-sent mail must reference the Message-ID each recipient actually received."""

import email
import smtplib

import pytest
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Agent, Message, MessageRecipient, SmtpConfig
from app.services.email_service import EmailService, ses_message_id_domain
from app.services.outbound_worker import _dispatch_one
from tests.conftest import ADMIN, make_agent
from tests.test_recipients import BASE, SENDER, _enable_relay, _make_job_due, _send, _sender

DOMAIN = "eu-central-1.amazonses.com"
QUEUED = None  # a relay that keeps our Message-ID answers "250 2.0.0 Ok: queued as ..."


def ses(n: int) -> str:
    return f"010701a1dc5c{n:04x}-b56f0c74-292f-4a92-9b37-6758da28{n:04x}-000000"


class SesRelay:
    """smtplib.SMTP stand-in: each transaction gets the next SES id; records envelope and data."""
    ids: list = []
    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def data(self, payload):
        ses_id = self.ids.pop(0)
        return 250, (f"Ok {ses_id}" if ses_id else "2.0.0 Ok: queued as 4XyZ12").encode()

    def sendmail(self, frm, to, data):
        self.sent.append((to[0], data))
        assert self.data(data)[0] == 250
        return {}

    def quit(self):
        pass


@pytest.fixture
def relay(monkeypatch):
    SesRelay.ids, SesRelay.sent = [], []
    monkeypatch.setattr(smtplib, "SMTP", SesRelay)
    return SesRelay


async def _setup(client, host="email-smtp.eu-central-1.amazonaws.com"):
    await _sender(client)
    await _enable_relay()
    async with AsyncSessionLocal() as db:
        await db.execute(update(SmtpConfig).where(SmtpConfig.id == "default").values(host=host))
        await db.commit()


async def _deliver(relay, ids):
    """Dispatch the queued message; returns {envelope recipient: raw data} of this dispatch."""
    relay.ids, start = list(ids), len(relay.sent)
    assert await _dispatch_one()
    assert relay.ids == []
    return dict(relay.sent[start:])


def _headers(raw):
    parsed = email.message_from_string(raw)
    flat = {name: " ".join(parsed[name].split()) if parsed[name] else None
            for name in ("In-Reply-To", "References", "To", "Cc", "Bcc", "Message-ID")}
    return flat


async def _reply(client, message_id, **payload):
    r = await client.post(f"{BASE}/messages/{message_id}/reply", headers=ADMIN,
                          json={"body_text": "Folgemail", **payload})
    assert r.status_code == 201, r.text
    return r.json()


async def _original(client, relay, ids=(ses(1), ses(2), ses(3))):
    r = await _send(client, to=["a@example.com"], cc=["b@example.com"], bcc=["hidden@example.com"])
    assert r.status_code == 201, r.text
    copies = await _deliver(relay, ids)
    return r.json(), copies


@pytest.mark.parametrize("host, expected", [
    ("email-smtp.eu-central-1.amazonaws.com", "eu-central-1.amazonses.com"),
    ("EMAIL-SMTP.eu-west-1.amazonaws.com ", "eu-west-1.amazonses.com"),
    ("email-smtp.us-east-1.amazonaws.com", "email.amazonses.com"),
    ("relay.test", None),
    ("email-smtp.eu-central-1.amazonaws.com.evil.example", None),
    (None, None),
])
def test_ses_domain_comes_from_the_ses_smtp_endpoint(host, expected):
    assert ses_message_id_domain(host) == expected


async def test_followup_to_own_mail_references_each_recipients_ses_id(client, relay):
    await _setup(client)
    original, _ = await _original(client, relay)
    own_id = original["rfc_message_id"]

    # Plain reply to our own sent mail goes to the original To only.
    followup = await _reply(client, original["id"])
    assert followup["to"] == ["a@example.com"] and followup["cc"] == [] and followup["bcc"] == []
    assert followup["thread_id"] == original["thread_id"]
    copies = await _deliver(relay, [ses(10)])
    assert list(copies) == ["a@example.com"]
    head = _headers(copies["a@example.com"])
    assert head["In-Reply-To"] == head["References"] == f"<{ses(1)}@{DOMAIN}>"
    raw = copies["a@example.com"]
    assert own_id not in raw and ses(2) not in raw and ses(3) not in raw and "hidden@example.com" not in raw

    # The stored message keeps our own ids; only the transmitted copy is tailored.
    detail = (await client.get(f"{BASE}/messages/{followup['id']}", headers=ADMIN)).json()
    assert detail["in_reply_to"] == own_id and detail["references"] == own_id
    assert [(d["address"], d["attempts"], d["relay_message_id"]) for d in detail["deliveries"]] == [
        ("a@example.com", 1, ses(10))]


async def test_reply_all_tailors_every_copy_and_never_leaks_another_alias(client, relay):
    await _setup(client)
    original, _ = await _original(client, relay)
    # Reply-All: original Bcc is not copied; an explicit Bcc (here the same hidden reader) is allowed.
    followup = await _reply(client, original["id"], reply_all=True, bcc=["hidden@example.com"],
                            cc=["new@example.com"])
    assert followup["to"] == ["a@example.com"]
    assert followup["cc"] == ["b@example.com", "new@example.com"] and followup["bcc"] == ["hidden@example.com"]
    copies = await _deliver(relay, [ses(11), ses(12), ses(13), ses(14)])
    expected = {"a@example.com": ses(1), "b@example.com": ses(2), "hidden@example.com": ses(3)}
    aliases = {ses(1), ses(2), ses(3)}
    for address, own_alias in expected.items():
        head = _headers(copies[address])
        assert head["In-Reply-To"] == head["References"] == f"<{own_alias}@{DOMAIN}>"
        assert head["Bcc"] is None
        for other in aliases - {own_alias}:
            assert other not in copies[address]
    # A recipient added now never saw the original; it gets our own id and nobody's alias.
    new_head = _headers(copies["new@example.com"])
    assert new_head["In-Reply-To"] == new_head["References"] == original["rfc_message_id"]
    assert not any(alias in copies["new@example.com"] for alias in aliases)
    for address in ("a@example.com", "b@example.com", "new@example.com"):
        assert "hidden@example.com" not in copies[address]


async def test_followup_to_a_followup_keeps_the_whole_chain_per_recipient(client, relay):
    await _setup(client)
    original, _ = await _original(client, relay)
    first = await _reply(client, original["id"], reply_all=True)
    await _deliver(relay, [ses(20), ses(21)])
    second = await _reply(client, first["id"], reply_all=True)
    assert second["thread_id"] == original["thread_id"]
    copies = await _deliver(relay, [ses(30), ses(31)])
    a, b = _headers(copies["a@example.com"]), _headers(copies["b@example.com"])
    assert a["References"] == f"<{ses(1)}@{DOMAIN}> <{ses(20)}@{DOMAIN}>" and a["In-Reply-To"] == f"<{ses(20)}@{DOMAIN}>"
    assert b["References"] == f"<{ses(2)}@{DOMAIN}> <{ses(21)}@{DOMAIN}>" and b["In-Reply-To"] == f"<{ses(21)}@{DOMAIN}>"
    assert ses(1) not in copies["b@example.com"] and ses(20) not in copies["b@example.com"]


async def test_missing_or_unknown_relay_ids_fall_back_to_our_own_message_id(client, relay):
    await _setup(client)
    # b's copy was accepted by a relay answer without an SES id; a's outcome later became unknown.
    original, _ = await _original(client, relay, ids=(ses(1), QUEUED, ses(3)))
    async with AsyncSessionLocal() as db:
        await db.execute(update(MessageRecipient).where(
            MessageRecipient.message_id == original["id"], MessageRecipient.address == "a@example.com",
        ).values(status="delivery_unknown", relay_message_id=None))
        await db.commit()
    await _reply(client, original["id"], reply_all=True)
    copies = await _deliver(relay, [ses(40), ses(41)])
    for address in ("a@example.com", "b@example.com"):
        head = _headers(copies[address])
        assert head["In-Reply-To"] == head["References"] == original["rfc_message_id"]
        assert ses(3) not in copies[address]


async def test_relays_that_keep_our_message_id_get_unchanged_references(client, relay):
    await _setup(client, host="relay.test")
    original, _ = await _original(client, relay, ids=(QUEUED, QUEUED, QUEUED))
    await _reply(client, original["id"], reply_all=True)
    copies = await _deliver(relay, [QUEUED, QUEUED])
    for raw in copies.values():
        head = _headers(raw)
        assert head["In-Reply-To"] == head["References"] == original["rfc_message_id"]


async def test_reply_to_an_inbound_answer_maps_the_quoted_alias_per_recipient(client, relay):
    await _setup(client)
    original, _ = await _original(client, relay)
    # The Bcc reader answered privately; its reply quotes the alias of the Bcc copy.
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == SENDER))
        inbound, _scan, _review = await EmailService.process_inbound(
            db, agent, sender="hidden@example.com", subject="Re: Hello", body_text="Antwort", dmarc_status="pass",
            raw_message_id="<r1@mail.example.com>", in_reply_to=f"<{ses(3)}@{DOMAIN}>",
            references=f"<{ses(3)}@{DOMAIN}>", to_recipients=[SENDER])
    assert inbound.thread_id == original["thread_id"]
    # Sarah answers and explicitly copies b, who must not learn of the hidden copy's alias.
    await _reply(client, inbound.id, cc=["b@example.com"])
    copies = await _deliver(relay, [ses(50), ses(51)])
    hidden, b = _headers(copies["hidden@example.com"]), _headers(copies["b@example.com"])
    assert hidden["References"] == f"<{ses(3)}@{DOMAIN}> <r1@mail.example.com>"
    assert b["References"] == f"<{ses(2)}@{DOMAIN}> <r1@mail.example.com>"
    assert hidden["In-Reply-To"] == b["In-Reply-To"] == "<r1@mail.example.com>"
    assert ses(3) not in copies["b@example.com"]


async def test_local_inbox_gets_our_own_ids_and_joins_its_thread(client, relay):
    await _setup(client)
    await make_agent(client, "local@agents.example.com")
    r = await _send(client, to=["a@example.com"], cc=["local@agents.example.com"])
    original = r.json()
    await _deliver(relay, [ses(1)])
    await _reply(client, original["id"], reply_all=True)
    copies = await _deliver(relay, [ses(60)])
    assert _headers(copies["a@example.com"])["References"] == f"<{ses(1)}@{DOMAIN}>"
    async with AsyncSessionLocal() as db:
        local = (await db.execute(select(Message).where(Message.agent_email == "local@agents.example.com")
                                  .order_by(Message.created_at))).scalars().all()
    assert len(local) == 2
    assert local[1].in_reply_to == local[1].references == original["rfc_message_id"]
    assert local[1].thread_id == local[0].thread_id


async def test_retry_sends_the_same_tailored_headers_once_per_recipient(client, relay, monkeypatch):
    await _setup(client)
    original, _ = await _original(client, relay)
    headers = {**ADMIN, "Idempotency-Key": "followup-1"}
    payload = {"body_text": "Folgemail", "reply_all": True}
    first = await client.post(f"{BASE}/messages/{original['id']}/reply", headers=headers, json=payload)
    again = await client.post(f"{BASE}/messages/{original['id']}/reply", headers=headers, json=payload)
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]

    # b's first transaction is refused by the relay; the retry submits only b, with the same headers.
    refused = {"b@example.com"}
    real_sendmail = SesRelay.sendmail

    def flaky(self, frm, to, data):
        if to[0] in refused:
            refused.discard(to[0])
            raise smtplib.SMTPRecipientsRefused({to[0]: (550, b"try later")})
        return real_sendmail(self, frm, to, data)

    monkeypatch.setattr(SesRelay, "sendmail", flaky)
    copies = await _deliver(relay, [ses(70)])
    assert list(copies) == ["a@example.com"]
    await _make_job_due(first.json()["id"])
    retry = await _deliver(relay, [ses(71)])
    assert list(retry) == ["b@example.com"]
    assert _headers(retry["b@example.com"])["References"] == f"<{ses(2)}@{DOMAIN}>"
    detail = (await client.get(f"{BASE}/messages/{first.json()['id']}", headers=ADMIN)).json()
    assert {d["address"]: d["attempts"] for d in detail["deliveries"]} == {"a@example.com": 1, "b@example.com": 2}
