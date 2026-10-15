import asyncio
import json
import sys

import pytest

from open_agent_email import AgentEmailClient
from open_agent_email.cli import (
    FatalListenError,
    dispatch_email,
    forward_json,
    listen_loop,
    main,
    run_command,
    validate_forward_url,
    ws_url,
    UsageError,
)
from tests.conftest import MockServer, SECRET_KEY

AG = "a@agents.local"
MSG_PATH = "/v1/agents/a%40agents.local/messages/m1"
FULL = {"id": "m1", "sender": "x@example.com", "subject": "Hello", "body_text": "Body"}


@pytest.fixture
def client(server):
    server.route("GET", MSG_PATH, FULL)
    return AgentEmailClient(server.url, SECRET_KEY)


@pytest.fixture
def handler():
    srv = MockServer()
    yield srv
    srv.close()


def test_ws_url_scheme_and_quoting():
    assert ws_url("http://127.0.0.1:8000", AG) == "ws://127.0.0.1:8000/v1/agents/a%40agents.local/ws"
    assert ws_url("https://mail.example.com/", AG).startswith("wss://mail.example.com/v1/agents/")


@pytest.mark.parametrize("url", ["http://example.com/h", "ftp://localhost/h", "http://u:p@localhost/h", "localhost/h", ""])
def test_forward_url_rejected(url):
    with pytest.raises(UsageError):
        validate_forward_url(url)


@pytest.mark.parametrize("url", ["http://localhost:3000/hook", "http://127.0.0.1/h", "https://example.com/h"])
def test_forward_url_accepted(url):
    assert validate_forward_url(url) == url


def test_dispatch_forwards_full_message_as_json(client, server, handler):
    result = dispatch_email(client, AG, {"id": "m1"}, forward_to=handler.url + "/hook")
    assert result == {"message_id": "m1", "subject": "Hello", "forward_status": 200}
    sent = handler.last
    assert (sent["method"], sent["path"]) == ("POST", "/hook")
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["body"] == {"event": "email.received", "agent_email": AG, "message": FULL}
    # the handler never receives the API key
    assert SECRET_KEY not in json.dumps(sent["headers"]) + json.dumps(sent["body"])


def test_forward_does_not_follow_redirects(handler):
    handler.route("POST", "/hook", None, status=302)
    assert forward_json(handler.url + "/hook", {"x": 1}) == 302
    assert len(handler.requests) == 1


def test_forward_unreachable_handler_raises_runtime_error():
    with pytest.raises(RuntimeError, match="unreachable"):
        forward_json("http://127.0.0.1:1/hook", {}, timeout=1)


def test_dispatch_command_gets_json_on_stdin(client, tmp_path):
    out = tmp_path / "stdin.json"
    script = f"import sys,pathlib;pathlib.Path({str(out)!r}).write_bytes(sys.stdin.buffer.read())"
    result = dispatch_email(client, AG, {"id": "m1"}, command=[sys.executable, "-c", script])
    assert result["command_exit"] == 0
    assert json.loads(out.read_text())["message"] == FULL


def test_command_is_never_run_through_a_shell(tmp_path):
    marker = tmp_path / "pwned"
    # Shell metacharacters stay inert arguments because argv is passed directly.
    status = run_command([sys.executable, "-c", "import sys", f"; touch {marker}", f"$(touch {marker})"], {})
    assert status == 0 and not marker.exists()


def test_command_timeout_and_missing_binary():
    with pytest.raises(RuntimeError, match="timed out"):
        run_command([sys.executable, "-c", "import time;time.sleep(5)"], {}, timeout=0.3)
    with pytest.raises(RuntimeError, match="failed to start"):
        run_command(["/nonexistent/handler-binary"], {})


def test_dispatch_requires_message_id(client):
    with pytest.raises(RuntimeError, match="no message id"):
        dispatch_email(client, AG, {}, forward_to="http://127.0.0.1:1/x")


# ---- the reconnecting listen loop with a scripted fake socket ------------------------------


class FakeSocket:
    def __init__(self, frames, then=None):
        self.frames, self.then = frames, then

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for frame in self.frames:
            yield frame if isinstance(frame, str) else json.dumps(frame)
        if self.then:
            raise self.then


def scripted_connect(steps, calls):
    """Each step is a FakeSocket or an exception raised while connecting."""
    steps = list(steps)

    def connect(url, headers):
        calls.append((url, headers))
        step = steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    return connect


