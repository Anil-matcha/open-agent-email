"""Tenant isolation contract.

Two tenants (A and B) and the default tenant (the admin key) each own a full set of data. Tenant A then
tries to read or change tenant B's data through every REST route, every MCP tool, webhooks, events and
WebSockets. Credentials for A and B come from a registered principal resolver, which is exactly how a
hosted layer plugs in.

Two properties carry most of the weight:

* a request that names only another tenant's resources returns no secret of that tenant, and
* it changes nothing anywhere in the database.

The route and tool sweeps enumerate the OpenAPI document and the MCP tool list, so a route or tool added
later is exercised automatically; one that needs a path value this file cannot supply fails the suite
until it is mapped.
"""
import asyncio
import datetime
import json
import re
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.config import settings
from app.database import AsyncSessionLocal, Base, engine
from app.main import app, live_dashboard_websocket
from app.models import (
    Agent, ApiKey, Domain, IdempotencyRecord, Message, MessageAttachment, OutboundJob, ReviewItem,
    WebhookDelivery, WebhookEndpoint,
)
from app.routers.agents import agent_websocket_endpoint
from app.security import Principal
from app.services.email_service import EmailService
from app.services.idempotency import reserve_idempotency
from app.services.ws_manager import ws_manager
from app.tenancy import (
    DEFAULT_TENANT_ID, bind_tenant, register_principal_resolver, register_token_extractor,
    unregister_principal_resolver, unregister_token_extractor, validate_tenant_id,
)
from tests.conftest import ADMIN

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
HEADERS = {
    TENANT_A: {"Authorization": "Bearer admin-a-token"},
    TENANT_B: {"Authorization": "Bearer admin-b-token"},
}
TOKEN_TENANT = {"admin-a-token": TENANT_A, "admin-b-token": TENANT_B}
A_HEADERS, B_HEADERS = HEADERS[TENANT_A], HEADERS[TENANT_B]

# Tables whose contents are expected to change as a side effect of an otherwise rejected request.
VOLATILE_TABLES = {"idempotency_records", "alembic_version"}


async def _resolver(token, db):
    tenant = TOKEN_TENANT.get(token)
    if tenant is None:
        return None
    return Principal(tenant_id=tenant, is_admin=True, principal_id=f"account:{tenant}")


@pytest.fixture(autouse=True)
def hosted_resolver():
    register_principal_resolver(_resolver)
    yield
    unregister_principal_resolver(_resolver)


# --- seeding ---------------------------------------------------------------------------------------

@dataclass
class Tenant:
    id: str
    headers: dict
    mark: str
    email: str = ""
    ids: dict = field(default_factory=dict)
    secrets: list = field(default_factory=list)


async def _post(client, tenant, path, body, status=201):
    r = await client.post(path, headers=tenant.headers, json=body)
    assert r.status_code == status, f"{path}: {r.status_code} {r.text}"
    return r.json()


async def _seed(client, tenant_id) -> Tenant:
    mark = f"SECRET{tenant_id[-1].upper()}"
    t = Tenant(id=tenant_id, headers=HEADERS[tenant_id], mark=mark)
    t.email = f"{tenant_id}-inbox@agents.example.com"
    await _post(client, t, "/v1/agents", {
        "email": t.email, "display_name": f"{mark} name", "description": f"{mark} description",
        "hitl_enabled": False})
    inbox = await _post(client, t, "/v1/webhooks",
                        {"agent_email": t.email, "url": f"https://example.com/{mark}-inbox", "events": ["*"]})
    account = await _post(client, t, "/v1/webhooks", {"url": f"https://example.com/{mark}-account", "events": ["*"]})
    t.ids.update(endpoint_id=inbox["id"], account_endpoint_id=account["id"])
    t.secrets += [inbox["signing_secret"], account["signing_secret"]]

    contact = await _post(client, t, f"/v1/agents/{t.email}/contacts",
                          {"email": f"contact-{tenant_id}@example.org", "name": f"{mark} contact", "notes": f"{mark} notes"})
    t.ids.update(contact_id=contact["id"], address=contact["email"])
    template = await _post(client, t, f"/v1/agents/{t.email}/templates",
                           {"name": f"{mark} template", "subject": f"{mark} subject", "body_text": f"{mark} template body"})
    t.ids["template_id"] = template["id"]
    suppression = await _post(client, t, f"/v1/agents/{t.email}/suppressions",
                              {"email": f"blocked-{tenant_id}@example.org", "reason": f"{mark} reason"})
    t.ids["suppression_id"] = suppression["id"]
    engagement = await _post(client, t, f"/v1/agents/{t.email}/engagements",
                             {"contact_id": contact["id"], "stage": "new", "notes": f"{mark} engagement"})
    t.ids["engagement_id"] = engagement["id"]
    domain = await _post(client, t, "/v1/domains", {"domain": f"{tenant_id}-corp.example"})
    t.ids["domain_name"] = domain["domain"]
    key = await _post(client, t, "/v1/api-keys", {"name": f"{mark} key", "agent_email": t.email, "scope": "full_access"})
    t.ids["key_id"] = key["id"]
    t.secrets.append(key["key"])
    t.ids["agent_key"] = key["key"]

    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == t.email))
        inbound, _, _ = await EmailService.process_inbound(
            db, agent, sender="sender@example.org", subject=f"{mark} inbound subject",
            body_text=f"{mark} inbound body, your verification code is 424242",
            attachments=[{"filename": f"{mark}.txt", "content_type": "text/plain", "content": f"{mark}-bytes".encode()}])
        _, _, review = await EmailService.process_inbound(
            db, agent, sender="held@example.org", subject=f"{mark} held subject", body_text=f"{mark} held body",
            force_review=True)
        t.ids.update(message_id=inbound.id, thread_id=inbound.thread_id, review_id=review.id)
    sent = await _post(client, t, f"/v1/agents/{t.email}/messages", {
        "recipient": "someone@example.org", "subject": f"{mark} outbound subject", "body_text": f"{mark} outbound body",
        "conversation_id": f"conv-{tenant_id}"})
    t.ids.update(outbound_id=sent["id"], conversation_id=f"conv-{tenant_id}")
    async with AsyncSessionLocal() as db:
        t.ids["job_id"] = await db.scalar(select(OutboundJob.id).where(OutboundJob.message_id == sent["id"]))
        t.ids["delivery_id"] = await db.scalar(
            select(WebhookDelivery.id).where(WebhookDelivery.tenant_id == tenant_id).order_by(WebhookDelivery.id))
        t.ids["attachment_id"] = await db.scalar(
            select(MessageAttachment.id).where(MessageAttachment.message_id == t.ids["message_id"]))
    t.ids["event_id"] = t.ids["delivery_id"]
    assert all(t.ids.get(k) for k in ("job_id", "delivery_id", "attachment_id", "review_id")), t.ids
    return t


