"""Structured API errors: code/details/retry-after parsing, subclasses, and CLI output."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from open_agent_email import (
    AgentEmailClient,
    AgentEmailError,
    IdempotencyKeyReuseError,
    RateLimitError,
)
from open_agent_email.cli import main

from .conftest import SECRET_KEY


class ErrorServer:
    """Answers every request with one configurable status, JSON body and header set."""

    def __init__(self):
        self.status, self.body, self.headers = 200, {}, {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self):
                data = outer.body if isinstance(outer.body, bytes) else json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in outer.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    def respond(self, status, body, **headers):
        self.status, self.body, self.headers = status, body, headers

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def errors():
    srv = ErrorServer()
    yield srv
    srv.close()


def fail(srv):
    client = AgentEmailClient(srv.url, SECRET_KEY)
    with pytest.raises(AgentEmailError) as caught:
        client.whoami()
    return caught.value


def test_structured_error_exposes_code_details_and_keeps_detail(errors):
    errors.respond(403, {
        "detail": "Token is not authorized for this agent inbox",
        "error": {"code": "forbidden_inbox", "message": "Token is not authorized for this agent inbox", "details": None},
    })
    err = fail(errors)
    assert (err.status_code, err.code) == (403, "forbidden_inbox")
    assert err.detail == "Token is not authorized for this agent inbox"
    assert err.details is None and err.retry_after_seconds is None
    assert not isinstance(err, RateLimitError)
    assert SECRET_KEY not in str(err)


def test_validation_errors_carry_field_list(errors):
    fields = [{"path": "body.email", "message": "Field required", "type": "missing"}]
    errors.respond(422, {
        "detail": [{"loc": ["body", "email"], "msg": "Field required", "type": "missing"}],
        "error": {"code": "validation_error", "message": "Request validation failed", "details": fields},
    })
    err = fail(errors)
    assert err.code == "validation_error" and err.details == fields
    assert isinstance(err.detail, list)


def test_rate_limit_error_prefers_body_over_header(errors):
    errors.respond(429, {
        "detail": "Rate limit exceeded; try again later.",
        "error": {"code": "rate_limited", "message": "x", "details": {"retry_after_seconds": 12}},
    }, **{"Retry-After": "30"})
    err = fail(errors)
    assert isinstance(err, RateLimitError) and err.retry_after_seconds == 12.0 and err.code == "rate_limited"


def test_rate_limit_from_older_server_uses_retry_after_header(errors):
    errors.respond(429, {"detail": "Rate limit exceeded; try again later."}, **{"Retry-After": "7"})
    err = fail(errors)
    assert isinstance(err, RateLimitError)
    assert err.retry_after_seconds == 7.0 and err.code is None
    assert err.detail == "Rate limit exceeded; try again later."


def test_garbage_retry_after_is_ignored(errors):
    errors.respond(429, {"detail": "slow down"}, **{"Retry-After": "soon"})
    assert fail(errors).retry_after_seconds is None


def test_idempotency_key_reuse_subclass(errors):
    errors.respond(409, {
        "detail": "Idempotency-Key was already used with a different request",
        "error": {"code": "idempotency_key_reuse", "message": "m", "details": None},
    })
    assert isinstance(fail(errors), IdempotencyKeyReuseError)


def test_detail_only_and_non_json_bodies_still_work(errors):
    errors.respond(404, {"detail": "Agent not found"})
    err = fail(errors)
    assert err.code is None and err.detail == "Agent not found" and err.status_code == 404
    errors.respond(502, b"Bad gateway")
    err = fail(errors)
    assert err.status_code == 502 and err.code is None


def test_error_is_still_an_exception_with_the_original_message(errors):
    errors.respond(401, {"detail": "Missing or invalid bearer token"})
    assert "HTTP 401" in str(fail(errors))


def test_cli_prints_code_detail_and_retry_after_but_never_the_key(errors, capsys):
    errors.respond(429, {
        "detail": "Rate limit exceeded; try again later.",
        "error": {"code": "rate_limited", "message": "m", "details": {"retry_after_seconds": 12}},
    }, **{"Retry-After": "12"})
    code = main(["whoami", "--base-url", errors.url, "--api-key", SECRET_KEY])
    out = capsys.readouterr()
    assert code == 1
    assert "[rate_limited]" in out.err and "Rate limit exceeded" in out.err and "retry after 12s" in out.err
    assert SECRET_KEY not in out.out + out.err


def test_cli_without_code_is_unchanged(errors, capsys):
    errors.respond(401, {"detail": "Missing or invalid bearer token"})
    code = main(["whoami", "--base-url", errors.url, "--api-key", SECRET_KEY])
    err = capsys.readouterr().err
    assert code == 1 and "HTTP 401" in err and "[" not in err.split("HTTP 401", 1)[1].split(":", 1)[0]
