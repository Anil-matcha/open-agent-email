import pytest

from app.services.starter_templates import STARTER_TEMPLATES
from tests.conftest import ADMIN, make_agent, make_key

A, B = "a@agents.local", "b@agents.local"
VALID = {"subject": "Hi {{name}}", "body_text": "Hello {{name}}", "body_html": "<p>{{name}}</p>", "data": {"name": "Sam"}}


async def test_validate_renders_and_escapes_html(client):
    r = await client.post("/v1/templates/validate", headers=ADMIN, json={**VALID, "data": {"name": "<b>Sam</b>"}})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["subject"] == "Hi <b>Sam</b>"
    assert body["body_html"] == "<p>&lt;b&gt;Sam&lt;/b&gt;</p>" and body["variables"] == ["name"]


@pytest.mark.parametrize("payload", [
    {**VALID, "subject": "Hi {{name"},
    {**VALID, "body_text": "{{ bad-name }}"},
    {**VALID, "subject": "line\nbreak"},
    {"subject": "s", "body_text": "", "body_html": "", "data": {}},
])
async def test_validate_rejects_bad_templates(client, payload):
    assert (await client.post("/v1/templates/validate", headers=ADMIN, json=payload)).status_code == 422


async def test_validate_requires_authentication_and_stores_nothing(client):
    assert (await client.post("/v1/templates/validate", json=VALID)).status_code == 401
    await make_agent(client, A)
    key, _ = await make_key(client, A, "read_only")
    assert (await client.post("/v1/templates/validate", headers=key, json=VALID)).status_code == 200
    assert (await client.get(f"/v1/agents/{A}/templates", headers=ADMIN)).json() == []


async def test_catalog_lists_every_starter_without_bodies(client):
    items = (await client.get("/v1/starter-templates", headers=ADMIN)).json()["items"]
    assert [i["alias"] for i in items] == [t["alias"] for t in STARTER_TEMPLATES]
    assert len(items) >= 8 and all("body_text" not in i for i in items)
    assert (await client.get("/v1/starter-templates")).status_code == 401


async def test_starter_template_pages_match_catalog_and_reject_foreign_cursor(client):
    first = await client.get("/v1/starter-templates/page?limit=2", headers=ADMIN)
    assert first.status_code == 200 and len(first.json()["items"]) == 2 and first.json()["next_cursor"]
    second = await client.get("/v1/starter-templates/page?limit=2&cursor=" + first.json()["next_cursor"], headers=ADMIN)
    assert [item["alias"] for item in first.json()["items"] + second.json()["items"]] == [item["alias"] for item in STARTER_TEMPLATES[:4]]
    assert (await client.get("/v1/starter-templates/page?cursor=bad", headers=ADMIN)).status_code == 400


@pytest.mark.parametrize("alias", [t["alias"] for t in STARTER_TEMPLATES])
async def test_every_starter_renders_with_its_sample_data(client, alias):
    r = await client.get(f"/v1/starter-templates/{alias}", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    preview = body["preview"]
    assert preview["subject"] and "{{" not in preview["subject"] + preview["body_text"] + preview["body_html"]
    assert body["variables"], "starter should use at least one variable"
    # every declared variable has a value in the sample data, so previews are not blank
    for variable in body["variables"]:
        value = body["sample_data"]
        for part in variable.split("."):
            value = value[part]
        assert str(value) in preview["body_text"] + preview["subject"] + preview["body_html"]


async def test_unknown_starter_is_404(client):
    assert (await client.get("/v1/starter-templates/nope", headers=ADMIN)).status_code == 404
    await make_agent(client, A)
    assert (await client.post(f"/v1/agents/{A}/templates/from-starter/nope", headers=ADMIN)).status_code == 404


async def test_copy_starter_into_inbox_then_edit_independently(client):
    await make_agent(client, A)
    r = await client.post(f"/v1/agents/{A}/templates/from-starter/welcome", headers=ADMIN)
    assert r.status_code == 201 and r.json()["name"] == "Welcome"
    tid = r.json()["id"]
    assert (await client.patch(f"/v1/agents/{A}/templates/{tid}", headers=ADMIN, json={"subject": "Custom {{name}}"})).status_code == 200
    # the catalog entry is unchanged by the edit
    assert (await client.get("/v1/starter-templates/welcome", headers=ADMIN)).json()["subject"].startswith("Welcome to")
    # copying twice conflicts on the name, never silently duplicates
    assert (await client.post(f"/v1/agents/{A}/templates/from-starter/welcome", headers=ADMIN)).status_code == 409


async def test_copied_starter_can_be_used_to_send(client):
    await client.post("/v1/agents", headers=ADMIN, json={"email": A, "display_name": "A", "hitl_enabled": False})
    tid = (await client.post(f"/v1/agents/{A}/templates/from-starter/verification-code", headers=ADMIN)).json()["id"]
    r = await client.post(f"/v1/agents/{A}/messages", headers=ADMIN, json={
        "recipient": "to@example.com", "template_id": tid,
        "template_data": {"product": "Acme", "code": "123456", "expires_minutes": 5}})
    assert r.status_code == 201 and "123456" in r.json()["subject"]


async def test_raw_slots_and_saved_template_alias_send(client):
    await make_agent(client, A)
    created = await client.post(f"/v1/agents/{A}/templates", headers=ADMIN, json={
        "name": "alias template", "alias": "receipt-v1", "subject": "Hi {{name}}",
        "body_text": "{{{fragment}}}", "body_html": "<div>{{name}}|{{{fragment}}}</div>",
    })
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    rendered = await client.post(f"/v1/agents/{A}/templates/{tid}/preview", headers=ADMIN,
                                 json={"data": {"name": "<Ada>", "fragment": "<b>raw</b>"}})
    assert rendered.status_code == 200
    body = rendered.json()
    assert body["body_text"] == "<b>raw</b>"
    assert body["body_html"] == "<div>&lt;Ada&gt;|<b>raw</b></div>"
    sent = await client.post(f"/v1/agents/{A}/messages", headers=ADMIN, json={
        "recipient": "to@example.com", "template_alias": "receipt-v1",
        "template_data": {"name": "Ada", "fragment": "raw"},
    })
    assert sent.status_code in (201, 202), sent.text


async def test_copy_starter_scope_and_inbox_isolation(client):
    await make_agent(client, A)
    await make_agent(client, B)
    ro, _ = await make_key(client, A, "read_only")
    full_b, _ = await make_key(client, B, "full_access")
    full_a, _ = await make_key(client, A, "full_access")
    path = f"/v1/agents/{A}/templates/from-starter/receipt"
    assert (await client.post(path)).status_code == 401
    assert (await client.post(path, headers=ro)).status_code == 403
    assert (await client.post(path, headers=full_b)).status_code == 403
    assert (await client.post(path, headers=full_a)).status_code == 201
    assert (await client.post("/v1/agents/missing@agents.local/templates/from-starter/receipt", headers=ADMIN)).status_code == 404