@pytest.fixture
async def world(client):
    a = await _seed(client, TENANT_A)
    b = await _seed(client, TENANT_B)
    return a, b


async def _snapshot(tenant_id=None, skip=VOLATILE_TABLES):
    """Every row of every table (or one tenant's rows), as comparable text."""
    out = {}
    async with engine.connect() as conn:
        for name, table in sorted(Base.metadata.tables.items()):
            if name in skip:
                continue
            query = select(table)
            if tenant_id is not None:
                if "tenant_id" not in table.c:
                    continue
                query = query.where(table.c.tenant_id == tenant_id)
            rows = (await conn.execute(query)).all()
            out[name] = sorted(repr(tuple(r)) for r in rows)
    return out


# --- principals, resolver and hooks ----------------------------------------------------------------

def test_tenant_id_format():
    for good in ("default", "tenant-a", "a", "0abc_def-9", "x" * 64):
        assert validate_tenant_id(good) == good
    for bad in ("", "Tenant", "-a", "_a", "a b", "a/b", "x" * 65, None, 7, "ünï"):
        with pytest.raises(ValueError):
            validate_tenant_id(bad)
    with pytest.raises(ValueError):
        Principal(tenant_id="Bad Id", is_admin=True)


async def test_admin_key_maps_to_the_default_tenant(client):
    me = await client.get("/v1/whoami", headers=ADMIN)
    assert me.status_code == 200
    await client.post("/v1/agents", headers=ADMIN, json={"email": "op@agents.example.com", "display_name": "op"})
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Agent.tenant_id).where(Agent.email == "op@agents.example.com")) == DEFAULT_TENANT_ID


async def test_resolved_principal_is_bound_to_its_tenant(client):
    r = await client.post("/v1/agents", headers=A_HEADERS, json={"email": "a1@agents.example.com", "display_name": "a1"})
    assert r.status_code == 201
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Agent.tenant_id).where(Agent.email == "a1@agents.example.com")) == TENANT_A


