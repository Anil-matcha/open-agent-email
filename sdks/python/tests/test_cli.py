import json
import os
import stat

import pytest

from open_agent_email.cli import config_path, main
from tests.conftest import SECRET_KEY

AG = "a@agents.local"
BASE = f"/v1/agents/{AG.replace('@', '%40')}"


def _path(server):
    # urlsplit keeps percent-encoding in .path, so the recorded path is the encoded form
    return server.last["path"]


# (argv, method, path-suffix after the agent prefix or absolute, expected query subset, expected body subset)
REQUEST_SHAPES = [
    (["whoami"], "GET", "/v1/whoami", {}, None),
    (["info"], "GET", "/v1/info", {}, None),
    (["agents", "list", "--include-deleted"], "GET", "/v1/agents", {"include_deleted": "true"}, None),
    (["agents", "create", "x@agents.local", "--name", "X", "--no-review"], "POST", "/v1/agents", {},
     {"email": "x@agents.local", "display_name": "X", "hitl_enabled": False}),
    (["agents", "get", AG], "GET", BASE, {}, None),
    (["messages", "list", "--agent", AG, "--label", "vip", "--trashed", "--limit", "5"], "GET", f"{BASE}/messages",
     {"label": "vip", "trashed": "true", "limit": "5"}, None),
    (["messages", "get", "--agent", AG, "m1"], "GET", f"{BASE}/messages/m1", {}, None),
    (["messages", "forward", "--agent", AG, "m1", "--to", "h@example.com", "--note", "fyi"], "POST",
     f"{BASE}/messages/m1/forward", {}, {"recipient": "h@example.com", "note": "fyi"}),
    (["messages", "label", "--agent", AG, "m1", "--set", "a, b,,a"], "PATCH", f"{BASE}/messages/m1", {},
     {"labels": ["a", "b", "a"]}),
    (["messages", "label", "--agent", AG, "m1"], "PATCH", f"{BASE}/messages/m1", {}, {"labels": []}),
    (["messages", "trash", "--agent", AG, "m1"], "DELETE", f"{BASE}/messages/m1", {}, None),
    (["messages", "restore", "--agent", AG, "m1"], "POST", f"{BASE}/messages/m1/restore", {}, None),
    (["conversations", "list", "--agent", AG, "--limit", "3"], "GET", f"{BASE}/conversations", {"limit": "3"}, None),
    (["conversations", "get", "--agent", AG, "th_1"], "GET", f"{BASE}/conversations/th_1", {}, None),
    (["reviews", "list", "--status", "pending", "--agent", AG], "GET", "/v1/reviews",
     {"status": "pending", "agent_email": AG}, None),
    (["reviews", "approve", "rev_1", "--notes", "ok"], "POST", "/v1/reviews/rev_1/approve", {},
     {"action": "approve", "notes": "ok"}),
    (["reviews", "reject", "rev_1"], "POST", "/v1/reviews/rev_1/reject", {}, {"action": "reject"}),
    (["webhooks", "list", "--agent", AG], "GET", "/v1/webhooks", {"agent_email": AG}, None),
    (["webhooks", "create", "--agent", AG, "--url", "https://h.example.com/x", "--events", "email.received,email.bounced"],
     "POST", "/v1/webhooks", {}, {"agent_email": AG, "url": "https://h.example.com/x",
                                  "events": ["email.received", "email.bounced"]}),
    (["webhooks", "test", "wh_1"], "POST", "/v1/webhooks/wh_1/test", {}, None),
    (["webhooks", "rotate", "wh_1"], "POST", "/v1/webhooks/wh_1/rotate-secret", {}, None),
    (["webhooks", "deliveries", "--endpoint", "wh_1", "--status", "failed"], "GET", "/v1/webhooks/deliveries/page",
     {"endpoint_id": "wh_1", "status": "failed"}, None),
    (["events", "list", "--agent", AG, "--type", "email.received", "--since", "2026-01-01T00:00:00Z"], "GET", "/v1/events",
     {"agent_email": AG, "event_type": "email.received", "since": "2026-01-01T00:00:00Z"}, None),
    (["events", "redeliver", "evt_1"], "POST", "/v1/events/evt_1/redeliver", {}, None),
    (["otp", "--agent", AG, "--wait", "5", "--since", "120"], "GET", f"{BASE}/messages/otp",
     {"wait_seconds": "5.0", "since_seconds": "120"}, None),
]


