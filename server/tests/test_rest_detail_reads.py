from tests.conftest import ADMIN, make_agent


A = "a@agents.local"
B = "b@agents.local"


async def test_contact_detail_is_inbox_scoped(client):
    await make_agent(client, A)
    await make_agent(client, B)
    created = await client.post(f"/v1/agents/{A}/contacts", headers=ADMIN,
                                json={"email": "person@example.com", "name": "Person"})
    contact_id = created.json()["id"]

    response = await client.get(f"/v1/agents/{A}/contacts/{contact_id}", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["id"] == contact_id
    assert response.json()["agent_email"] == A
    assert (await client.get(f"/v1/agents/{B}/contacts/{contact_id}", headers=ADMIN)).status_code == 404
    assert (await client.get(f"/v1/agents/{A}/contacts/missing", headers=ADMIN)).status_code == 404


async def test_domain_detail_is_admin_scoped(client):
    created = await client.post("/v1/domains", headers=ADMIN, json={"domain": "Mail.Example.com."})
    assert created.status_code == 201

    response = await client.get("/v1/domains/mail.example.com", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["domain"] == "mail.example.com"
    assert "dkim_private_key" not in response.json()
    assert (await client.get("/v1/domains/missing.example.com", headers=ADMIN)).status_code == 404
    assert (await client.get("/v1/domains/mail.example.com", headers={"Authorization": "Bearer invalid"})).status_code == 401


async def test_engagement_detail_is_inbox_scoped_and_uses_response_model(client):
    await make_agent(client, A)
    await make_agent(client, B)
    contact = await client.post(f"/v1/agents/{A}/contacts", headers=ADMIN,
                                json={"email": "person@example.com", "name": "Person"})
    created = await client.post(f"/v1/agents/{A}/engagements", headers=ADMIN,
                                json={"contact_id": contact.json()["id"], "stage": "waiting"})
    engagement_id = created.json()["id"]

    response = await client.get(f"/v1/agents/{A}/engagements/{engagement_id}", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["id"] == engagement_id
    assert response.json()["contact_email"] == "person@example.com"
    assert response.json()["stage"] == "waiting"
    assert (await client.get(f"/v1/agents/{B}/engagements/{engagement_id}", headers=ADMIN)).status_code == 404
    assert (await client.get(f"/v1/agents/{A}/engagements/missing", headers=ADMIN)).status_code == 404