async def test_unknown_credentials_are_rejected(client):
    assert (await client.get("/v1/agents", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get("/v1/agents")).status_code == 401


async def test_resolver_can_reject_a_recognised_but_invalid_credential(client):
    async def strict(token, db):
        if token.startswith("dead_"):
            raise HTTPException(status_code=401, detail="expired")
        return None

    register_principal_resolver(strict)
    try:
        res = await client.get("/v1/agents", headers={"Authorization": "Bearer dead_token"})
        assert res.status_code == 401 and res.json()["detail"] == "expired"
    finally:
        unregister_principal_resolver(strict)


async def test_token_extractor_supplies_a_credential(client):
    def from_cookie(request):
        return request.headers.get("x-session")

    assert (await client.get("/v1/agents", headers={"x-session": "admin-a-token"})).status_code == 401
    register_token_extractor(from_cookie)
    try:
        assert (await client.get("/v1/agents", headers={"x-session": "admin-a-token"})).status_code == 200
    finally:
        unregister_token_extractor(from_cookie)
    assert (await client.get("/v1/agents", headers={"x-session": "admin-a-token"})).status_code == 401


async def test_stored_keys_carry_their_tenant(client, world):
    a, b = world
    key = {"Authorization": f"Bearer {a.ids['agent_key']}"}
    # A's inbox key works on A's inbox and means nothing on B's.
    assert (await client.get(f"/v1/agents/{a.email}/messages", headers=key)).status_code == 200
    assert (await client.get(f"/v1/agents/{b.email}/messages", headers=key)).status_code == 403
    assert (await client.get(f"/v1/agents/{b.email}/ws-nothing", headers=key)).status_code == 404


async def test_tenant_admin_is_not_the_server_operator(client, world):
    a, _ = world
    for method, path in (("get", "/v1/settings"), ("put", "/v1/settings"), ("get", "/v1/settings/readiness"),
                         ("get", "/v1/settings/smtp"), ("post", "/v1/settings/smtp"), ("get", "/metrics")):
        r = await getattr(client, method)(path, headers=A_HEADERS, **({"json": {"values": {}}} if method != "get" else {}))
        assert r.status_code == 403, (method, path, r.status_code)
    assert (await client.get("/v1/settings", headers=ADMIN)).status_code == 200
    assert (await client.get("/v1/settings/smtp", headers=ADMIN)).status_code == 200


# --- listing and direct reads ----------------------------------------------------------------------

async def test_each_tenant_lists_only_its_own_data(client, world):
    a, b = world
    for tenant, other in ((a, b), (b, a)):
        agents = (await client.get("/v1/agents", headers=tenant.headers)).json()
        assert [x["email"] for x in agents] == [tenant.email]
        messages = (await client.get(f"/v1/agents/{tenant.email}/messages", headers=tenant.headers)).json()
        assert messages and all(other.mark not in json.dumps(m) for m in messages)
        for path in ("/v1/webhooks", "/v1/api-keys", "/v1/domains", "/v1/reviews", "/v1/outbox", "/v1/contacts",
                     "/v1/account/suppressions", "/v1/events", "/v1/webhooks/deliveries", "/v1/account/export"):
            body = (await client.get(path, headers=tenant.headers)).text
            assert other.mark not in body and other.email not in body and other.ids["domain_name"] not in body, path
            assert all(s not in body for s in other.secrets), path


async def test_default_tenant_data_is_separate_from_hosted_tenants(client, world):
    a, b = world
    await client.post("/v1/agents", headers=ADMIN, json={"email": "op@agents.example.com", "display_name": "SECRETOP"})
    assert [x["email"] for x in (await client.get("/v1/agents", headers=ADMIN)).json()] == ["op@agents.example.com"]
    assert "op@agents.example.com" not in (await client.get("/v1/agents", headers=A_HEADERS)).text
    assert (await client.get("/v1/agents/op@agents.example.com", headers=A_HEADERS)).status_code == 404
    assert (await client.get(f"/v1/agents/{a.email}", headers=ADMIN)).status_code == 404


async def test_same_names_coexist_per_tenant_and_addresses_are_global(client, world):
    a, b = world
    # Per-inbox names are unique only inside an inbox, and inboxes are tenant-owned.
    r = await client.post("/v1/agents", headers=A_HEADERS, json={"email": b.email, "display_name": "squat"})
    assert r.status_code == 400
    same = await client.post("/v1/domains", headers=A_HEADERS, json={"domain": b.ids["domain_name"]})
    assert same.status_code == 400
    # A domain registered by B is B's namespace: A cannot open inboxes on it.
    r = await client.post("/v1/agents", headers=A_HEADERS,
                          json={"email": f"x@{b.ids['domain_name']}", "display_name": "squat"})
    assert r.status_code == 400
    # The failed attempts left B's data untouched and created nothing for A.
    assert [x["email"] for x in (await client.get("/v1/agents", headers=A_HEADERS)).json()] == [a.email]


# --- the sweeps ------------------------------------------------------------------------------------

PUBLIC_OR_STATIC = {
    # No principal: capability links, provider callbacks, discovery and static content.
    "/unsubscribe/{token}", "/review-link/{token}", "/webhooks/ses", "/v1/info", "/health", "/api/health", "/ready",
    "/", "/v1/starter-templates", "/v1/starter-templates/page", "/v1/starter-templates/{alias}",
    "/v1/attachments/{message_id}/{attachment_id}/download", "/v1/whoami", "/mcp/info", "/mcp", "/mcp/tools",
    "/mcp/rpc",  # exercised tool by tool in test_every_mcp_tool_is_isolated
}
# Pure functions of their request: they touch no stored data, so succeeding is not a breach.
STATELESS = {"/v1/templates/validate"}
OPERATOR_ONLY = {"/v1/settings", "/v1/settings/readiness", "/v1/settings/smtp", "/v1/settings/smtp/test", "/metrics"}

BODIES = {
    ("POST", "/v1/webhooks"): lambda t: {"agent_email": t.email, "url": "https://example.com/x"},
    ("PATCH", "/v1/webhooks/{endpoint_id}"): lambda t: {"enabled": False, "url": "https://example.com/stolen"},
    ("POST", "/v1/agents"): lambda t: {"email": t.email, "display_name": "squat"},
    ("PATCH", "/v1/agents/{email}"): lambda t: {"display_name": "stolen", "status": "paused"},
    ("POST", "/v1/agents/{email}/contacts"): lambda t: {"email": "planted@example.org", "name": "planted"},
    ("PATCH", "/v1/agents/{email}/contacts/{contact_id}"): lambda t: {"name": "stolen"},
    ("POST", "/v1/agents/{email}/contacts/{contact_id}/consent"): lambda t: {
        "action": "granted", "source": "stolen", "evidence": "x"},
    ("POST", "/v1/contacts/import"): lambda t: {"agent_email": t.email, "contacts": [{"email": "p@example.org", "name": "p"}]},
    ("POST", "/v1/agents/{email}/engagements"): lambda t: {"contact_id": t.ids["contact_id"], "stage": "new"},
    ("PATCH", "/v1/agents/{email}/engagements/{engagement_id}"): lambda t: {"stage": "closed"},
    ("POST", "/v1/agents/{email}/suppressions"): lambda t: {"email": "planted@example.org"},
    ("POST", "/v1/agents/{email}/templates"): lambda t: {"name": "planted", "subject": "s", "body_text": "b"},
    ("POST", "/v1/agents/{email}/templates/preview"): lambda t: {"subject": "s", "body_text": "b"},
    ("POST", "/v1/agents/{email}/templates/{template_id}/preview"): lambda t: {"data": {}},
    ("PATCH", "/v1/agents/{email}/templates/{template_id}"): lambda t: {"name": "stolen"},
    ("POST", "/v1/agents/{email}/messages"): lambda t: {"recipient": "x@example.org", "subject": "s", "body_text": "b"},
    ("PATCH", "/v1/agents/{email}/messages/{message_id}"): lambda t: {"labels": ["stolen"]},
    ("POST", "/v1/agents/{email}/messages/{message_id}/reply"): lambda t: {"body_text": "stolen"},
    ("POST", "/v1/agents/{email}/messages/{message_id}/forward"): lambda t: {"recipient": "x@example.org"},
    ("PUT", "/v1/agents/{email}/protection"): lambda t: {"threat_sensitivity": "off", "hitl_enabled": False},
    ("POST", "/v1/reviews/{review_id}/approve"): lambda t: {"action": "approve", "notes": "stolen"},
    ("POST", "/v1/reviews/{review_id}/reject"): lambda t: {"action": "reject", "notes": "stolen"},
    ("POST", "/v1/domains"): lambda t: {"domain": t.ids["domain_name"]},
    ("POST", "/v1/api-keys"): lambda t: {"name": "planted", "agent_email": t.email, "scope": "full_access"},
    ("POST", "/v1/account/api-keys"): lambda t: {"name": "planted", "agent_email": t.email, "scope": "agent"},
    ("POST", "/v1/contacts"): lambda t: {"agent_email": t.email, "email": "planted@example.org", "name": "planted"},
    ("PATCH", "/v1/contacts/{address}"): lambda t: {"name": "stolen"},
    ("POST", "/v1/templates/validate"): lambda t: {"subject": "s", "body_text": "b"},
}


def _path_values(t: Tenant) -> dict:
    return {
        "email": t.email, "message_id": t.ids["message_id"], "thread_id": t.ids["thread_id"],
        "conversation_id": t.ids["conversation_id"], "attachment_id": t.ids["attachment_id"], "index": "0",
        "contact_id": t.ids["contact_id"], "batch_id": "batch_x", "engagement_id": t.ids["engagement_id"],
        "suppression_id": t.ids["suppression_id"], "template_id": t.ids["template_id"],
        "endpoint_id": t.ids["endpoint_id"], "delivery_id": t.ids["delivery_id"], "event_id": t.ids["event_id"],
        "review_id": t.ids["review_id"], "job_id": t.ids["job_id"], "key_id": t.ids["key_id"], "id": t.ids["key_id"],
        "domain_name": t.ids["domain_name"], "address": t.ids["address"], "alias": "welcome", "token": "x" * 43,
    }


def _operations():
    spec = app.openapi()
    for path, ops in sorted(spec["paths"].items()):
        for method, op in sorted(ops.items()):
            yield method.upper(), path, op


def _fill(path, values):
    def sub(m):
        name = m.group(1)
        assert name in values, f"map a value for path parameter {{{name}}} of {path}"
        return values[name]
    return re.sub(r"{(\w+)}", sub, path)


async def _sweep(client, attacker_headers, victim: Tenant, attacker_tenant: Tenant):
    """Send every route's request, aimed at the victim's resources, from the attacker. Returns results."""
    results = []
    values = _path_values(victim)
    for method, path, op in _operations():
        if path in PUBLIC_OR_STATIC:
            continue
        url = _fill(path, values)
        if "{index}" in path:
            url = url
        kwargs = {}
        if op.get("parameters") and any(p["name"] == "confirm" for p in op["parameters"]):
            url += "?confirm=DELETE"
        if "requestBody" in op:
            body = BODIES.get((method, path))
            kwargs["json"] = body(victim) if body else {}
        response = await client.request(method, url, headers=attacker_headers, **kwargs)
        results.append((method, path, response))
    return results


def _victim_values(victim: Tenant) -> list[str]:
    """Strings the attacker could only learn by reading the victim's data."""
    return [victim.mark, *victim.secrets]


async def test_every_route_refuses_cross_tenant_access(client, world):
    a, b = world
    before = await _snapshot()
    results = await _sweep(client, A_HEADERS, victim=b, attacker_tenant=a)
    after = await _snapshot()

    assert len(results) > 100, "the OpenAPI sweep found too few routes"
    for method, path, response in results:
        assert response.status_code < 500, (method, path, response.status_code, response.text[:200])
        if path in OPERATOR_ONLY:
            assert response.status_code == 403, (method, path)
        text_ = response.text
        for value in _victim_values(b):
            assert value not in text_, (method, path, "leaked", value)
        if response.status_code < 300:
            for victim_id in (b.ids["message_id"], b.ids["review_id"], b.ids["delivery_id"], b.ids["job_id"],
                              b.ids["attachment_id"], b.ids["key_id"], b.ids["endpoint_id"]):
                assert victim_id not in text_, (method, path, "returned a victim id", victim_id)
        if (method, path) in BODIES:
            # A body that fails validation never reaches the code under test, so the sweep would prove nothing.
            assert response.status_code != 422, (method, path, "crafted body was rejected as invalid", response.text[:200])
        if method != "GET" and path not in STATELESS:
            assert response.status_code >= 400, (method, path, response.status_code, response.text[:200])
    assert after == before, [k for k in after if after[k] != before[k]]


async def test_agent_scoped_key_cannot_reach_another_tenant_either(client, world):
    a, b = world
    key = {"Authorization": f"Bearer {a.ids['agent_key']}"}
    before = await _snapshot()
    results = await _sweep(client, key, victim=b, attacker_tenant=a)
    after = await _snapshot()
    for method, path, response in results:
        assert response.status_code < 500, (method, path, response.status_code)
        assert response.status_code != 200 or all(v not in response.text for v in _victim_values(b)), (method, path)
        if method != "GET" and path not in STATELESS:
            assert response.status_code >= 400, (method, path)
    assert after == before


async def test_sweep_is_not_vacuous(client, world):
    """The owner gets the same data the attacker was refused, so the refusals above mean something."""
    _, b = world
    results = await _sweep(client, B_HEADERS, victim=b, attacker_tenant=b)
    assert sum(1 for m, _, r in results if m == "GET" and r.status_code == 200) > 30
    assert sum(1 for m, _, r in results if m == "GET" and r.status_code == 200 and b.mark in r.text) > 15


async def test_cross_tenant_writes_to_each_resource_family_are_refused(client, world):
    a, b = world
    before = await _snapshot(TENANT_B)
    attempts = [
        ("delete", f"/v1/agents/{b.email}?purge=true", None),
        ("delete", f"/v1/agents/{b.email}/contacts/{b.ids['contact_id']}", None),
        ("delete", f"/v1/agents/{b.email}/templates/{b.ids['template_id']}", None),
        ("delete", f"/v1/agents/{b.email}/suppressions/{b.ids['suppression_id']}", None),
        ("delete", f"/v1/webhooks/{b.ids['endpoint_id']}", None),
        ("delete", f"/v1/webhooks/{b.ids['account_endpoint_id']}", None),
        ("delete", f"/v1/api-keys/{b.ids['key_id']}", None),
        ("delete", f"/v1/domains/{b.ids['domain_name']}", None),
        ("delete", f"/v1/agents/{b.email}/messages/{b.ids['message_id']}?permanent=true&confirm=DELETE", None),
        ("post", f"/v1/reviews/{b.ids['review_id']}/approve", {}),
        ("post", f"/v1/outbox/{b.ids['job_id']}/retry", {}),
        ("post", f"/v1/webhooks/deliveries/{b.ids['delivery_id']}/replay", {}),
        ("post", f"/v1/webhooks/{b.ids['endpoint_id']}/rotate-secret", {}),
        ("post", f"/v1/domains/{b.ids['domain_name']}/verify", {}),
        ("post", f"/v1/agents/{b.email}/test", {}),
    ]
    for method, path, body in attempts:
        r = await client.request(method.upper(), path, headers=A_HEADERS, **({"json": body} if body is not None else {}))
        assert r.status_code in (400, 403, 404, 409, 422), (method, path, r.status_code, r.text[:200])
    assert await _snapshot(TENANT_B) == before


async def test_deleting_a_tenant_inbox_with_purge_leaves_the_others(client, world):
    a, b = world
    assert (await client.delete(f"/v1/agents/{a.email}?purge=true", headers=A_HEADERS)).status_code == 204
    assert (await client.get(f"/v1/agents/{b.email}/messages", headers=B_HEADERS)).status_code == 200
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Message.id).where(Message.id == b.ids["message_id"]))
        assert not await db.scalar(select(Message.id).where(Message.id == a.ids["message_id"]))