@pytest.mark.parametrize("argv,method,path,query,body", REQUEST_SHAPES, ids=[" ".join(r[0][:3]) for r in REQUEST_SHAPES])
def test_request_shape(server, oae, argv, method, path, query, body):
    if argv[0] == "otp":
        server.route("GET", path, {"found": True, "code": "123456"})
    code, out, err = oae(*argv)
    assert code == 0, err
    req = server.last
    assert (req["method"], req["path"]) == (method, path)
    assert query.items() <= req["query"].items()
    if body is None:
        assert req["body"] is None
    else:
        assert body.items() <= req["body"].items()
    assert req["headers"]["authorization"] == f"Bearer {SECRET_KEY}"


def test_send_direct_content_idempotency_and_scheduled_at(server, oae):
    code, _, err = oae("messages", "send", "--agent", AG, "--to", "p@example.com", "--subject", "Hi",
                       "--body", "Body", "--idempotency-key", "k-1", "--send-at", "2099-01-01T00:00:00Z")
    assert code == 0, err
    req = server.last
    assert req["path"] == f"{BASE}/messages"
    assert req["headers"]["idempotency-key"] == "k-1"
    assert req["body"] == {"recipient": "p@example.com", "subject": "Hi", "body_text": "Body",
                           "scheduled_at": "2099-01-01T00:00:00Z"}


def test_send_with_template_and_data(server, oae):
    code, _, err = oae("messages", "send", "--agent", AG, "--to", "p@example.com", "--template", "tpl_1",
                       "--data-json", '{"name": "Ada"}')
    assert code == 0, err
    assert server.last["body"] == {"recipient": "p@example.com", "template_id": "tpl_1",
                                   "template_data": {"name": "Ada"}}


