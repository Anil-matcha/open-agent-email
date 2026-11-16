"""To/Cc/Bcc, per-recipient delivery state, partial failures, privacy and Reply-All."""

import datetime
import email
import json
import smtplib
import sqlite3
from email import policy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

import app.migrate as migrate
import smtp_receiver
from app.database import AsyncSessionLocal
from app.models import Agent, Message, MessageRecipient, OutboundJob, SmtpConfig, WebhookDelivery, WebhookEndpoint
from app.routers.ses_feedback import MESSAGE_ID_HEADER, _apply_feedback
from app.services import outbound_worker
from app.services.email_service import EmailService, summarize_deliveries
from app.services.outbound_worker import _dispatch_one
from tests.conftest import ADMIN, make_agent

SENDER = "sender@agents.example.com"
BASE = f"/v1/agents/{SENDER}"


async def _sender(client, hitl=False):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": hitl})
    assert r.status_code == 201, r.text


async def _send(client, headers=None, **payload):
    payload.setdefault("subject", "Hello")
    payload.setdefault("body_text", "Body")
    return await client.post(f"{BASE}/messages", headers={**ADMIN, **(headers or {})}, json=payload)


async def _enable_relay():
    async with AsyncSessionLocal() as db:
        db.add(SmtpConfig(id="default", host="relay.test", port="587", username="", password="",
                          use_tls=False, enabled=True, from_email=SENDER))
        await db.commit()


class Relay:
    """Fake smtplib.SMTP. `plan` maps an envelope address to 'refuse', 'disconnect' or a callable."""

    def __init__(self, monkeypatch, plan=None):
        self.transactions = []
        self.plan = plan or {}
        relay = self

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass

            def starttls(self):
                pass

            def login(self, *args):
                pass

            def sendmail(self, frm, to, data):
                assert len(to) == 1, "each recipient must get its own SMTP transaction"
                action = relay.plan.get(to[0])
                if action == "refuse":
                    raise smtplib.SMTPRecipientsRefused({to[0]: (451, b"try later")})
                if action == "reject-data":
                    raise smtplib.SMTPDataError(554, b"Message rejected")
                if action == "disconnect":
                    raise smtplib.SMTPServerDisconnected("connection lost during DATA")
                relay.transactions.append((frm, to[0], data))
                return {}

            def quit(self):
                pass

        monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    @property
    def envelope(self):
        return [rcpt for _frm, rcpt, _data in self.transactions]


async def _rows(message_id):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MessageRecipient).where(MessageRecipient.message_id == message_id)
                                 .order_by(MessageRecipient.id))).scalars().all()
        return {row.address: (row.kind, row.status, row.attempts) for row in rows}


async def _state(message_id):
    async with AsyncSessionLocal() as db:
        message = await db.get(Message, message_id)
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == message_id))
        return message, job


async def _make_job_due(message_id):
    async with AsyncSessionLocal() as db:
        await db.execute(update(OutboundJob).where(OutboundJob.message_id == message_id)
                         .values(available_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=1)))
        await db.commit()


# --- request validation ---------------------------------------------------------------------------

async def test_lists_are_canonicalized_and_deduplicated_case_insensitively(client):
    await _sender(client)
    r = await _send(client, to=["A@Example.com", "Bob <b@example.com>"], cc=["a@example.com", "C@example.com"],
                    bcc=["c@EXAMPLE.com", "d@example.com", "B@example.com"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["recipient"] == "a@example.com"
    assert (body["to"], body["cc"], body["bcc"]) == (["a@example.com", "b@example.com"], ["c@example.com"], ["d@example.com"])
    assert [(d["address"], d["kind"], d["status"]) for d in body["deliveries"]] == [
        ("a@example.com", "to", "pending"), ("b@example.com", "to", "pending"),
        ("c@example.com", "cc", "pending"), ("d@example.com", "bcc", "pending")]


@pytest.mark.parametrize("payload", [
    {"to": "a@example.com\r\nBcc: evil@example.com"},
    {"to": ["a@example.com"], "cc": ["b@example.com\nX-Injected: 1"]},
    {"to": "a@example.com, b@example.com"},
    {"to": ["a@example.com"], "bcc": ["not-an-address"]},
    {"to": []},
    {"cc": ["a@example.com"]},
    {"to": "a@example.com", "recipient": "b@example.com"},
    {"to": [f"r{i}@example.com" for i in range(30)], "cc": [f"c{i}@example.com" for i in range(21)]},
])
async def test_invalid_or_injected_recipients_are_rejected(client, payload):
    await _sender(client)
    r = await _send(client, **payload)
    assert r.status_code == 422, r.text
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Message.id)) is None