# --- MCP -------------------------------------------------------------------------------------------

def _mcp_value(name, schema, victim: Tenant):
    mapped = {
        "agent_email": victim.email, "email": victim.email, "message_id": victim.ids["message_id"],
        "review_id": victim.ids["review_id"], "webhook_id": victim.ids["endpoint_id"],
        "event_id": victim.ids["event_id"], "job_id": victim.ids["job_id"], "key_id": victim.ids["key_id"],
        "domain": victim.ids["domain_name"], "address": victim.ids["address"], "contact_id": victim.ids["contact_id"],
        "template_id": victim.ids["template_id"], "suppression_id": victim.ids["suppression_id"],
        "engagement_id": victim.ids["engagement_id"], "attachment_id": victim.ids["attachment_id"],
        "conversation_id": victim.ids["conversation_id"], "batch_id": "batch_x", "alias": "welcome",
        "confirm": "DELETE", "confirm_duplicate_risk": True, "permanent": True, "purge": True,
        "scope": "agent", "recipient": "x@example.org", "url": "https://example.com/x", "name": "planted",
        "action": "granted", "source": "stolen", "status": "paused", "threat_sensitivity": "off",
        "hitl_enabled": False, "labels": ["stolen"], "csv": "email,name\nplanted@example.org,planted\n",
        "contacts": [{"email": "planted@example.org", "name": "planted"}], "stage": "closed",
        "values": {"PUBLIC_BASE_URL": "https://stolen.example"},
    }
    if name in mapped:
        return mapped[name]
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    return {"string": "x", "integer": 1, "number": 0, "boolean": False, "array": [], "object": {}}.get(kind, "x")


