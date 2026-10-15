import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SECRET_KEY = "oae_test_SECRETKEYVALUE123"


class MockServer:
    """Records every request and answers from a (method, path) -> (status, body) table."""

    def __init__(self):
        self.requests: list[dict] = []
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", "replace")
                outer.requests.append({
                    "method": self.command, "path": parts.path,
                    "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                    "body": body, "headers": {k.lower(): v for k, v in self.headers.items()},
                })
                status, payload = outer.routes.get((self.command, parts.path), (200, {}))
                data = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if status in (301, 302):
                    self.send_header("Location", "http://127.0.0.1:1/should-not-be-followed")
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    def route(self, method, path, body=None, status=200):
        self.routes[(method, path)] = (status, body)

    @property
    def last(self):
        return self.requests[-1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = MockServer()
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for name in ("OAE_BASE_URL", "OAE_API_KEY", "OAE_CONFIG", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OAE_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


@pytest.fixture
def oae(server, capsys):
    """Run the CLI against the mock server; returns (exit_code, stdout, stderr)."""
    from open_agent_email.cli import main

    def run(*argv, auth=True):
        extra = ["--base-url", server.url, "--api-key", SECRET_KEY] if auth else []
        code = main([*argv, *extra])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch):
    """Tests never touch the real OS keychain; keychain tests install their own fake runner."""
    from open_agent_email import cli

    monkeypatch.setattr(cli, "keychain_available", lambda: False)
