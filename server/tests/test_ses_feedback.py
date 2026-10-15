import datetime

import pytest

from app.database import AsyncSessionLocal
from app.models import Agent, Message
from app.routers.ses_feedback import MESSAGE_ID_HEADER, _apply_feedback


async def _seed(recipient="to@example.com"):
    async with AsyncSessionLocal() as db:
        db.add(Agent(id="ag1", email="a@agents.local", display_name="A"))
        db.add(Message(id="msg1", agent_email="a@agents.local", direction="outbound", sender="a@agents.local",
                       recipient=recipient, body_text="hi", thread_id="t1", rfc_message_id="<1@x>",
                       status="accepted_by_relay", created_at=datetime.datetime.utcnow()))
        await db.commit()


def _event(kind, recipient="to@example.com", message_id="msg1", **extra):
    mail = {"headers": [{"name": MESSAGE_ID_HEADER, "value": message_id}], "destination": [recipient]}
    body = {
        "Delivery": {"delivery": {"recipients": [recipient]}},
        "DeliveryDelay": {"deliveryDelay": {"delayedRecipients": [{"emailAddress": recipient}]}},
        "Bounce": {"bounce": {"bounceType": "Permanent", "bouncedRecipients": [{"emailAddress": recipient}]}},
        "Complaint": {"complaint": {"complainedRecipients": [{"emailAddress": recipient}]}},
        "Reject": {"reject": {"reason": "Bad content"}},
    }[kind]
    return {"eventType": kind, "mail": mail, **body, **extra}


async def _status():
    async with AsyncSessionLocal() as db:
        return (await db.get(Message, "msg1")).provider_delivery_status


async def _apply(event):
    async with AsyncSessionLocal() as db:
        await _apply_feedback(db, event)
        await db.commit()


@pytest.mark.parametrize("kind,expected", [
    ("Delivery", "delivered"), ("DeliveryDelay", "delayed"), ("Bounce", "bounced"),
    ("Complaint", "complained"), ("Reject", "rejected"),
])
async def test_each_event_type_recorded(kind, expected):
    await _seed()
    await _apply(_event(kind))
    assert await _status() == expected


@pytest.mark.parametrize("first,late", [("Bounce", "Delivery"), ("Complaint", "Delivery"),
                                        ("Complaint", "Bounce"), ("Bounce", "DeliveryDelay"),
                                        ("Reject", "Delivery")])
async def test_out_of_order_never_downgrades(first, late):
    await _seed()
    await _apply(_event(first))
    before = await _status()
    await _apply(_event(late))
    assert await _status() == before


async def test_duplicate_notification_is_idempotent_on_status():
    await _seed()
    await _apply(_event("Delivery"))
    await _apply(_event("Delivery"))
    assert await _status() == "delivered"


async def test_recipient_mismatch_ignored():
    await _seed()
    await _apply(_event("Bounce", recipient="someone-else@example.com"))
    assert await _status() is None


async def test_unknown_or_oversized_correlation_ignored():
    await _seed()
    await _apply(_event("Bounce", message_id="nope"))
    await _apply(_event("Bounce", message_id="x" * 65))
    assert await _status() is None


async def test_recipient_case_insensitive_match():
    await _seed()
    await _apply(_event("Delivery", recipient="TO@Example.com"))
    assert await _status() == "delivered"