async def test_every_mcp_tool_is_isolated(client, world):
    a, b = world
    from app.routers.mcp import MCP_TOOLS, OPERATOR_TOOLS
    assert len(MCP_TOOLS) > 90
    before = await _snapshot()
    refused_operator = 0
    for tool in MCP_TOOLS:
        schema = tool.get("inputSchema", {})
        args = {n: _mcp_value(n, s, b) for n, s in schema.get("properties", {}).items()
                if n in schema.get("required", []) or n in {"agent_email", "message_id", "review_id"}}
        r = await client.post("/mcp/rpc", headers=A_HEADERS, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool["name"], "arguments": args}})
        assert r.status_code == 200, (tool["name"], r.status_code)
        for value in _victim_values(b):
            assert value not in r.text, (tool["name"], "leaked", value)
        if tool["name"] in OPERATOR_TOOLS:
            assert "operator" in r.text.lower(), tool["name"]
            refused_operator += 1
    assert refused_operator == len(OPERATOR_TOOLS) == 3
    assert await _snapshot() == before


async def test_mcp_lists_and_reads_are_scoped_to_the_callers_tenant(client, world):
    a, b = world

    async def call(headers, name, **args):
        r = await client.post("/mcp/rpc", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return r.json()["result"]["content"][0]["text"]

    for headers, own, other in ((A_HEADERS, a, b), (B_HEADERS, b, a)):
        for name, args in (("list_agents", {}), ("list_reviews", {}), ("list_webhooks", {}), ("list_events", {}),
                           ("list_domains", {}), ("list_api_keys", {}), ("export_account", {}),
                           ("list_messages", {"agent_email": own.email})):
            text_ = await call(headers, name, **args)
            assert other.mark not in text_ and other.email not in text_, name
        assert own.email in await call(headers, "list_agents")
        assert other.ids["message_id"] not in await call(headers, "list_messages", agent_email=own.email)
        assert "not found" in (await call(headers, "get_message", agent_email=other.email,
                                          message_id=other.ids["message_id"])).lower()


# --- events, webhooks and the delivery worker ------------------------------------------------------

async def test_account_webhook_never_receives_another_tenants_events(client, world):
    a, b = world
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(WebhookDelivery.endpoint_id, WebhookDelivery.tenant_id,
                                        WebhookDelivery.agent_email))).all()
        endpoints = {e.id: e.tenant_id for e in (await db.execute(select(WebhookEndpoint))).scalars()}
    assert rows
    for endpoint_id, tenant_id, agent_email in rows:
        assert endpoints[endpoint_id] == tenant_id
        assert agent_email.startswith(tenant_id)
    # Each tenant's account-level endpoint did receive its own inbox events.
    for t in (a, b):
        assert any(r[0] == t.ids["account_endpoint_id"] for r in rows)