def test_reply_reads_body_file(server, oae, tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("from a file", encoding="utf-8")
    code, _, err = oae("messages", "reply", "--agent", AG, "m1", "--body-file", str(body))
    assert code == 0, err
    assert server.last["path"] == f"{BASE}/messages/m1/reply"
    assert server.last["body"] == {"body_text": "from a file"}


@pytest.mark.parametrize("argv", [
    ["messages", "send", "--agent", AG, "--to", "p@example.com", "--subject", "s"],  # no body
    ["messages", "send", "--agent", AG, "--to", "p@example.com", "--template", "t", "--body", "x"],
    ["messages", "send", "--agent", AG, "--to", "p@example.com", "--subject", "s", "--body", "b",
     "--data-json", "{}"],
    ["messages", "send", "--agent", AG, "--to", "p@example.com", "--template", "t", "--data-json", "[1]"],
    ["messages", "send", "--agent", AG, "--to", "p@example.com", "--subject", "s", "--body", "b",
     "--send-at", "   "],
    ["messages", "reply", "--agent", AG, "m1"],
    ["listen", "--agent", AG],  # no handler
])
def test_usage_errors_exit_2_without_calling_server(server, oae, argv):
    code, _, err = oae(*argv)
    assert code == 2
    assert server.requests == []
    assert SECRET_KEY not in err


def test_argparse_errors_exit_2(oae):
    assert oae("nonsense")[0] == 2
    assert oae("messages", "list")[0] == 2  # --agent required
    assert oae("--help")[0] == 0


def test_api_errors_exit_1_and_show_detail(server, oae):
    server.route("GET", "/v1/whoami", {"detail": "Missing or invalid bearer token"}, status=401)
    code, out, err = oae("whoami")
    assert code == 1 and "HTTP 401" in err and "invalid bearer token" in err and out == ""


def test_transport_error_exits_1(oae, server):
    server.close()
    code, _, err = oae("whoami")
    assert code == 1 and "request failed" in err


def test_otp_not_found_exits_1(server, oae):
    server.route("GET", f"{BASE}/messages/otp", {"found": False})
    code, out, _ = oae("otp", "--agent", AG)
    assert code == 1 and "no code found" in out


def test_otp_prints_only_the_code(server, oae):
    server.route("GET", f"{BASE}/messages/otp", {"found": True, "code": "424242"})
    assert oae("otp", "--agent", AG) [:2] == (0, "424242\n")


# ---- configuration and secrecy --------------------------------------------------------------


def test_missing_config_exits_2(capsys):
    assert main(["whoami"]) == 2
    err = capsys.readouterr().err
    assert "oae login" in err


def test_precedence_flag_over_env_over_file(server, monkeypatch, capsys, isolated_env):
    config_path().write_text(json.dumps({"base_url": "http://127.0.0.1:1", "api_key": "from-file"}))
    server.route("GET", "/v1/whoami", {"kind": "admin"})
    # file only: points at a dead port -> transport error
    assert main(["whoami"]) == 1
    # env beats file
    monkeypatch.setenv("OAE_BASE_URL", server.url)
    monkeypatch.setenv("OAE_API_KEY", "from-env")
    assert main(["whoami"]) == 0
    assert server.last["headers"]["authorization"] == "Bearer from-env"
    # flag beats env
    assert main(["whoami", "--api-key", "from-flag"]) == 0
    assert server.last["headers"]["authorization"] == "Bearer from-flag"
    capsys.readouterr()


def test_login_saves_0600_after_verifying_and_never_echoes_key(server, capsys):
    server.route("GET", "/v1/whoami", {"kind": "agent_key", "scope": "full_access", "agent_email": AG})
    assert main(["login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out + captured.err
    path = config_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text()) == {"base_url": server.url, "api_key": SECRET_KEY}
    # stored credentials work with no flags
    assert main(["whoami"]) == 0
    assert server.last["headers"]["authorization"] == f"Bearer {SECRET_KEY}"
    assert main(["logout"]) == 0
    assert not path.exists()


def test_login_rejected_key_is_not_saved(server, capsys):
    server.route("GET", "/v1/whoami", {"detail": "nope"}, status=401)
    assert main(["login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 1
    assert not config_path().exists()
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out + captured.err


def test_login_tightens_permissions_of_an_existing_file(server, capsys):
    path = config_path()
    path.write_text("{}")
    os.chmod(path, 0o644)
    server.route("GET", "/v1/whoami", {"kind": "admin", "scope": "admin"})
    assert main(["login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.parametrize("argv", [
    ["whoami"], ["whoami", "--json"], ["messages", "list", "--agent", AG], ["agents", "list", "--json"],
    ["webhooks", "create", "--agent", AG, "--url", "https://h.example.com/x"],
])
def test_key_never_appears_in_output(server, oae, argv):
    server.route("GET", "/v1/whoami", {"kind": "admin"})
    server.route("POST", "/v1/webhooks", {"id": "wh_1", "signing_secret": "whsec_shown_once"})
    server.route("GET", f"{BASE}/messages", [])
    server.route("GET", "/v1/agents", [])
    code, out, err = oae(*argv)
    assert code == 0
    assert SECRET_KEY not in out + err


def test_key_never_in_error_output(server, oae):
    server.route("GET", "/v1/whoami", {"detail": "bad"}, status=403)
    _, out, err = oae("whoami", "--json")
    assert SECRET_KEY not in out + err


def test_bad_base_url_is_usage_error(capsys):
    assert main(["whoami", "--base-url", "http://example.com", "--api-key", SECRET_KEY]) == 2
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out + captured.err


# ---- output ---------------------------------------------------------------------------------


def test_json_flag_before_or_after_subcommand(server, oae):
    server.route("GET", "/v1/whoami", {"kind": "admin", "scope": "admin"})
    for argv in (["--json", "whoami"], ["whoami", "--json"]):
        code, out, _ = oae(*argv)
        assert code == 0 and json.loads(out) == {"kind": "admin", "scope": "admin"}


def test_table_output_and_empty_list(server, oae):
    server.route("GET", f"{BASE}/messages", [
        {"id": "m1", "direction": "inbound", "sender": "x@example.com", "recipient": AG,
         "subject": "Hello\nthere", "status": "delivered", "created_at": "2026-01-01T00:00:00"}])
    code, out, _ = oae("messages", "list", "--agent", AG)
    lines = out.strip().splitlines()
    assert code == 0 and lines[0].split()[:3] == ["ID", "DIRECTION", "SENDER"]
    assert "Hello there" in lines[1] and len(lines) == 2
    server.route("GET", f"{BASE}/messages", [])
    assert oae("messages", "list", "--agent", AG)[1].strip() == "(none)"


def test_webhook_create_prints_secret_once_notice(server, oae):
    server.route("POST", "/v1/webhooks", {"id": "wh_1", "agent_email": AG, "url": "https://h.example.com/x",
                                          "events": ["*"], "signing_secret": "whsec_abc"})
    _, out, _ = oae("webhooks", "create", "--agent", AG, "--url", "https://h.example.com/x")
    assert "whsec_abc" in out and "not shown again" in out