async def test_legacy_single_recipient_request_is_unchanged(client):
    await _sender(client)
    body = (await _send(client, recipient="Person@Example.com")).json()
    assert body["recipient"] == "person@example.com" and body["to"] == ["person@example.com"]
    assert body["cc"] == [] and body["bcc"] == [] and len(body["deliveries"]) == 1


async def test_any_suppressed_recipient_refuses_the_whole_message(client):
    await _sender(client)
    r = await client.post(f"{BASE}/suppressions", headers=ADMIN, json={"email": "blocked@example.com"})
    assert r.status_code in (200, 201), r.text
    r = await _send(client, to=["ok@example.com"], bcc=["Blocked@example.com"])
    assert r.status_code == 422 and "blocked@example.com" in r.text


async def test_idempotency_covers_cc_and_bcc(client):
    await _sender(client)
    key = {"Idempotency-Key": "multi-1"}
    first = await _send(client, key, to=["a@example.com"], cc=["b@example.com"], bcc=["c@example.com"])
    again = await _send(client, key, to=["a@example.com"], cc=["b@example.com"], bcc=["c@example.com"])
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    changed = await _send(client, key, to=["a@example.com"], cc=["b@example.com"], bcc=["other@example.com"])
    assert changed.status_code == 422
    # A single-recipient request keeps its old digest, whichever spelling is used.
    single = await _send(client, {"Idempotency-Key": "single-1"}, recipient="p@example.com")
    as_list = await _send(client, {"Idempotency-Key": "single-1"}, to=["p@example.com"])
    assert single.json()["id"] == as_list.json()["id"]
    async with AsyncSessionLocal() as db:
        assert len((await db.execute(select(Message.id))).all()) == 2


# --- outbound SMTP --------------------------------------------------------------------------------

async def test_bcc_is_envelope_only_and_every_recipient_gets_one_transaction(client, monkeypatch):
    await _sender(client)
    await _enable_relay()
    relay = Relay(monkeypatch)
    sent = (await _send(client, to=["a@example.com", "b@example.com"], cc=["c@example.com"],
                        bcc=["secret@example.com"])).json()
    assert await _dispatch_one()

    assert sorted(relay.envelope) == ["a@example.com", "b@example.com", "c@example.com", "secret@example.com"]
    for _frm, _rcpt, data in relay.transactions:
        mime = email.message_from_string(data, policy=policy.default)
        assert mime["To"] == "a@example.com, b@example.com"
        assert mime["Cc"] == "c@example.com"
        assert mime["Bcc"] is None and "secret@example.com" not in data
    message, job = await _state(sent["id"])
    assert message.status == "accepted_by_relay" and job.status == "complete" and job.attempts == 1
    assert {address: status for address, (_k, status, _a) in (await _rows(sent["id"])).items()} == {
        "a@example.com": "accepted_by_relay", "b@example.com": "accepted_by_relay",
        "c@example.com": "accepted_by_relay", "secret@example.com": "accepted_by_relay"}


async def test_partial_failure_retries_only_the_failed_recipient(client, monkeypatch):
    await _sender(client)
    await _enable_relay()
    relay = Relay(monkeypatch, {"c@example.com": "refuse"})
    sent = (await _send(client, to=["a@example.com"], cc=["c@example.com"], bcc=["d@example.com"])).json()
    assert await _dispatch_one()

    message, job = await _state(sent["id"])
    assert message.status == "sending" and job.status == "pending" and job.attempts == 1
    assert "Accepted for 2 of 3 recipients" in message.delivery_error
    assert (await _rows(sent["id"]))["c@example.com"] == ("cc", "failed", 1)

    relay.plan.clear()
    await _make_job_due(sent["id"])
    assert await _dispatch_one()
    assert relay.envelope == ["a@example.com", "d@example.com", "c@example.com"]  # nobody twice
    message, job = await _state(sent["id"])
    assert message.status == "accepted_by_relay" and job.status == "complete"
    assert (await _rows(sent["id"]))["a@example.com"] == ("to", "accepted_by_relay", 1)