async def test_new_events_follow_the_inbox_tenant(client, world):
    a, b = world
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == b.email))
        await EmailService.process_inbound(db, agent, sender="s@example.org", subject="later", body_text="later")
    async with AsyncSessionLocal() as db:
        a_endpoints = {a.ids["endpoint_id"], a.ids["account_endpoint_id"]}
        leaked = (await db.execute(select(WebhookDelivery).where(
            WebhookDelivery.endpoint_id.in_(a_endpoints), WebhookDelivery.agent_email == b.email))).all()
        assert leaked == []
        b_new = (await db.execute(select(WebhookDelivery).where(
            WebhookDelivery.endpoint_id == b.ids["account_endpoint_id"], WebhookDelivery.agent_email == b.email))).all()
        assert len(b_new) >= 2


async def test_inbound_routing_uses_the_address_and_stamps_the_inbox_tenant(client, world):
    a, b = world
    async with AsyncSessionLocal() as db:
        for t in (a, b):
            m = await db.get(Message, t.ids["message_id"])
            assert m.tenant_id == t.id and m.agent_email == t.email
            review = await db.get(ReviewItem, t.ids["review_id"])
            assert review.tenant_id == t.id


async def test_local_delivery_between_tenants_lands_in_the_recipients_tenant(client, world):
    a, b = world
    from app.services.outbound_worker import _dispatch_one
    r = await client.post(f"/v1/agents/{a.email}/messages", headers=A_HEADERS, json={
        "recipient": b.email, "subject": "cross tenant hello", "body_text": "hello"})
    assert r.status_code == 201, r.text
    for _ in range(5):
        if not await _dispatch_one():
            break
    async with AsyncSessionLocal() as db:
        landed = (await db.execute(select(Message).where(
            Message.agent_email == b.email, Message.subject == "cross tenant hello"))).scalars().all()
        assert len(landed) == 1 and landed[0].tenant_id == TENANT_B and landed[0].direction == "inbound"
    assert "cross tenant hello" in (await client.get(f"/v1/agents/{b.email}/messages", headers=B_HEADERS)).text
    assert "cross tenant hello" not in (await client.get(f"/v1/agents/{b.email}/messages", headers=A_HEADERS)).text


async def test_delivery_worker_refuses_an_endpoint_of_another_tenant(client, world):
    a, b = world
    from app.services import webhook_delivery
    async with AsyncSessionLocal() as db:
        delivery = await db.get(WebhookDelivery, b.ids["delivery_id"])
        delivery.endpoint_id = a.ids["endpoint_id"]  # corrupt link: a B event pointing at A's endpoint
        delivery.status, delivery.lease_token = "processing", "lease"
        await db.commit()
    posted = AsyncMock()
    original = webhook_delivery._post_pinned
    webhook_delivery._post_pinned = posted
    try:
        await webhook_delivery._deliver(b.ids["delivery_id"], "lease")
    finally:
        webhook_delivery._post_pinned = original
    posted.assert_not_called()
    async with AsyncSessionLocal() as db:
        assert (await db.get(WebhookDelivery, b.ids["delivery_id"])).last_error == "endpoint_unavailable"


