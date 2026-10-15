import json
import os
import subprocess

import pytest

from open_agent_email import AgentEmailClient, AgentEmailError, cli
from open_agent_email.cli import config_path, main
from tests.conftest import SECRET_KEY

AG = "a@agents.local"
BASE = f"/v1/agents/{AG.replace('@', '%40')}"


@pytest.fixture
def client(server):
    return AgentEmailClient(server.url, SECRET_KEY)


def test_messages_send_maps_short_names_to_canonical_body(server, client):
    server.route("POST", f"{BASE}/messages", {"id": "m1"})
    client.inboxes.messages.send(AG, to="p@example.com", subject="Hi", text="plain", html="<p>x</p>")
    assert server.last["body"] == {"recipient": "p@example.com", "subject": "Hi", "body_text": "plain", "body_html": "<p>x</p>"}


def test_messages_list_returns_a_page_and_passes_page_token_as_cursor(server, client):
    server.route("GET", f"{BASE}/messages/page", {"items": [], "next_cursor": None})
    page = client.inboxes.messages.list(AG, limit=5, page_token="tok", read_status="unread")
    assert page == {"items": [], "next_cursor": None}
    assert server.last["query"] == {"limit": "5", "read_status": "unread", "cursor": "tok"}


def test_reply_and_forward_use_short_names_and_reject_reply_all(server, client):
    server.route("POST", f"{BASE}/messages/m1/reply", {"id": "m2"})
    client.inboxes.messages.reply(AG, "m1", text="answer")
    assert server.last["body"] == {"body_text": "answer"}
    with pytest.raises(ValueError, match="reply_all"):
        client.inboxes.messages.reply(AG, "m1", text="x", reply_all=True)
    server.route("POST", f"{BASE}/messages/m1/forward", {"id": "m3"})
    client.inboxes.messages.forward(AG, "m1", to="lead@example.com", note="fyi")
    assert server.last["body"] == {"recipient": "lead@example.com", "note": "fyi"}


def test_update_supports_add_remove_and_replace(server, client):
    server.route("PATCH", f"{BASE}/messages/m1", {"id": "m1"})
    client.inboxes.messages.update(AG, "m1", add_labels=["a"], remove_labels=["b"])
    assert server.last["body"] == {"add_labels": ["a"], "remove_labels": ["b"]}
    client.inboxes.messages.update(AG, "m1", labels=["x"])
    assert server.last["body"] == {"labels": ["x"]}
    with pytest.raises(ValueError):
        client.inboxes.messages.update(AG, "m1")
    with pytest.raises(ValueError):
        client.inboxes.messages.update(AG, "m1", labels=["x"], add_labels=["y"])


def test_flat_methods_still_work(server, client):
    server.route("POST", f"{BASE}/messages", {"id": "m1"})
    client.send_message(AG, recipient="p@example.com", subject="Hi", body_text="x")
    assert server.last["body"]["recipient"] == "p@example.com"


def test_threads_inboxes_webhooks_and_api_keys(server, client):
    server.route("GET", f"{BASE}/conversations/page", {"items": [], "next_cursor": None})
    client.inboxes.threads.list(AG, limit=3, page_token="t")
    assert server.last["query"] == {"limit": "3", "cursor": "t"}
    server.route("POST", "/v1/agents", {"email": AG})
    client.inboxes.create(AG)
    assert server.last["body"] == {"email": AG, "threat_sensitivity": "medium", "hitl_enabled": True}
    server.route("POST", "/v1/webhooks", {"id": "w1"})
    client.webhooks.create(AG, url="https://h.example.com/x", event_types=["message.received"])
    assert server.last["body"]["events"] == ["message.received"] and server.last["body"]["agent_email"] == AG
    server.route("POST", "/v1/account/api-keys", {"id": "k1"})
    client.api_keys.create("k", inbox=AG)
    assert server.last["body"] == {"name": "k", "scope": "agent", "agent_email": AG}
    client.api_keys.create("acct")
    assert server.last["body"] == {"name": "acct", "scope": "account"}


def test_error_exposes_fix_and_docs_when_server_sends_them(server, client):
    server.route("GET", "/v1/whoami", {"detail": "x", "error": {"code": "forbidden", "message": "m", "fix": "Use a full-access key", "docs": "https://docs.example.com/e"}}, status=403)
    with pytest.raises(AgentEmailError) as caught:
        client.whoami()
    assert caught.value.fix == "Use a full-access key" and caught.value.docs == "https://docs.example.com/e"
    server.route("GET", "/v1/info", {"detail": "x"}, status=500)
    with pytest.raises(AgentEmailError) as plain:
        client.info()
    assert plain.value.fix is None and plain.value.docs is None