def run_loop(client, server, connect, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    lines, logs = [], []
    code = asyncio.run(listen_loop(
        client, server.url, SECRET_KEY, AG, connect=connect, sleep=fake_sleep,
        log=logs.append, emit_line=lines.append, **kwargs))
    return code, sleeps, lines, logs


def test_listen_dispatches_only_email_received(client, server, handler):
    frames = [
        {"type": "email.sent", "data": {"id": "zzz"}},
        "not json",
        {"type": "email.received", "data": {"id": "m1"}},
    ]
    calls = []
    code, _, lines, _ = run_loop(client, server, scripted_connect([FakeSocket(frames)], calls),
                                 forward_to=handler.url + "/hook", max_events=1)
    assert code == 0
    assert [json.loads(line)["message_id"] for line in lines] == ["m1"]
    assert len(handler.requests) == 1
    url, headers = calls[0]
    assert url == ws_url(server.url, AG) and headers == {"Authorization": f"Bearer {SECRET_KEY}"}
    assert "api_key" not in url  # the key travels in a header, never in the URL


def test_listen_reconnects_with_growing_backoff(client, server, handler):
    calls = []
    steps = [
        ConnectionRefusedError(),
        OSError("boom"),
        FakeSocket([{"type": "email.received", "data": {"id": "m1"}}]),
    ]
    code, sleeps, lines, logs = run_loop(client, server, scripted_connect(steps, calls),
                                         forward_to=handler.url + "/hook", max_events=1)
    assert code == 0 and len(calls) == 3 and len(lines) == 1
    assert len(sleeps) == 2 and 1.0 <= sleeps[0] < 1.3 and 2.0 <= sleeps[1] < 2.6
    assert any("retrying" in line for line in logs)


def test_listen_backoff_is_capped(client, server):
    steps = [ConnectionError()] * 9 + [FatalCode(1008)]
    with pytest.raises(FatalListenError):
        run_loop(client, server, scripted_connect(steps, []), forward_to="http://127.0.0.1:1/x")


class FatalCode(Exception):
    def __init__(self, code):
        self.code = code


def test_listen_stops_on_policy_close_code(client, server):
    calls = []
    with pytest.raises(FatalListenError):
        run_loop(client, server, scripted_connect([FatalCode(1008), FakeSocket([])], calls),
                 forward_to="http://127.0.0.1:1/x")
    assert len(calls) == 1  # no retry after a rejected credential


def test_listen_stops_on_replaced_connection(client, server):
    calls = []
    replaced = FatalCode(4000)
    replaced.reason = "replaced"
    with pytest.raises(FatalListenError, match="replaced"):
        run_loop(client, server, scripted_connect([replaced, FakeSocket([])], calls),
                 forward_to="http://127.0.0.1:1/x")
    assert len(calls) == 1


def test_listen_stops_on_unknown_application_close_code(client, server):
    calls = []
    with pytest.raises(FatalListenError):
        run_loop(client, server, scripted_connect([FatalCode(4001), FakeSocket([])], calls),
                 forward_to="http://127.0.0.1:1/x")
    assert len(calls) == 1


def test_listen_survives_handler_failure_and_continues(client, server):
    frames = [{"type": "email.received", "data": {"id": "m1"}}, {"type": "email.received", "data": {"id": "m1"}}]
    code, _, lines, logs = run_loop(client, server, scripted_connect([FakeSocket(frames)], []),
                                    forward_to="http://127.0.0.1:1/unreachable", max_events=2)
    assert code == 0 and lines == []
    assert sum("handler error" in line for line in logs) == 2


def test_listen_reconnects_after_a_dropped_stream(client, server, handler):
    steps = [
        FakeSocket([{"type": "email.received", "data": {"id": "m1"}}], then=ConnectionResetError()),
        FakeSocket([{"type": "email.received", "data": {"id": "m1"}}]),
    ]
    code, sleeps, lines, _ = run_loop(client, server, scripted_connect(steps, []),
                                      forward_to=handler.url + "/hook", max_events=2)
    assert code == 0 and len(lines) == 2 and len(sleeps) == 1


def test_listen_command_wires_the_pieces(server, handler, monkeypatch, capsys):
    server.route("GET", MSG_PATH, FULL)
    import open_agent_email.cli as cli

    steps = [FakeSocket([{"type": "email.received", "data": {"id": "m1"}}])]
    monkeypatch.setattr(cli, "_default_connect", lambda: scripted_connect(steps, []))
    code = main(["listen", "--agent", AG, "--forward-to", handler.url + "/hook", "--max-events", "1",
                 "--base-url", server.url, "--api-key", SECRET_KEY])
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out)["forward_status"] == 200
    assert SECRET_KEY not in captured.out + captured.err


def test_listen_fatal_exits_1(server, monkeypatch, capsys):
    import open_agent_email.cli as cli

    monkeypatch.setattr(cli, "_default_connect", lambda: scripted_connect([FatalCode(1008)], []))
    code = main(["listen", "--agent", AG, "--command", "true", "--base-url", server.url, "--api-key", SECRET_KEY])
    assert code == 1 and "rejected" in capsys.readouterr().err


def test_listen_rejects_remote_http_handler(server, capsys):
    code = main(["listen", "--agent", AG, "--forward-to", "http://example.com/h",
                 "--base-url", server.url, "--api-key", SECRET_KEY])
    assert code == 2
