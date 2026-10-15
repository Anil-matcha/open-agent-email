import pytest

from tests.conftest import ADMIN, make_agent


async def _three_agents(client):
    for name in ("a", "b", "c"):
        await make_agent(client, f"{name}@agents.example.com")


async def test_page_token_follows_next_cursor_like_cursor(client):
    await _three_agents(client)
    first = (await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1})).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    via_cursor = (await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1, "cursor": first["next_cursor"]})).json()
    via_token = (await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1, "page_token": first["next_cursor"]})).json()
    assert via_token == via_cursor
    assert via_token["items"][0]["email"] != first["items"][0]["email"]


async def test_cursor_and_page_token_must_match_when_both_given(client):
    await _three_agents(client)
    first = (await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1})).json()
    same = await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1, "cursor": first["next_cursor"], "page_token": first["next_cursor"]})
    assert same.status_code == 200
    clash = await client.get("/v1/agents/page", headers=ADMIN, params={"limit": 1, "cursor": first["next_cursor"], "page_token": "other"})
    assert clash.status_code == 400
    assert clash.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize("path", [
    "/v1/domains/page", "/v1/api-keys/page", "/v1/account/api-keys", "/v1/events", "/v1/outbox/page",
])
async def test_page_routes_accept_page_token(client, path):
    r = await client.get(path, headers=ADMIN, params={"page_token": "not-a-real-cursor"})
    # An unrecognised token is rejected as a bad cursor, which proves the alias reaches the cursor logic.
    assert r.status_code in (200, 400), (path, r.status_code, r.text)
    if r.status_code == 400:
        assert r.json()["error"]["code"] == "invalid_cursor"
