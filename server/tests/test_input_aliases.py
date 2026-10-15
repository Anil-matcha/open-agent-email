from tests.conftest import ADMIN

BASE = "/v1/agents/a@agents.local"


async def _agent(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": "a@agents.local", "hitl_enabled": False})
    assert r.status_code == 201, r.text


async def _send(client, **payload):
    return await client.post(f"{BASE}/messages", headers=ADMIN, json=payload)


async def test_send_accepts_to_text_html_aliases(client):
    await _agent(client)
    r = await _send(client, to="person@example.com", subject="Hi", text="plain", html="<p>rich</p>")
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["recipient"], body["body_text"], body["body_html"]) == ("person@example.com", "plain", "<p>rich</p>")


async def test_send_alias_matching_canonical_is_accepted_and_conflict_rejected(client):
    await _agent(client)
    ok = await _send(client, to="p@example.com", recipient="p@example.com", subject="Hi", body_text="x")
    assert ok.status_code == 201, ok.text
    bad = await _send(client, to="p@example.com", recipient="q@example.com", subject="Hi", body_text="x")
    assert bad.status_code == 422


async def test_canonical_names_still_work(client):
    await _agent(client)
    r = await _send(client, recipient="p@example.com", subject="Hi", body_text="x")
    assert r.status_code == 201, r.text


async def test_reply_accepts_text_alias_and_rejects_reply_all(client):
    await _agent(client)
    mid = (await _send(client, to="p@example.com", subject="Hi", text="x")).json()["id"]
    ok = await client.post(f"{BASE}/messages/{mid}/reply", headers=ADMIN, json={"text": "answer"})
    assert ok.status_code == 201 and ok.json()["body_text"] == "answer"
    flag_off = await client.post(f"{BASE}/messages/{mid}/reply", headers=ADMIN, json={"text": "a", "reply_all": False})
    assert flag_off.status_code == 201
    all_ = await client.post(f"{BASE}/messages/{mid}/reply", headers=ADMIN, json={"text": "a", "reply_all": True})
    assert all_.status_code == 422
    assert all_.json()["error"]["code"] == "reply_all_unsupported"


async def test_forward_accepts_to_alias(client):
    await _agent(client)
    mid = (await _send(client, to="p@example.com", subject="Hi", text="x")).json()["id"]
    r = await client.post(f"{BASE}/messages/{mid}/forward", headers=ADMIN, json={"to": "lead@example.com", "note": "fyi"})
    assert r.status_code == 201, r.text
    assert r.json()["recipient"] == "lead@example.com"


async def test_add_and_remove_labels(client):
    await _agent(client)
    mid = (await _send(client, to="p@example.com", subject="Hi", text="x")).json()["id"]
    url = f"{BASE}/messages/{mid}"
    r = await client.patch(url, headers=ADMIN, json={"labels": ["a", "b"]})
    assert r.json()["labels"] == ["a", "b"]
    r = await client.patch(url, headers=ADMIN, json={"add_labels": ["c", "a"], "remove_labels": ["b"]})
    assert r.status_code == 200 and r.json()["labels"] == ["a", "c"]
    assert (await client.patch(url, headers=ADMIN, json={})).status_code == 422
    assert (await client.patch(url, headers=ADMIN, json={"labels": ["x"], "add_labels": ["y"]})).status_code == 422


async def test_page_token_is_an_alias_for_cursor(client):
    await _agent(client)
    for i in range(3):
        await _send(client, to="p@example.com", subject=f"s{i}", text="x")
    first = (await client.get(f"{BASE}/messages/page?limit=2", headers=ADMIN)).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    via_cursor = (await client.get(f"{BASE}/messages/page?limit=2&cursor={first['next_cursor']}", headers=ADMIN)).json()
    via_token = (await client.get(f"{BASE}/messages/page?limit=2&page_token={first['next_cursor']}", headers=ADMIN)).json()
    assert via_token == via_cursor and len(via_token["items"]) == 1
    mismatch = await client.get(f"{BASE}/messages/page?cursor=aaa&page_token=bbb", headers=ADMIN)
    assert mismatch.status_code == 400