async def test_exhausted_partial_failure_is_reported_as_partially_failed(client, monkeypatch):
    await _sender(client)
    await _enable_relay()
    async with AsyncSessionLocal() as db:
        db.add(WebhookEndpoint(tenant_id="default", id="wh1", agent_email=SENDER, url="https://hooks.example/x",
                               event_types=["*"], signing_secret="s"))
        await db.commit()
    monkeypatch.setattr(outbound_worker, "MAX_ATTEMPTS", 1)
    relay = Relay(monkeypatch, {"c@example.com": "refuse"})
    sent = (await _send(client, to=["a@example.com"], cc=["c@example.com"])).json()
    assert await _dispatch_one()
    message, job = await _state(sent["id"])
    assert message.status == "partially_failed" and job.status == "failed"
    async with AsyncSessionLocal() as db:
        events = {d.event_type for d in (await db.execute(select(WebhookDelivery))).scalars()}
    assert {"email.sent", "email.partially_failed"} <= events and "email.failed" not in events

    # An operator retry submits only the recipient that has not been accepted.
    relay.plan.clear()
    r = await client.post(f"/v1/outbox/{job.id}/retry", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert await _dispatch_one()
    assert relay.envelope == ["a@example.com", "c@example.com"]
    message, _job = await _state(sent["id"])
    assert message.status == "accepted_by_relay"


async def test_ambiguous_submission_is_unknown_and_never_retried_automatically(client, monkeypatch):
    await _sender(client)
    await _enable_relay()
    relay = Relay(monkeypatch, {"b@example.com": "disconnect", "c@example.com": "refuse"})
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com", "c@example.com"])).json()
    assert await _dispatch_one()
    # The refused recipient is retried, the unknown one is not.
    relay.plan["c@example.com"] = None
    await _make_job_due(sent["id"])
    assert await _dispatch_one()
    assert relay.envelope == ["a@example.com", "c@example.com"]
    rows = await _rows(sent["id"])
    assert rows["b@example.com"] == ("cc", "delivery_unknown", 1)
    message, job = await _state(sent["id"])
    assert message.status == "delivery_unknown" and job.status == "uncertain"
    assert await _dispatch_one() is False

    # Retrying an uncertain job needs explicit confirmation and then resubmits only that recipient.
    assert (await client.post(f"/v1/outbox/{job.id}/retry", headers=ADMIN)).status_code == 409
    r = await client.post(f"/v1/outbox/{job.id}/retry?confirm_duplicate_risk=true", headers=ADMIN)
    assert r.status_code == 200, r.text
    relay.plan.clear()
    assert await _dispatch_one()
    assert relay.envelope == ["a@example.com", "c@example.com", "b@example.com"]


async def test_recipients_left_by_the_time_budget_stay_pending_for_the_next_attempt(client, monkeypatch):
    from app.services import email_service
    await _sender(client)
    await _enable_relay()
    relay = Relay(monkeypatch)
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com"])).json()
    monkeypatch.setattr(email_service, "DISPATCH_BUDGET_SECONDS", -1)
    assert await _dispatch_one()
    assert relay.envelope == []
    message, job = await _state(sent["id"])
    assert message.status == "sending" and job.status == "pending"
    assert {status for _k, status, _a in (await _rows(sent["id"])).values()} == {"pending"}
    monkeypatch.setattr(email_service, "DISPATCH_BUDGET_SECONDS", 120)
    await _make_job_due(sent["id"])
    assert await _dispatch_one()
    assert relay.envelope == ["a@example.com", "b@example.com"]


async def test_expired_lease_marks_only_the_in_flight_recipient_unknown(client):
    await _sender(client)
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com"], bcc=["c@example.com"])).json()
    stale = datetime.datetime.utcnow() - datetime.timedelta(seconds=outbound_worker.LEASE_SECONDS + 5)
    async with AsyncSessionLocal() as db:
        await db.execute(update(OutboundJob).where(OutboundJob.message_id == sent["id"])
                         .values(status="processing", locked_at=stale, attempts=1))
        await db.execute(update(MessageRecipient).where(MessageRecipient.address == "a@example.com")
                         .values(status="accepted_by_relay"))
        await db.execute(update(MessageRecipient).where(MessageRecipient.address == "b@example.com")
                         .values(status="sending"))
        await db.commit()
    assert await _dispatch_one()
    rows = await _rows(sent["id"])
    assert [rows[a][1] for a in ("a@example.com", "b@example.com", "c@example.com")] == [
        "accepted_by_relay", "delivery_unknown", "pending"]
    message, job = await _state(sent["id"])
    assert message.status == "delivery_unknown" and job.status == "uncertain"


async def test_hitl_review_shows_all_recipients_and_approval_sends_to_each(client, monkeypatch):
    await _sender(client, hitl=True)
    await _enable_relay()
    relay = Relay(monkeypatch)
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com"], bcc=["c@example.com"])).json()
    assert sent["status"] == "pending_review"
    assert await _dispatch_one() is False
    reviews = (await client.get("/v1/reviews?status=pending", headers=ADMIN)).json()
    review = next(r for r in reviews if r["message_id"] == sent["id"])
    assert (review["message"]["to"], review["message"]["cc"], review["message"]["bcc"]) == (
        ["a@example.com"], ["b@example.com"], ["c@example.com"])
    r = await client.post(f"/v1/reviews/{review['id']}/approve", headers=ADMIN, json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert await _dispatch_one()
    assert sorted(relay.envelope) == ["a@example.com", "b@example.com", "c@example.com"]


async def test_ses_feedback_is_recorded_per_recipient(client):
    await _sender(client)
    sent = (await _send(client, to=["a@example.com"], cc=["b@example.com"])).json()
    event = {"eventType": "Bounce", "mail": {"headers": [{"name": MESSAGE_ID_HEADER, "value": sent["id"]}]},
             "bounce": {"bounceType": "Permanent",
                        "bouncedRecipients": [{"emailAddress": "B@example.com"}, {"emailAddress": "x@example.com"}]}}
    async with AsyncSessionLocal() as db:
        await _apply_feedback(db, event)
        await db.commit()
        rows = {r.address: r for r in (await db.execute(select(MessageRecipient))).scalars()}
        message = await db.get(Message, sent["id"])
    assert rows["b@example.com"].provider_delivery_status == "bounced"
    assert rows["a@example.com"].provider_delivery_status is None
    assert message.provider_delivery_status == "bounced"


def test_summary_statuses():
    def rows(*states):
        return [SimpleNamespace(address=f"r{i}@x.test", status=s, detail=None) for i, s in enumerate(states)]
    assert summarize_deliveries(rows("accepted_by_relay", "delivered"))[0] == "accepted_by_relay"
    assert summarize_deliveries(rows("delivered", "delivered"))[0] == "delivered"
    assert summarize_deliveries(rows("accepted_by_relay", "failed"))[0] == "partially_failed"
    assert summarize_deliveries(rows("accepted_by_relay", "blocked"))[0] == "partially_failed"
    assert summarize_deliveries(rows("accepted_by_relay", "delivery_unknown", "failed"))[0] == "delivery_unknown"
    assert summarize_deliveries(rows("blocked", "blocked"))[0] == "blocked"
    assert summarize_deliveries(rows("failed", "blocked"))[0] == "failed"


# --- privacy of local copies and inbound mail -------------------------------------------------------

async def test_local_copies_never_contain_bcc_or_delivery_results(client):
    await _sender(client)
    for inbox in ("to@agents.example.com", "cc@agents.example.com", "hidden@agents.example.com"):
        await make_agent(client, inbox)
    async with AsyncSessionLocal() as db:
        db.add(WebhookEndpoint(tenant_id="default", id="wh2", agent_email="to@agents.example.com",
                               url="https://hooks.example/y", event_types=["*"], signing_secret="s"))
        await db.commit()
    sent = (await _send(client, to=["to@agents.example.com"], cc=["cc@agents.example.com"],
                        bcc=["hidden@agents.example.com"])).json()
    assert await _dispatch_one()
    message, _job = await _state(sent["id"])
    assert message.status == "delivered"

    for inbox in ("to@agents.example.com", "cc@agents.example.com", "hidden@agents.example.com"):
        copies = (await client.get(f"/v1/agents/{inbox}/messages", headers=ADMIN)).json()
        assert len(copies) == 1
        copy = copies[0]
        assert copy["to"] == ["to@agents.example.com"] and copy["cc"] == ["cc@agents.example.com"]
        assert copy["bcc"] == [] and copy["deliveries"] == []
        if inbox != "hidden@agents.example.com":
            assert "hidden@agents.example.com" not in json.dumps(copy)
    async with AsyncSessionLocal() as db:
        payloads = [d.payload for d in (await db.execute(select(WebhookDelivery))).scalars()]
    assert payloads and "hidden@agents.example.com" not in json.dumps(payloads)
    # A retry of the same message does not create second copies.
    assert (await EmailService.dispatch_to_upstream(*(await _dispatch_args(sent["id"]))))[0] == "delivered"


async def _dispatch_args(message_id):
    db = AsyncSessionLocal()
    message = await db.get(Message, message_id)
    agent = await db.scalar(select(Agent).where(Agent.email == message.agent_email))
    return db, message, agent


async def test_inbound_smtp_keeps_visible_headers_and_ignores_bcc(client, monkeypatch):
    await make_agent(client, "inbox@agents.local")
    monkeypatch.setattr(smtp_receiver, "evaluate_inbound_authentication",
                        AsyncMock(return_value=("pass", "pass", "pass", {})))
    raw = (b"From: Alice <alice@example.org>\r\n"
           b"To: Inbox <INBOX@agents.local>, bob@example.org\r\n"
           b"Cc: carol@example.org, bad-entry, Bob@Example.org\r\n"
           b"Bcc: spy@example.org\r\n"
           b"Reply-To: Team <team@example.org>\r\n"
           b"Subject: hi\r\nMessage-ID: <in-1@example.org>\r\n\r\nbody\r\n")
    envelope = SimpleNamespace(mail_from="alice@example.org", rcpt_tos=["inbox@agents.local"])
    session = SimpleNamespace(peer=("192.0.2.1", 25), host_name="mx.example.org")
    await smtp_receiver.SmtpHandler().process_email(raw, envelope, session)

    stored = (await client.get("/v1/agents/inbox@agents.local/messages", headers=ADMIN)).json()[0]
    assert stored["to"] == ["inbox@agents.local", "bob@example.org"]
    assert stored["cc"] == ["carol@example.org", "bob@example.org"]
    assert stored["reply_to"] == ["team@example.org"]
    assert stored["bcc"] == [] and "spy@example.org" not in json.dumps(stored)


# --- replies ----------------------------------------------------------------------------------------

async def _inbound(to=None, cc=None, reply_to=None, sender="alice@example.org"):
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == SENDER))
        message, _scan, _review = await EmailService.process_inbound(
            db, agent, sender=sender, subject="Question", body_text="hello", dmarc_status="pass",
            raw_message_id=f"<{datetime.datetime.utcnow().timestamp()}@example.org>",
            to_recipients=to, cc_recipients=cc, reply_to=reply_to)
        return message.id