# ---- dry run --------------------------------------------------------------------------


def test_dry_run_prints_request_and_sends_nothing(server, capsys):
    code = main(["messages", "send", "--agent", AG, "--to", "p@example.com", "--subject", "Hi", "--body", "Yo",
                 "--dry-run", "--base-url", server.url])
    out = capsys.readouterr().out
    assert code == 0 and server.requests == []
    printed = json.loads(out)
    assert printed["dry_run"] is True and printed["method"] == "POST"
    assert printed["body"] == {"recipient": "p@example.com", "subject": "Hi", "body_text": "Yo"}
    assert SECRET_KEY not in out and "dry-run-no-key" not in out


# ---- keychain with a fake runner -------------------------------------------------------


class FakeSecurity:
    def __init__(self, fail=False):
        self.store: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, argv, input=None, **kwargs):
        self.calls.append(list(argv))
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "denied")
        if argv[:2] == ["security", "-i"]:
            parts = input.split()
            account = input.split('-a "')[1].split('"')[0]
            secret = input.split('-w "')[1].split('"')[0]
            self.store[account] = secret
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "find-generic-password":
            account = argv[argv.index("-a") + 1]
            if account in self.store:
                return subprocess.CompletedProcess(argv, 0, self.store[account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if argv[1] == "delete-generic-password":
            account = argv[argv.index("-a") + 1]
            return subprocess.CompletedProcess(argv, 0 if self.store.pop(account, None) is not None else 44, "", "")
        raise AssertionError(argv)


@pytest.fixture
def fake_keychain(monkeypatch):
    fake = FakeSecurity()
    monkeypatch.setattr(cli, "keychain_available", lambda: True)
    monkeypatch.setattr(cli, "_run", fake)
    return fake


def test_login_prefers_keychain_and_keeps_key_out_of_the_file_and_argv(server, capsys, fake_keychain):
    server.route("GET", "/v1/whoami", {"kind": "agent_key", "scope": "full_access", "agent_email": AG})
    assert main(["login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out + captured.err
    assert json.loads(config_path().read_text()) == {"base_url": server.url, "storage": "keychain"}
    assert fake_keychain.store == {server.url: SECRET_KEY}
    assert all(SECRET_KEY not in " ".join(call) for call in fake_keychain.calls)  # secret only travels on stdin
    assert main(["whoami"]) == 0
    assert server.last["headers"]["authorization"] == f"Bearer {SECRET_KEY}"
    assert main(["logout"]) == 0
    assert fake_keychain.store == {} and not config_path().exists()


def test_login_falls_back_to_file_when_keychain_refuses(server, capsys, monkeypatch):
    monkeypatch.setattr(cli, "keychain_available", lambda: True)
    monkeypatch.setattr(cli, "_run", FakeSecurity(fail=True))
    server.route("GET", "/v1/whoami", {"kind": "admin", "scope": "admin"})
    assert main(["login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    assert json.loads(config_path().read_text()) == {"base_url": server.url, "api_key": SECRET_KEY}
    assert oct(os.stat(config_path()).st_mode & 0o777) == "0o600"


def test_login_file_flag_skips_the_keychain(server, fake_keychain):
    server.route("GET", "/v1/whoami", {"kind": "admin", "scope": "admin"})
    assert main(["login", "--file", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    assert fake_keychain.calls == [] and json.loads(config_path().read_text())["api_key"] == SECRET_KEY


def test_unsafe_characters_are_not_sent_to_the_keychain(fake_keychain):
    assert cli.keychain_set("http://127.0.0.1:1", 'bad"key') is False
    assert cli.keychain_set("http://127.0.0.1:1", "has space") is False
    assert fake_keychain.calls == []


def test_auth_status_reports_source_without_the_key(server, capsys, fake_keychain):
    server.route("GET", "/v1/whoami", {"kind": "agent_key", "scope": "read_only", "agent_email": AG})
    assert main(["auth", "status", "--base-url", server.url]) == 1  # nothing stored yet
    capsys.readouterr()
    assert main(["auth", "login", "--base-url", server.url, "--api-key", SECRET_KEY]) == 0
    capsys.readouterr()
    assert main(["auth", "status", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["credential_source"] == "keychain" and data["authenticated"] is True and data["scope"] == "read_only"
    assert SECRET_KEY not in out