async def test_provider_feedback_suppresses_inside_the_messages_tenant(client, world):
    a, b = world
    from app.routers.ses_feedback import _apply_feedback
    async with AsyncSessionLocal() as db:
        from app.models import RecipientSuppression
        message_id = b.ids["outbound_id"]
        event = {"eventType": "Bounce", "bounce": {"bounceType": "Permanent",
                                                    "bouncedRecipients": [{"emailAddress": "someone@example.org"}]},
                 "mail": {"headers": [{"name": "X-OAE-Message-ID", "value": message_id}]}}
        from app.routers import ses_feedback
        event["mail"]["headers"][0]["name"] = ses_feedback.MESSAGE_ID_HEADER
        await _apply_feedback(db, event)
        await db.commit()
        rows = (await db.execute(select(RecipientSuppression).where(
            RecipientSuppression.email == "someone@example.org"))).scalars().all()
        assert [(r.agent_email, r.tenant_id) for r in rows] == [(b.email, TENANT_B)]


async def test_capability_links_act_inside_their_own_tenant(client, world):
    a, b = world
    from app.models import UnsubscribeToken
    from app.services.unsubscribe import issue_token
    async with AsyncSessionLocal() as db:
        raw, digest = issue_token()
        db.add(UnsubscribeToken(tenant_id=TENANT_B, token_hash=digest, agent_email=b.email,
                                recipient="victim@example.org"))
        await db.commit()
    r = await client.post(f"/unsubscribe/{raw}", content="confirm=unsubscribe",
                          headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    from app.models import RecipientSuppression
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(RecipientSuppression).where(RecipientSuppression.email == "victim@example.org"))
        assert row.tenant_id == TENANT_B and row.agent_email == b.email


# --- idempotency -----------------------------------------------------------------------------------

async def test_idempotency_keys_do_not_collide_across_tenants():
    kwargs = dict(request={"x": 1})
    one = await reserve_idempotency("principal:route", "same-key", tenant_id=TENANT_A, **kwargs)
    other = await reserve_idempotency("principal:route", "same-key", tenant_id=TENANT_B, **kwargs)
    again = await reserve_idempotency("principal:route", "same-key", tenant_id=TENANT_A, **kwargs)
    assert one.outcome == other.outcome == "reserved"
    assert one.scope != other.scope
    assert again.outcome == "in_progress"
    default_a = await reserve_idempotency("principal:route", "legacy", tenant_id=DEFAULT_TENANT_ID, **kwargs)
    assert default_a.scope == "principal:route"  # receipts from before tenancy keep their scope


# --- WebSockets ------------------------------------------------------------------------------------

class FakeSocket:
    """Stands in for a Starlette WebSocket: records closes and sends, then disconnects on first read."""

    def __init__(self, token, query=None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self.query_params = query or {}
        self.sent, self.closed, self.accepted = [], None, False

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=None):
        self.closed = code

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def receive_text(self):
        from starlette.websockets import WebSocketDisconnect
        raise WebSocketDisconnect()


async def test_inbox_websocket_refuses_another_tenants_inbox(client, world):
    a, b = world
    socket = FakeSocket("admin-a-token")
    await agent_websocket_endpoint(socket, b.email)
    assert socket.closed == 1008 and not socket.accepted
    assert b.email not in ws_manager.agent_connections

    own = FakeSocket("admin-a-token")
    await agent_websocket_endpoint(own, a.email)
    assert own.accepted and own.closed is None

    stored_key = FakeSocket(a.ids["agent_key"])
    await agent_websocket_endpoint(stored_key, b.email)
    assert stored_key.closed == 1008 and not stored_key.accepted


async def test_live_feed_carries_only_the_callers_tenant(client, world):
    a, b = world
    ws_manager.broadcast_connections.clear()
    sockets = {}
    for tenant_id, token in ((TENANT_A, "admin-a-token"), (TENANT_B, "admin-b-token"), (DEFAULT_TENANT_ID, "test-admin-key")):
        socket = FakeSocket(token)
        await live_dashboard_websocket(socket)
        assert socket.accepted
        sockets[tenant_id] = socket
        await ws_manager.connect_broadcast(MagicMock(), "unused") if False else None
    # The fakes disconnect on first read, so register live ones to observe delivery.
    live = {}
    for tenant_id in (TENANT_A, TENANT_B, DEFAULT_TENANT_ID):
        ws = FakeSocket(None)
        await ws_manager.connect_broadcast(ws, tenant_id)
        live[tenant_id] = ws
    await ws_manager.broadcast_event(TENANT_B, "email.received", {"subject": "SECRETB live"})
    assert [e["data"]["subject"] for e in live[TENANT_B].sent] == ["SECRETB live"]
    assert live[TENANT_A].sent == [] and live[DEFAULT_TENANT_ID].sent == []
    assert ws_manager.total_active_connections(TENANT_B) == 1
    ws_manager.broadcast_connections.clear()


