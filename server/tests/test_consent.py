from tests.conftest import ADMIN, make_agent, make_key

BASE = "/v1/agents/a@agents.local/contacts"


async def _contact(client, headers=ADMIN):
    r = await client.post(BASE, headers=headers, json={"email": "person@example.com", "name": "P"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_consent_history_is_append_only_and_current_is_latest(client):
    await make_agent(client, "a@agents.local")
    cid = await _contact(client)
    assert (await client.get(f"{BASE}/{cid}/consent", headers=ADMIN)).json() == {"current": "unknown", "events": []}
    for action in ("granted", "revoked", "granted"):
        r = await client.post(f"{BASE}/{cid}/consent", headers=ADMIN, json={"action": action, "source": "web form"})
        assert r.status_code == 201
    body = (await client.get(f"{BASE}/{cid}/consent", headers=ADMIN)).json()
    assert body["current"] == "granted"
    assert [e["action"] for e in body["events"]] == ["granted", "revoked", "granted"][::-1]


async def test_consent_validation(client):
    await make_agent(client, "a@agents.local")
    cid = await _contact(client)
    for bad in ({"action": "maybe", "source": "x"}, {"action": "granted", "source": "   "}, {"action": "granted"}):
        assert (await client.post(f"{BASE}/{cid}/consent", headers=ADMIN, json=bad)).status_code == 422


async def test_consent_scoping(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    cid = await _contact(client)
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    assert (await client.post(f"{BASE}/{cid}/consent", headers=ro, json={"action": "granted", "source": "x"})).status_code == 403
    assert (await client.get(f"{BASE}/{cid}/consent", headers=ro)).status_code == 200
    other, _ = await make_key(client, "b@agents.local", "full_access")
    assert (await client.get(f"{BASE}/{cid}/consent", headers=other)).status_code == 403
    # a contact id from inbox A is not addressable through inbox B
    r = await client.get(f"/v1/agents/b@agents.local/contacts/{cid}/consent", headers=ADMIN)
    assert r.status_code == 404
