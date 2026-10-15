from tests.conftest import ADMIN, make_agent


async def test_json_import_creates_merges_duplicate_rows_and_suppresses_without_mail(client):
    inbox = "contacts@example.com"
    await make_agent(client, inbox)
    response = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "contacts": [
            {"email": "One@Example.com", "display_name": "First", "metadata": {"tier": "free"}, "stage": "new"},
            {"email": "one@example.com", "display_name": "One", "metadata": {"tier": "paid", "active": True}, "suppressed": True},
        ],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["created"], body["updated"], body["skipped"], body["failed"]) == (1, 1, 0, 0)
    assert body["results"][0]["status"] == "created"
    assert body["results"][1]["status"] == "updated"
    assert body["results"][1]["suppressed"] is True
    contact = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()[0]
    assert contact["name"] == "One"
    assert contact["metadata"] == {"tier": "paid", "active": True}
    engagement = (await client.get(f"/v1/agents/{inbox}/engagements", headers=ADMIN)).json()
    assert len(engagement) == 1 and engagement[0]["stage"] == "new"
    assert (await client.get("/v1/account/suppressions", headers=ADMIN)).json()["items"][0]["email"] == "one@example.com"


async def test_json_import_accepts_address_and_initializes_top_level_stage(client):
    inbox = "address-import@example.com"
    await make_agent(client, inbox)
    response = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "stage": "contacted",
        "contacts": [{"address": "person@example.com", "display_name": "Person"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["email"] == "person@example.com"
    assert response.json()["results"][0]["address"] == "person@example.com"
    assert response.json()["results"][0]["index"] == 0
    engagement = (await client.get(f"/v1/agents/{inbox}/engagements", headers=ADMIN)).json()
    assert len(engagement) == 1 and engagement[0]["stage"] == "contacted"


async def test_json_import_keeps_existing_stage_when_merging_with_new_stage(client):
    inbox = "stage-import@example.com"
    await make_agent(client, inbox)
    first = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox, "stage": "waiting",
        "contacts": [{"address": "person@example.com"}],
    })
    assert first.status_code == 200, first.text
    merged = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox, "stage": "closed",
        "contacts": [{"address": "person@example.com", "stage": "contacted"}],
    })
    assert merged.status_code == 200, merged.text
    assert merged.json()["results"][0]["stage"] == "waiting"
    engagement = (await client.get(f"/v1/agents/{inbox}/engagements", headers=ADMIN)).json()
    assert len(engagement) == 1 and engagement[0]["stage"] == "waiting"


async def test_json_import_metadata_utf8_boundaries_are_row_isolated(client):
    inbox = "metadata-boundaries@example.com"
    await make_agent(client, inbox)
    valid_metadata = {"é" * 64: "é" * 2048}  # 128-byte key and 4,096-byte value
    too_many_keys = {f"key-{index}": index for index in range(51)}
    too_long_key = {"é" * 65: "value"}
    too_large_object = {f"key-{index}": "v" * 4096 for index in range(5)}
    response = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "contacts": [
            {"address": "valid@example.com", "metadata": valid_metadata},
            {"address": "many@example.com", "metadata": too_many_keys},
            {"address": "key@example.com", "metadata": too_long_key},
            {"address": "object@example.com", "metadata": too_large_object},
            {"address": "value@example.com", "metadata": {"value": "é" * 2049}},
        ],
    })
    assert response.status_code == 200, response.text
    assert (response.json()["created"], response.json()["failed"]) == (1, 4)
    contact = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()[0]
    assert contact["metadata"] == valid_metadata


async def test_json_import_accepts_mailbox_address_and_isolates_overlong_address(client):
    inbox = "mailbox-import@example.com"
    await make_agent(client, inbox)
    response = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "contacts": [
            {"address": "Friendly Person <person@example.com>"},
            {"address": f"{'a' * 321}@example.com"},
        ],
    })
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["status"] == "created"
    assert results[0]["address"] == "person@example.com"
    assert results[1]["status"] == "failed"
    contacts = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()
    assert [contact["email"] for contact in contacts] == ["person@example.com"]


