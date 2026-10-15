import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

import app.migrate as migrate
from app.config import settings
from app.database import AsyncSessionLocal
from app.services import server_settings
from tests.conftest import ADMIN, make_agent, make_key


@pytest.fixture(autouse=True)
def restore_settings():
    yield
    for key, value in server_settings._BASELINE.items():
        setattr(settings, key, value)


def by_key(body):
    return {item["key"]: item for item in body["items"]}


async def put(client, values, headers=ADMIN):
    return await client.put("/v1/settings", headers=headers, json={"values": values})


async def test_list_shows_value_source_and_type_for_each_kind(client):
    r = await client.get("/v1/settings", headers=ADMIN)
    assert r.status_code == 200
    items = by_key(r.json())
    ttl = items["TRASH_RETENTION_DAYS"]
    assert ttl["kind"] == "setting" and ttl["type"] == "integer" and ttl["editable"] is True
    assert ttl["source"] in {"env", "default"} and ttl["minimum"] == 0 and ttl["maximum"] == 3650
    assert items["PORT"]["editable"] is False and items["PORT"]["requires_restart"] is True
    assert items["PUBLIC_BASE_URL"]["example"].startswith("https://")


async def test_saved_value_wins_over_env_applies_live_and_can_be_removed(client):
    settings.TRASH_RETENTION_DAYS = 11  # stands in for an environment value
    server_settings._BASELINE["TRASH_RETENTION_DAYS"] = 11
    r = await put(client, {"TRASH_RETENTION_DAYS": 7})
    assert r.status_code == 200, r.text
    item = by_key(r.json())["TRASH_RETENTION_DAYS"]
    assert item["value"] == 7 and item["source"] == "ui" and item["updated_by"]
    assert settings.TRASH_RETENTION_DAYS == 7

    r = await put(client, {"TRASH_RETENTION_DAYS": None})
    item = by_key(r.json())["TRASH_RETENTION_DAYS"]
    assert item["value"] == 11 and item["source"] != "ui"
    assert settings.TRASH_RETENTION_DAYS == 11


async def test_overrides_are_loaded_again_at_startup(client):
    assert (await put(client, {"SPF_INCLUDE": "amazonses.com", "WEBHOOK_DELIVERY_RETENTION_DAYS": 5})).status_code == 200
    settings.SPF_INCLUDE = ""
    settings.WEBHOOK_DELIVERY_RETENTION_DAYS = 30
    async with AsyncSessionLocal() as db:
        assert await server_settings.load_overrides(db) == 2
    assert settings.SPF_INCLUDE == "amazonses.com" and settings.WEBHOOK_DELIVERY_RETENTION_DAYS == 5


async def test_public_base_url_follows_the_unsubscribe_link_rules(client):
    for bad in ("http://mail.example.com", "https://localhost", "https://user:pw@mail.example.com",
                "https://mail.example.com?x=1", "https://mail.example.com:8443", "https://192.168.0.5"):
        r = await put(client, {"PUBLIC_BASE_URL": bad})
        assert r.status_code == 422, bad
        assert "PUBLIC_BASE_URL" in r.text
    r = await put(client, {"PUBLIC_BASE_URL": "https://mail.example.com/"})
    assert r.status_code == 200
    assert by_key(r.json())["PUBLIC_BASE_URL"]["value"] == "https://mail.example.com"
    from app.services.unsubscribe import public_base_url
    assert public_base_url() == "https://mail.example.com"
    assert (await put(client, {"PUBLIC_BASE_URL": ""})).status_code == 200
    assert public_base_url() is None


@pytest.mark.parametrize("values", [
    {"TRASH_RETENTION_DAYS": -1}, {"TRASH_RETENTION_DAYS": 4000}, {"TRASH_RETENTION_DAYS": "7"},
    {"TRASH_RETENTION_DAYS": True}, {"TRASH_RETENTION_DAYS": 1.5}, {"WEBHOOK_DELIVERY_RETENTION_DAYS": 0},
    {"SES_FEEDBACK_TOPIC_ARN": "not-an-arn"}, {"SES_CONFIGURATION_SET": "bad name!"},
    {"MAIL_HOSTNAME": "bad host"}, {"SHARED_DOMAIN": ""}, {"SPF_INCLUDE": "-bad.example.com"},
    {"OUTBOUND_MAX_ATTACHMENTS": 0}, {"PUBLIC_BASE_URL": 5},
])
async def test_invalid_values_are_rejected_and_change_nothing(client, values):
    before = {k: getattr(settings, k) for k in values}
    r = await put(client, values)
    assert r.status_code == 422, r.text
    assert {k: getattr(settings, k) for k in values} == before


async def test_valid_aws_values_and_cross_field_rule(client):
    arn = "arn:aws:sns:us-east-1:123456789012:ses-feedback"
    r = await put(client, {"SES_FEEDBACK_TOPIC_ARN": arn, "SES_CONFIGURATION_SET": "my-set_1"})
    assert r.status_code == 200 and settings.SES_FEEDBACK_TOPIC_ARN == arn
    r = await put(client, {"OUTBOUND_MAX_ATTACHMENT_BYTES": 50 * 1024 * 1024, "OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES": 1024 * 1024})
    assert r.status_code == 422 and "total attachment limit" in r.text


async def test_one_bad_key_rejects_the_whole_request(client):
    r = await put(client, {"TRASH_RETENTION_DAYS": 3, "SES_FEEDBACK_TOPIC_ARN": "nope"})
    assert r.status_code == 422
    assert by_key((await client.get("/v1/settings", headers=ADMIN)).json())["TRASH_RETENTION_DAYS"]["source"] != "ui"


