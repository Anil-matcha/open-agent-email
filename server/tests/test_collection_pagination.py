from tests.conftest import ADMIN, make_agent

A = "a@agents.local"


async def test_engagement_pages_follow_stable_order_and_bind_due_filter(client):
    await make_agent(client, A)
    ids = []
    for address in ("one@example.com", "two@example.com"):
        contact = await client.post(f"/v1/agents/{A}/contacts", headers=ADMIN,
                                    json={"email": address, "name": address})
        assert contact.status_code == 201
        engagement = await client.post(f"/v1/agents/{A}/engagements", headers=ADMIN,
                                       json={"contact_id": contact.json()["id"]})
        assert engagement.status_code == 201
        ids.append(engagement.json()["id"])
    first = await client.get(f"/v1/agents/{A}/engagements/page?limit=1", headers=ADMIN)
    assert first.status_code == 200 and len(first.json()["items"]) == 1 and first.json()["next_cursor"]
    second = await client.get(f"/v1/agents/{A}/engagements/page?limit=1&cursor=" + first.json()["next_cursor"], headers=ADMIN)
    assert len(second.json()["items"]) == 1 and second.json()["next_cursor"] is None
    assert (await client.get(f"/v1/agents/{A}/engagements/page?due=true&cursor=" + first.json()["next_cursor"], headers=ADMIN)).status_code == 400


async def test_custom_cursor_families_use_standard_error_code(client):
    for path in ("/v1/outbox/page?cursor=bad", "/v1/webhooks/deliveries/page?cursor=bad"):
        response = await client.get(path, headers=ADMIN)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_cursor"
        assert response.json()["error"]["code"] == "invalid_cursor"


async def test_nested_history_pages_expose_items_and_next_cursor(client):
    await make_agent(client, A)
    imports = await client.get(f"/v1/agents/{A}/contacts/imports/page", headers=ADMIN)
    assert imports.status_code == 200
    assert imports.json() == {"items": [], "next_cursor": None}
    thread = await client.get(f"/v1/agents/{A}/messages/thread/missing/page", headers=ADMIN)
    assert thread.status_code == 200
    assert thread.json() == {"items": [], "next_cursor": None}