async def _reply(client, message_id, **payload):
    r = await client.post(f"{BASE}/messages/{message_id}/reply", headers=ADMIN, json={"body_text": "ok", **payload})
    assert r.status_code == 201, r.text
    return r.json()


async def test_reply_all_uses_visible_headers_and_excludes_own_address(client):
    await _sender(client)
    mid = await _inbound(to=[SENDER, "bob@example.org"], cc=["carol@example.org", "Alice@example.org"])
    reply = await _reply(client, mid, reply_all=True)
    assert reply["to"] == ["alice@example.org"]
    assert reply["cc"] == ["bob@example.org", "carol@example.org"] and reply["bcc"] == []
    single = await _reply(client, mid)
    assert single["to"] == ["alice@example.org"] and single["cc"] == [] and single["in_reply_to"]


async def test_reply_honors_reply_to(client):
    await _sender(client)
    mid = await _inbound(to=[SENDER, "bob@example.org"], reply_to=["team@example.org", SENDER.upper()])
    assert (await _reply(client, mid))["to"] == ["team@example.org"]
    reply_all = await _reply(client, mid, reply_all=True, bcc=["audit@example.org"])
    assert reply_all["to"] == ["team@example.org"] and reply_all["cc"] == ["bob@example.org"]
    assert reply_all["bcc"] == ["audit@example.org"]