async def test_real_events_reach_only_the_owning_tenants_live_feed(client, world):
    a, b = world
    ws_manager.broadcast_connections.clear()
    feeds = {t: FakeSocket(None) for t in (TENANT_A, TENANT_B)}
    for tenant_id, ws in feeds.items():
        await ws_manager.connect_broadcast(ws, tenant_id)
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == b.email))
        await EmailService.process_inbound(db, agent, sender="s@example.org", subject="SECRETB live mail",
                                           body_text="live")
    assert any("SECRETB live mail" in json.dumps(e) for e in feeds[TENANT_B].sent)
    assert feeds[TENANT_A].sent == []
    ws_manager.broadcast_connections.clear()


async def test_stats_count_only_the_callers_tenant(client, world):
    a, b = world
    stats_a = (await client.get("/v1/stats", headers=A_HEADERS)).json()
    stats_default = (await client.get("/v1/stats", headers=ADMIN)).json()
    assert stats_a["total_agents"] == 1 and stats_default["total_agents"] == 0
    assert stats_default["total_messages"] == 0


# --- the session net itself ------------------------------------------------------------------------

async def test_a_bound_session_cannot_see_or_write_across_tenants(world):
    a, b = world
    async with AsyncSessionLocal() as db:
        bind_tenant(db, TENANT_A)
        assert [x.email for x in (await db.execute(select(Agent))).scalars()] == [a.email]
        assert await db.get(Message, b.ids["message_id"]) is None
        wrong = Agent(tenant_id=TENANT_B, id="ag_wrong", email="wrong@agents.example.com", display_name="w")
        db.add(wrong)
        with pytest.raises(ValueError):
            await db.flush()


async def test_an_unbound_session_must_name_the_tenant_of_new_rows():
    async with AsyncSessionLocal() as db:
        db.add(Agent(id="ag_orphan", email="orphan@agents.example.com", display_name="o"))
        with pytest.raises(ValueError, match="without a tenant_id"):
            await db.flush()


async def test_every_tenant_owned_table_has_an_indexed_tenant_column():
    from app.tenancy import TenantOwned
    owned = {m.local_table.name for m in Base.registry.mappers if issubclass(m.class_, TenantOwned)}
    global_tables = {"server_settings", "smtp_configs", "provider_feedback_receipts"}
    assert owned | global_tables == set(Base.metadata.tables)
    for name in owned:
        table = Base.metadata.tables[name]
        assert not table.c.tenant_id.nullable
        assert any("tenant_id" in [c.name for c in ix.columns] for ix in table.indexes), name


# --- gaps found by mutation testing ----------------------------------------------------------------

async def test_stats_report_only_the_callers_open_sockets(client, world):
    ws_manager.broadcast_connections.clear()
    await ws_manager.connect_broadcast(FakeSocket(None), TENANT_B)
    assert (await client.get("/v1/stats", headers=B_HEADERS)).json()["active_websockets"] == 1
    assert (await client.get("/v1/stats", headers=A_HEADERS)).json()["active_websockets"] == 0
    ws_manager.broadcast_connections.clear()


async def test_dkim_signing_never_uses_another_tenants_domain(client, world):
    a, b = world
    async with AsyncSessionLocal() as db:
        domain = await db.scalar(select(Domain).where(Domain.domain == b.ids["domain_name"]))
        domain.status = "verified"
        await db.commit()
    async with AsyncSessionLocal() as db:
        sender = f"bot@{b.ids['domain_name']}"
        assert await EmailService._dkim_signing_for(db, sender, TENANT_B) is not None
        assert await EmailService._dkim_signing_for(db, sender, TENANT_A) is None


async def test_account_scoped_key_stays_in_its_tenant(client, world):
    a, b = world
    made = await client.post("/v1/account/api-keys", headers=A_HEADERS, json={"name": "acct", "scope": "account"})
    assert made.status_code == 201, made.text
    key = {"Authorization": f"Bearer {made.json()['key']}"}
    listed = (await client.get("/v1/agents", headers=key)).json()
    assert [x["email"] for x in listed] == [a.email]
    assert (await client.get(f"/v1/agents/{b.email}", headers=key)).status_code == 404


async def test_review_resolution_is_published_only_to_the_owning_tenant(client, world):
    a, b = world
    ws_manager.broadcast_connections.clear()
    feeds = {t: FakeSocket(None) for t in (TENANT_A, TENANT_B)}
    for tenant_id, ws in feeds.items():
        await ws_manager.connect_broadcast(ws, tenant_id)
    r = await client.post(f"/v1/reviews/{a.ids['review_id']}/approve", headers=A_HEADERS, json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert any(e["type"] == "review.resolved" for e in feeds[TENANT_A].sent)
    assert feeds[TENANT_B].sent == []
    ws_manager.broadcast_connections.clear()


async def test_domain_events_stay_with_the_domains_tenant(client, world):
    a, b = world
    from app.services.webhook_events import enqueue_domain_webhook_event
    async with AsyncSessionLocal() as db:
        await enqueue_domain_webhook_event(
            db, event_type="domain.sending_verified", domain=b.ids["domain_name"], status="verified", records={},
            created_at=datetime.datetime.utcnow(), tenant_id=TENANT_B)
        await db.commit()
        rows = (await db.execute(select(WebhookDelivery).where(
            WebhookDelivery.event_type == "domain.sending_verified"))).scalars().all()
    owners = {r.endpoint_id for r in rows}
    assert b.ids["account_endpoint_id"] in owners
    assert a.ids["account_endpoint_id"] not in owners and all(r.tenant_id == TENANT_B for r in rows)