async def test_json_import_reversal_retains_contact_with_later_edited_engagement(client):
    inbox = "reversal-stage@example.com"
    await make_agent(client, inbox)
    imported = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "stage": "new",
        "contacts": [{"address": "person@example.com"}],
    })
    assert imported.status_code == 200, imported.text
    contact = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()[0]
    engagement = (await client.get(f"/v1/agents/{inbox}/engagements", headers=ADMIN)).json()[0]
    edited = await client.patch(
        f"/v1/agents/{inbox}/engagements/{engagement['id']}",
        headers=ADMIN, json={"stage": "waiting"})
    assert edited.status_code == 200, edited.text

    reversed_batch = await client.delete(
        f"/v1/agents/{inbox}/contacts/imports/{imported.json()['batch_id']}?confirm=DELETE",
        headers=ADMIN)
    assert reversed_batch.status_code == 200, reversed_batch.text
    assert reversed_batch.json()["deleted"] == 0
    assert reversed_batch.json()["retained_after_edits"] == 1
    remaining_contacts = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()
    remaining_engagements = (await client.get(f"/v1/agents/{inbox}/engagements", headers=ADMIN)).json()
    assert [row["id"] for row in remaining_contacts] == [contact["id"]]
    assert len(remaining_engagements) == 1
    assert remaining_engagements[0]["stage"] == "waiting"


async def test_inbox_contact_get_patch_delete_accept_address_identifier(client):
    inbox = "contact-address-path@example.com"
    await make_agent(client, inbox)
    created = await client.post(
        f"/v1/agents/{inbox}/contacts", headers=ADMIN,
        json={"email": "person@example.com", "name": "Person"},
    )
    assert created.status_code == 201, created.text
    address_path = f"/v1/agents/{inbox}/contacts/person@example.com"
    assert (await client.get(address_path, headers=ADMIN)).json()["id"] == created.json()["id"]
    updated = await client.patch(address_path, headers=ADMIN, json={"name": "Updated"})
    assert updated.status_code == 200 and updated.json()["name"] == "Updated"
    assert (await client.delete(address_path, headers=ADMIN)).status_code == 204


async def test_json_import_skip_mode_and_invalid_row_keep_valid_rows(client):
    inbox = "contacts@example.com"
    await make_agent(client, inbox)
    response = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox,
        "on_conflict": "skip",
        "contacts": [
            {"email": "new@example.com", "display_name": "New"},
            {"email": "NEW@example.com", "display_name": "Duplicate"},
            {"email": "not-an-email", "display_name": "Invalid"},
            "not-an-object",
            {"email": "last@example.com", "display_name": "Last"},
        ],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["created"], body["updated"], body["skipped"], body["failed"]) == (2, 0, 1, 2)
    assert [row["status"] for row in body["results"]] == ["created", "skipped", "failed", "failed", "created"]
    contacts = (await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()
    assert {row["email"] for row in contacts} == {"new@example.com", "last@example.com"}
    assert next(row for row in contacts if row["email"] == "new@example.com")["name"] == "New"


async def test_json_import_is_idempotent_and_replays_original_per_row_result(client):
    inbox = "contacts@example.com"
    await make_agent(client, inbox)
    headers = {**ADMIN, "Idempotency-Key": "json-contact-import-1"}
    payload = {"agent_email": inbox, "contacts": [{"email": "one@example.com"}]}
    first = await client.post("/v1/contacts/import", headers=headers, json=payload)
    replay = await client.post("/v1/contacts/import", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert len((await client.get(f"/v1/agents/{inbox}/contacts", headers=ADMIN)).json()) == 1
    conflict = await client.post("/v1/contacts/import", headers=headers,
                                 json={"agent_email": inbox, "contacts": [{"email": "two@example.com"}]})
    assert conflict.status_code == 422


async def test_json_import_requires_inbox_owner_and_rejects_nested_metadata(client):
    inbox = "owner@example.com"
    other = "other@example.com"
    await make_agent(client, inbox)
    await make_agent(client, other)
    key_response = await client.post("/v1/api-keys", headers=ADMIN, json={
        "name": "owner", "agent_email": inbox, "scope": "full_access"})
    key = {"Authorization": f"Bearer {key_response.json()['key']}"}
    forbidden = await client.post("/v1/contacts/import", headers=key, json={
        "agent_email": other, "contacts": [{"email": "one@example.com"}]})
    assert forbidden.status_code == 403
    invalid_metadata = await client.post("/v1/contacts/import", headers=ADMIN, json={
        "agent_email": inbox, "contacts": [{"email": "one@example.com", "metadata": {"nested": {"x": 1}}}]})
    assert invalid_metadata.status_code == 200
    assert invalid_metadata.json()["failed"] == 1


async def test_contact_metadata_patch_uses_mapped_json_column(client):
    inbox = "metadata@example.com"
    await make_agent(client, inbox)
    created = await client.post("/v1/contacts", headers=ADMIN, json={
        "agent_email": inbox, "email": "person@example.com", "name": "Person",
    })
    assert created.status_code == 201, created.text
    updated = await client.patch("/v1/contacts/person@example.com?agent_email=metadata%40example.com",
                                 headers=ADMIN, json={"metadata": {"source": "import"}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["metadata"] == {"source": "import"}

