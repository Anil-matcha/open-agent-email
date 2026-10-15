import pytest

from tests.conftest import ADMIN


@pytest.mark.asyncio
async def test_display_name_defaults_to_address_local_part(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": "hello@agents.example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["display_name"] == "hello"


@pytest.mark.asyncio
async def test_explicit_display_name_is_kept(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": "a@agents.example.com", "display_name": "Support"})
    assert r.status_code == 201, r.text
    assert r.json()["display_name"] == "Support"