async def test_restart_only_unknown_and_secret_keys_cannot_be_changed(client):
    for key in ("PORT", "DATABASE_URL", "HOST", "SMTP_PORT", "API_RATE_LIMIT_REQUESTS", "ADMIN_API_KEY",
                "SMTP_CONFIG_ENCRYPTION_KEY", "NOT_A_SETTING"):
        r = await put(client, {key: "x"})
        assert r.status_code == 422, key
        assert key in r.text
    assert settings.PORT == 8000 or isinstance(settings.PORT, int)


async def test_secrets_are_never_returned(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "metrics-token-value-123")
    body = (await client.get("/v1/settings", headers=ADMIN)).json()
    text = str(body)
    for secret in ("test-admin-key", settings.SMTP_CONFIG_ENCRYPTION_KEY, "metrics-token-value-123"):
        assert secret not in text
    items = by_key(body)
    assert items["ADMIN_API_KEY"]["configured"] is True and "value" not in items["ADMIN_API_KEY"]
    assert items["METRICS_TOKEN"]["configured"] is True
    assert items["ATTACHMENT_URL_SIGNING_KEY"]["configured"] in (True, False)


async def test_database_password_is_hidden(client, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://user:s3cretpw@db:5432/oae")
    body = (await client.get("/v1/settings", headers=ADMIN)).json()
    value = by_key(body)["DATABASE_URL"]["value"]
    assert "s3cretpw" not in value and "user" in value


async def test_only_the_administrator_may_read_or_write(client):
    await make_agent(client, "a@agents.local")
    full, _ = await make_key(client, "a@agents.local", "full_access")
    for headers in (full, {}, {"Authorization": "Bearer wrong"}):
        assert (await client.get("/v1/settings", headers=headers)).status_code in (401, 403)
        assert (await client.put("/v1/settings", headers=headers, json={"values": {"TRASH_RETENTION_DAYS": 1}})).status_code in (401, 403)
        assert (await client.get("/v1/settings/readiness", headers=headers)).status_code in (401, 403)
    assert (await client.get("/v1/settings", headers=full)).status_code == 403
    assert settings.TRASH_RETENTION_DAYS != 1


async def test_empty_update_is_rejected(client):
    assert (await put(client, {})).status_code == 422


async def test_readiness_reports_each_check_and_follows_configuration(client, monkeypatch):
    body = (await client.get("/v1/settings/readiness", headers=ADMIN)).json()
    checks = {c["id"]: c for c in body["items"]}
    assert set(checks) == {"admin_key", "encryption_key", "inbound_listener", "relay", "public_url", "domain", "ses_feedback"}
    assert checks["admin_key"]["ok"] and checks["encryption_key"]["ok"]
    assert checks["relay"]["ok"] is False and checks["relay"]["link"] == "/settings"
    assert checks["public_url"]["ok"] is False and "PUBLIC_BASE_URL" in checks["public_url"]["link"]
    assert checks["ses_feedback"]["severity"] == "optional" and checks["ses_feedback"]["ok"] is False
    assert body["ready"] is False

    await put(client, {"PUBLIC_BASE_URL": "https://mail.example.com",
                       "SES_FEEDBACK_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:t"})
    checks = {c["id"]: c for c in (await client.get("/v1/settings/readiness", headers=ADMIN)).json()["items"]}
    assert checks["public_url"]["ok"] and checks["public_url"]["fix"] is None and checks["public_url"]["link"] is None
    assert checks["ses_feedback"]["ok"]

    r = await client.post("/v1/settings/smtp", headers=ADMIN, json={
        "host": "smtp.example.com", "port": "587", "username": "u", "password": "p",
        "use_tls": True, "use_ssl": False, "from_email": "a@example.com", "enabled": True})
    assert r.status_code == 200, r.text
    checks = {c["id"]: c for c in (await client.get("/v1/settings/readiness", headers=ADMIN)).json()["items"]}
    assert checks["relay"]["ok"] is True


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(migrate.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    return path


async def test_migration_0011_upgrades_and_downgrades(db_path):
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "0010")
    engine = create_engine(f"sqlite:///{db_path}")
    assert "server_settings" not in inspect(engine).get_table_names()
    engine.dispose()
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "0011")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"] for c in inspect(engine).get_columns("server_settings")}
    assert columns == {"key", "value", "updated_at", "updated_by"}
    engine.dispose()
    await migrate.asyncio.to_thread(migrate.command.downgrade, migrate._config(), "0010")
    engine = create_engine(f"sqlite:///{db_path}")
    assert "server_settings" not in inspect(engine).get_table_names()
    engine.dispose()
    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchall() == [("0010",)]
    con.close()


async def _mcp(client, name, **arguments):
    r = await client.post("/mcp/rpc", headers=ADMIN, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    result = r.json()["result"]
    import json
    return result.get("isError", False), json.loads(result["content"][0]["text"]) if not result.get("isError") else result["content"][0]["text"]


async def test_mcp_tools_read_update_and_validate_like_rest(client):
    is_error, body = await _mcp(client, "get_server_settings")
    assert not is_error and "TRASH_RETENTION_DAYS" in by_key(body) and "ADMIN_API_KEY" in by_key(body)
    assert "test-admin-key" not in str(body)

    is_error, body = await _mcp(client, "update_server_settings", values={"TRASH_RETENTION_DAYS": 9})
    assert not is_error and by_key(body)["TRASH_RETENTION_DAYS"]["source"] == "ui" and settings.TRASH_RETENTION_DAYS == 9

    is_error, text = await _mcp(client, "update_server_settings", values={"PORT": 9000})
    assert is_error and "PORT" in text
    is_error, text = await _mcp(client, "update_server_settings", values="nope")
    assert is_error and "object" in text

    is_error, body = await _mcp(client, "get_server_readiness")
    assert not is_error and {c["id"] for c in body["items"]} >= {"relay", "public_url"}