async def test_reply_all_on_legacy_inbound_message_does_not_infer_recipients(client):
    await _sender(client)
    mid = await _inbound()  # stored without header capture
    reply = await _reply(client, mid, reply_all=True)
    assert reply["to"] == ["alice@example.org"] and reply["cc"] == []


async def test_reply_all_to_own_sent_message_never_copies_bcc(client):
    await _sender(client)
    sent = (await _send(client, to=["a@example.com", SENDER], cc=["c@example.com"], bcc=["d@example.com"])).json()
    reply = await _reply(client, sent["id"], reply_all=True)
    assert reply["to"] == ["a@example.com"] and reply["cc"] == ["c@example.com"] and reply["bcc"] == []
    plain = await _reply(client, sent["id"])
    assert plain["to"] == ["a@example.com", SENDER] and plain["cc"] == []


async def test_mcp_send_and_reply_all(client):
    await _sender(client)

    async def call(name, **arguments):
        r = await client.post("/mcp/rpc", headers=ADMIN, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        result = r.json()["result"]
        assert not result["isError"], result
        return json.loads(result["content"][0]["text"])

    out = await call("send_message", agent_email=SENDER, to=["a@example.com", "b@example.com"],
                     cc=["c@example.com"], bcc=["d@example.com"], subject="s", body_text="b")
    msg = await call("get_message", agent_email=SENDER, message_id=out["message_id"])
    assert (msg["to"], msg["cc"], msg["bcc"]) == (["a@example.com", "b@example.com"], ["c@example.com"], ["d@example.com"])
    assert [d["address"] for d in msg["deliveries"]] == ["a@example.com", "b@example.com", "c@example.com", "d@example.com"]
    reply = await call("reply_to_message", agent_email=SENDER, message_id=out["message_id"], body_text="r", reply_all=True)
    replied = await call("get_message", agent_email=SENDER, message_id=reply["message_id"])
    assert replied["to"] == ["a@example.com", "b@example.com"] and replied["cc"] == ["c@example.com"]
    assert replied["bcc"] == []
    single = await call("reply_to_message", agent_email=SENDER, message_id=out["message_id"], body_text="r",
                        cc="e@example.com", bcc="f@example.com")
    single = await call("get_message", agent_email=SENDER, message_id=single["message_id"])
    assert (single["cc"], single["bcc"]) == (["e@example.com"], ["f@example.com"])
    listed = await call("list_messages", agent_email=SENDER, direction="outbound", limit=10)
    assert any(m["to"] == ["a@example.com", "b@example.com"] and m["cc"] == ["c@example.com"] for m in listed)
    assert all("bcc" not in m for m in listed)
    legacy = await call("send_message", agent_email=SENDER, recipient="z@example.com", subject="s", body_text="b")
    assert (await call("get_message", agent_email=SENDER, message_id=legacy["message_id"]))["to"] == ["z@example.com"]


# --- migration ----------------------------------------------------------------------------------------

async def test_migration_backfills_legacy_messages(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(migrate.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "0012")
    con = sqlite3.connect(path)
    con.execute("insert into agents (id, email, display_name, created_at, updated_at) "
                "values ('ag', 'old@agents.local', 'Old', '2026-01-01', '2026-01-01')")
    for mid, direction, recipient, status in (("m_out", "outbound", "Peer@Example.com", "accepted_by_relay"),
                                              ("m_wait", "outbound", "q@example.com", "sending"),
                                              ("m_in", "inbound", "old@agents.local", "delivered")):
        con.execute("insert into messages (id, agent_email, direction, is_read, sender, recipient, subject, body_text, "
                    "thread_id, rfc_message_id, status, labels, created_at) values (?, 'old@agents.local', ?, 0, "
                    "'s@example.com', ?, 's', 'b', 't', ?, ?, '[]', '2026-01-01')",
                    (mid, direction, recipient, f"<{mid}@x>", status))
    con.commit()
    con.close()
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "head")
    con = sqlite3.connect(path)
    assert dict(con.execute("select id, to_recipients from messages")) == {
        "m_out": '["peer@example.com"]', "m_wait": '["q@example.com"]', "m_in": None}
    assert sorted(con.execute("select message_id, address, kind, status from message_recipients")) == [
        ("m_out", "peer@example.com", "to", "accepted_by_relay"), ("m_wait", "q@example.com", "to", "pending")]
    con.close()
    await migrate.asyncio.to_thread(migrate.command.downgrade, migrate._config(), "0012")


async def test_definitive_data_rejection_is_failed_not_unknown(client, monkeypatch):
    """A 5xx after DATA means the relay did not take the message: retryable, never delivery_unknown."""
    await _sender(client)
    await _enable_relay()
    relay = Relay(monkeypatch, {"b@example.com": "reject-data"})
    sent = await _send(client, to=["a@example.com", "b@example.com"])
    message_id = sent.json()["id"]
    await _dispatch_one()
    rows = await _rows(message_id)
    assert rows["a@example.com"][1] == "accepted_by_relay"
    assert rows["b@example.com"][1] == "failed"
    message, job = await _state(message_id)
    assert job.status == "pending" and message.status == "sending"
    await _make_job_due(message_id)
    relay.plan.clear()
    await _dispatch_one()
    assert relay.envelope == ["a@example.com", "b@example.com"]  # a never resubmitted
    message, job = await _state(message_id)
    assert message.status == "accepted_by_relay" and job.status == "complete"
