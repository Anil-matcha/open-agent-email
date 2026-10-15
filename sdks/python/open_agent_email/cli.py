"""`oae` command-line interface for Open Agent Email.

Exit codes: 0 success, 1 API/transport error, 2 usage or configuration error.
The API key is never printed, logged or included in error output.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .client import AgentEmailClient, AgentEmailError, _format_send_at

EXIT_OK, EXIT_API, EXIT_USAGE = 0, 1, 2
FORWARD_TIMEOUT_SECONDS = 10
COMMAND_TIMEOUT_SECONDS = 30
MAX_BACKOFF_SECONDS = 30.0


class UsageError(Exception):
    """Invalid arguments or configuration; reported with exit code 2."""


# ---- configuration ---------------------------------------------------------------------


def config_path() -> Path:
    override = os.environ.get("OAE_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "oae" / "config.json"


def load_config() -> dict[str, str]:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in {"base_url", "api_key", "storage"} and isinstance(v, str)} if isinstance(data, dict) else {}


def save_config(base_url: str, api_key: str) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start so the key is never world-readable, even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump({"base_url": base_url, "api_key": api_key}, handle)
    return path


# ---- OS keychain (macOS) ---------------------------------------------------------------

KEYCHAIN_SERVICE = "oae"
_run = subprocess.run  # replaced in tests with a fake runner


def keychain_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def _security_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def keychain_set(base_url: str, api_key: str) -> bool:
    """Store the key in the login keychain. The secret goes over stdin, never on a command line."""
    if not keychain_available():
        return False
    if any(ch in value for value in (base_url, api_key) for ch in '"\\\n\r \t'):
        return False  # unusual characters cannot be quoted safely; the caller falls back to the file
    command = (f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {_security_quote(base_url)} "
               f"-w {_security_quote(api_key)}\n")
    try:
        result = _run(["security", "-i"], input=command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "error" not in (result.stderr or "").lower()


def keychain_get(base_url: str) -> str | None:
    if not keychain_available():
        return None
    try:
        result = _run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", base_url, "-w"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def keychain_delete(base_url: str) -> bool:
    if not keychain_available():
        return False
    try:
        result = _run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", base_url],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def save_keychain_pointer(base_url: str) -> Path:
    """Write the config file without the key; it records only that the key lives in the keychain."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump({"base_url": base_url, "storage": "keychain"}, handle)
    return path


def credential_source(args: argparse.Namespace) -> tuple[str | None, str | None, str]:
    """Return (base_url, api_key, source) with source in flag, env, keychain, file or none."""
    stored = load_config()
    base_url = getattr(args, "base_url", None) or os.environ.get("OAE_BASE_URL") or stored.get("base_url")
    flag_key = getattr(args, "api_key", None)
    if flag_key:
        return base_url, flag_key, "flag"
    if os.environ.get("OAE_API_KEY"):
        return base_url, os.environ["OAE_API_KEY"], "env"
    if base_url and stored.get("storage") == "keychain":
        key = keychain_get(base_url)
        if key:
            return base_url, key, "keychain"
    if stored.get("api_key"):
        return base_url, stored["api_key"], "file"
    return base_url, None, "none"


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Precedence: command-line flag, then environment, then keychain or config file."""
    base_url, api_key, _ = credential_source(args)
    if not base_url:
        raise UsageError("No server URL: pass --base-url, set OAE_BASE_URL, or run `oae login`")
    if not api_key:
        raise UsageError("No API key: pass --api-key, set OAE_API_KEY, or run `oae login`")
    return base_url, api_key


def make_client(args: argparse.Namespace) -> AgentEmailClient:
    base_url, api_key = resolve_credentials(args)
    try:
        return AgentEmailClient(base_url, api_key, timeout=getattr(args, "timeout", None) or 15.0)
    except ValueError as exc:
        # Client validation messages never contain the key.
        raise UsageError(str(exc)) from None


# ---- output ----------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ",".join(_cell(v) for v in value) or "-"
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return " ".join(str(value).split())


def render_table(rows: Sequence[dict[str, Any]], columns: Sequence[str], *, width: int = 48) -> str:
    if not rows:
        return "(none)"
    cells = [[_cell(row.get(col)) for col in columns] for row in rows]
    cells = [[c if len(c) <= width else c[: width - 1] + "…" for c in line] for line in cells]
    widths = [max(len(col), *(len(line[i]) for line in cells)) for i, col in enumerate(columns)]
    header = "  ".join(col.upper().ljust(widths[i]) for i, col in enumerate(columns))
    body = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(line)).rstrip() for line in cells]
    return "\n".join([header.rstrip(), *body])


def render_record(record: dict[str, Any], keys: Sequence[str] | None = None) -> str:
    keys = keys or [k for k in record if not isinstance(record[k], (dict, list)) or k in {"labels", "participants"}]
    width = max((len(k) for k in keys), default=0)
    return "\n".join(f"{k.ljust(width)}  {_cell(record.get(k))}" for k in keys)


def emit(args: argparse.Namespace, data: Any, human: Callable[[Any], str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
    elif data is not None:
        print(human(data))


# ---- command handlers ------------------------------------------------------------------

MESSAGE_COLS = ["id", "direction", "sender", "recipient", "subject", "status", "created_at"]


def _json_arg(value: str | None, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise UsageError(f"{name} must be valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise UsageError(f"{name} must be a JSON object")
    return parsed


def _body(args: argparse.Namespace) -> str | None:
    if args.body is not None and args.body_file is not None:
        raise UsageError("use --body or --body-file, not both")
    if args.body_file is not None:
        try:
            return sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read --body-file: {exc.strerror}") from None
    return args.body


def cmd_login(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.environ.get("OAE_BASE_URL") or input("Server URL: ").strip()
    api_key = args.api_key or os.environ.get("OAE_API_KEY") or getpass.getpass("API key: ").strip()
    try:
        client = AgentEmailClient(base_url, api_key)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    who = client.whoami()  # verify before persisting anything
    storage = "file"
    if not getattr(args, "file", False) and keychain_set(base_url, api_key):
        path, storage = save_keychain_pointer(base_url), "keychain"
    else:
        path = save_config(base_url, api_key)
    emit(args, {"saved": str(path), "storage": storage, "kind": who.get("kind"), "scope": who.get("scope"),
                "agent_email": who.get("agent_email")},
         lambda d: (f"Saved credentials ({d['kind']}, scope {d['scope']}): key in the macOS keychain, server URL in {d['saved']}"
                    if d["storage"] == "keychain" else f"Saved credentials to {d['saved']} ({d['kind']}, scope {d['scope']})"))
    return EXIT_OK


def cmd_logout(args: argparse.Namespace) -> int:
    path = config_path()
    stored = load_config()
    removed_keychain = bool(stored.get("base_url")) and stored.get("storage") == "keychain" and keychain_delete(stored["base_url"])
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    emit(args, {"removed": removed or removed_keychain},
         lambda d: "Removed saved credentials" if d["removed"] else "No saved credentials")
    return EXIT_OK


def cmd_auth(args: argparse.Namespace) -> int:
    if args.action == "login":
        return cmd_login(args)
    if args.action == "logout":
        return cmd_logout(args)
    base_url, api_key, source = credential_source(args)
    data: dict[str, Any] = {"base_url": base_url, "credential_source": source, "authenticated": False}
    if base_url and api_key:
        try:
            who = AgentEmailClient(base_url, api_key, timeout=getattr(args, "timeout", None) or 15.0).whoami()
            data.update({"authenticated": True, "kind": who.get("kind"), "scope": who.get("scope"),
                         "agent_email": who.get("agent_email")})
        except ValueError as exc:
            data["error"] = str(exc)
        except AgentEmailError as exc:
            data["error"] = f"server returned HTTP {exc.status_code}"
    emit(args, data, render_record)
    return EXIT_OK if data["authenticated"] else EXIT_API


def cmd_whoami(args: argparse.Namespace) -> int:
    data = make_client(args).whoami()
    emit(args, data, render_record)
    return EXIT_OK


def cmd_info(args: argparse.Namespace) -> int:
    # Discovery is public, but the client always sends a bearer token, so a key is still required.
    data = make_client(args).info()
    emit(args, data, render_record)
    return EXIT_OK


def cmd_agents(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.action == "list":
        data = client.list_agents(include_deleted=args.include_deleted)
        emit(args, data, lambda d: render_table(d, ["email", "display_name", "status", "hitl_enabled", "threat_sensitivity"]))
    elif args.action == "create":
        data = client.create_agent(
            args.email, args.name, description=args.description,
            threat_sensitivity=args.threat_sensitivity, hitl_enabled=not args.no_review,
        )
        emit(args, data, render_record)
    else:
        data = client.get_agent(args.email)
        emit(args, data, render_record)
    return EXIT_OK


def _dry_run_client(args: argparse.Namespace) -> tuple[AgentEmailClient, list[dict[str, Any]]]:
    """A client whose requests are recorded instead of sent; needs no server or real key."""
    base_url = getattr(args, "base_url", None) or os.environ.get("OAE_BASE_URL") or load_config().get("base_url") or "http://127.0.0.1:8000"
    try:
        client = AgentEmailClient(base_url, "dry-run-no-key")
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    recorded: list[dict[str, Any]] = []

    def record(method: str, path: str, *, payload: Any = None, query: Any = None, idempotency_key: str | None = None, **_: Any) -> dict[str, Any]:
        recorded.append({"method": method, "url": base_url + path, "query": query, "idempotency_key": idempotency_key, "body": payload})
        return {}

    client._request = record  # type: ignore[method-assign]
    return client, recorded


def cmd_messages(args: argparse.Namespace) -> int:
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        client, recorded = _dry_run_client(args)
        rc = _messages(args, client, dry_run=True)
        for request in recorded:
            print(json.dumps({"dry_run": True, **request}, indent=2, default=str))
        return rc
    return _messages(args, make_client(args), dry_run=False)


def _messages(args: argparse.Namespace, client: AgentEmailClient, *, dry_run: bool) -> int:
    action, agent = args.action, args.agent
    if action == "list":
        data = client.list_messages(
            agent, direction=args.direction, status=args.status, limit=args.limit,
            conversation_id=args.conversation_id, label=args.label, trashed=args.trashed,
        )
        emit(args, data, lambda d: render_table(d, MESSAGE_COLS))
    elif action == "get":
        data = client.get_message(agent, args.id)
        emit(args, data, lambda d: render_record(d, [*MESSAGE_COLS, "thread_id", "conversation_id", "labels", "spf_status",
                                                      "dkim_status", "dmarc_status"]) + "\n\n" + d.get("body_text", ""))
    elif action in {"send", "reply"}:
        body, data_json = _body(args), _json_arg(args.data_json, "--data-json")
        common: dict[str, Any] = {"idempotency_key": args.idempotency_key, "scheduled_at": args.scheduled_at,
                                  "conversation_id": args.conversation_id}
        if args.template:
            content: dict[str, Any] = {"template_id": args.template, "template_data": data_json or {}}
            if body is not None or getattr(args, "subject", None):
                raise UsageError("--template cannot be combined with --body/--body-file/--subject")
        else:
            if data_json is not None:
                raise UsageError("--data-json requires --template")
            content = {"body_text": body}
        if action == "send":
            if args.template:
                data = client.send_message(agent, recipient=args.to, **content, **common)
            else:
                if args.subject is None or body is None:
                    raise UsageError("send needs --subject and --body/--body-file (or --template)")
                data = client.send_message(agent, recipient=args.to, subject=args.subject, **content, **common)
        else:
            if not args.template and body is None:
                raise UsageError("reply needs --body/--body-file (or --template)")
            data = client.reply_message(agent, args.id, **content, **common)
        if not dry_run:
            emit(args, data, lambda d: render_record(d, ["id", "status", "recipient", "subject", "thread_id", "conversation_id", "scheduled_at"]))
    elif action == "forward":
        data = client.forward_message(agent, args.id, recipient=args.to, note=args.note)
        emit(args, data, lambda d: render_record(d, ["id", "status", "recipient", "subject"]))
    elif action == "label":
        labels = [item.strip() for item in args.set.split(",") if item.strip()] if args.set else []
        data = client.update_message_labels(agent, args.id, labels)
        emit(args, data, lambda d: render_record(d, ["id", "labels"]))
    elif action == "trash":
        data = client.trash_message(agent, args.id)
        emit(args, data, lambda d: render_record(d, ["id", "status", "deleted_at"]))
    else:  # restore
        data = client.restore_message(agent, args.id)
        emit(args, data, lambda d: render_record(d, ["id", "status", "deleted_at"]))
    return EXIT_OK


def cmd_conversations(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.action == "list":
        data = client.list_conversations(args.agent, limit=args.limit)
        emit(args, data, lambda d: render_table(
            d.get("items", []), ["id", "subject", "message_count", "unread_count", "last_message_at"]))
    else:
        data = client.get_conversation(args.agent, args.id)
        emit(args, data, lambda d: render_record(d, ["id", "subject", "message_count", "unread_count", "participants"])
             + "\n\n" + render_table(d.get("messages", []), MESSAGE_COLS))
    return EXIT_OK


def cmd_otp(args: argparse.Namespace) -> int:
    data = make_client(args).get_recent_otp(args.agent, since_seconds=args.since, wait_seconds=args.wait)
    emit(args, data, lambda d: d["code"] if d.get("found") else "no code found")
    return EXIT_OK if data.get("found") else EXIT_API


def cmd_reviews(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.action == "list":
        data = client.list_reviews(status=args.status, agent_email=args.agent)
        emit(args, data, lambda d: render_table(d, ["id", "agent_email", "message_id", "status", "reason", "created_at"]))
    else:
        call = client.approve_review if args.action == "approve" else client.reject_review
        data = call(args.id, notes=args.notes)
        emit(args, data, lambda d: render_record(d, ["id", "status", "reviewed_by", "resolved_at"]))
    return EXIT_OK


def cmd_webhooks(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.action == "list":
        data = client.list_webhooks(agent_email=args.agent)
        emit(args, data, lambda d: render_table(d, ["id", "agent_email", "url", "enabled", "events"]))
    elif args.action == "create":
        events = [e.strip() for e in args.events.split(",") if e.strip()] if args.events else None
        data = client.create_webhook(args.agent, args.url, events=events)
        emit(args, data, lambda d: render_record(d, ["id", "agent_email", "url", "events", "signing_secret"])
             + "\n\nStore the signing secret now; it is not shown again.")
    elif args.action == "test":
        data = client.test_webhook(args.id)
        emit(args, data, render_record)
    elif args.action == "rotate":
        data = client.rotate_webhook_secret(args.id)
        emit(args, data, lambda d: render_record(d, ["id", "signing_secret"])
             + "\n\nStore the new signing secret now; it is not shown again.")
    else:
        data = client.list_webhook_deliveries(endpoint_id=args.endpoint, status=args.status, limit=args.limit)
        emit(args, data, lambda d: render_table(
            d.get("items", []), ["id", "endpoint_id", "event_type", "status", "attempts", "last_error"]))
    return EXIT_OK


def cmd_events(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.action == "list":
        data = client.list_events(agent_email=args.agent, event_type=args.type, status=args.status,
                                  since=args.since, until=args.until, limit=args.limit)
        emit(args, data, lambda d: render_table(
            d.get("items", []), ["id", "agent_email", "event_type", "status", "attempts", "created_at"]))
    else:
        data = client.redeliver_event(args.id)
        emit(args, data, render_record)
    return EXIT_OK


# ---- listen ----------------------------------------------------------------------------


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def validate_forward_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise UsageError("--forward-to must be an absolute http(s) URL without credentials")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise UsageError("--forward-to over HTTP is allowed only for localhost; use HTTPS otherwise")
    return url


def forward_json(url: str, payload: dict[str, Any], *, timeout: float = FORWARD_TIMEOUT_SECONDS) -> int:
    """POST JSON to a handler; no shell, no redirects, bounded time. Returns the HTTP status."""
    request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": "oae-cli"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            response.read(64 * 1024)
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"handler unreachable: {getattr(exc, 'reason', None) or type(exc).__name__}") from None


def run_command(argv: list[str], payload: dict[str, Any], *, timeout: float = COMMAND_TIMEOUT_SECONDS) -> int:
    """Run a command (never via a shell) with the event JSON on stdin. Returns its exit status."""
    try:
        done = subprocess.run(argv, input=json.dumps(payload).encode("utf-8"), timeout=timeout,
                              shell=False, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"handler command timed out after {timeout:g}s") from None
    except OSError as exc:
        raise RuntimeError(f"handler command failed to start: {exc.strerror}") from None
    return done.returncode


def dispatch_email(
    client: AgentEmailClient, agent: str, event: dict[str, Any], *,
    forward_to: str | None = None, command: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch the full message for an `email.received` event and hand it to the configured handler."""
    message_id = event.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("event has no message id")
    message = client.get_message(agent, message_id)
    payload = {"event": "email.received", "agent_email": agent, "message": message}
    result: dict[str, Any] = {"message_id": message_id, "subject": message.get("subject")}
    if forward_to:
        result["forward_status"] = forward_json(forward_to, payload)
    if command:
        result["command_exit"] = run_command(command, payload)
    return result


def ws_url(base_url: str, agent: str) -> str:
    from urllib.parse import quote
    parts = urlsplit(base_url.rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}{parts.path}/v1/agents/{quote(agent, safe='')}/ws"


def _default_connect() -> Callable[..., Any]:
    try:
        import websockets
    except ImportError:
        raise UsageError("`oae listen` needs the websockets package: pip install 'open-agent-email[listen]'") from None
    params = inspect.signature(websockets.connect).parameters
    header_kw = "additional_headers" if "additional_headers" in params else "extra_headers"

    def connect(url: str, headers: dict[str, str]):
        return websockets.connect(url, **{header_kw: headers}, ping_interval=20, open_timeout=15)

    return connect


class FatalListenError(Exception):
    """The server rejected the credential or inbox; reconnecting cannot help."""


async def listen_loop(
    client: AgentEmailClient, base_url: str, api_key: str, agent: str, *,
    forward_to: str | None = None, command: list[str] | None = None,
    max_events: int | None = None, connect: Callable[..., Any] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    log: Callable[[str], None] = lambda text: print(text, file=sys.stderr, flush=True),
    emit_line: Callable[[str], None] = lambda text: print(text, flush=True),
) -> int:
    connect = connect or _default_connect()
    url = ws_url(base_url, agent)
    headers = {"Authorization": f"Bearer {api_key}"}
    handled, delay = 0, 1.0
    while True:
        try:
            async with connect(url, headers) as socket:
                log(f"connected: {agent}")
                delay = 1.0
                async for raw in socket:
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(frame, dict) or frame.get("type") != "email.received":
                        continue
                    try:
                        result = await asyncio.to_thread(
                            dispatch_email, client, agent, frame.get("data") or {},
                            forward_to=forward_to, command=command)
                        emit_line(json.dumps(result))
                    except (RuntimeError, AgentEmailError) as exc:
                        log(f"handler error: {exc}")
                    handled += 1
                    if max_events is not None and handled >= max_events:
                        return EXIT_OK
                close_code = getattr(socket, "close_code", None)
                close_reason = getattr(socket, "close_reason", None)
                if close_code == 1000:
                    return EXIT_OK
                if close_code == 4000 and close_reason == "replaced":
                    raise FatalListenError("connection replaced by a newer client")
                if isinstance(close_code, int) and 4000 <= close_code <= 4999:
                    raise FatalListenError(f"server closed the connection with terminal code {close_code}")
        except FatalListenError:
            raise
        except Exception as exc:  # connection dropped or refused
            code = getattr(getattr(exc, "rcvd", None), "code", None) or getattr(exc, "code", None)
            status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
            reason = getattr(getattr(exc, "rcvd", None), "reason", None) or getattr(exc, "reason", None)
            if code == 4000 and reason == "replaced":
                raise FatalListenError("connection replaced by a newer client") from None
            if (isinstance(code, int) and 4000 <= code <= 4999) or code == 1008 or status in {401, 403, 404}:
                raise FatalListenError("server closed the connection with a terminal code or rejected the credential/inbox") from None
            log(f"disconnected ({type(exc).__name__}); retrying in {delay:.0f}s")
            await sleep(delay + random.uniform(0, delay / 4))
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)


def cmd_listen(args: argparse.Namespace) -> int:
    if not args.forward_to and not args.command:
        raise UsageError("give a handler: --forward-to URL and/or --command CMD")
    forward_to = validate_forward_url(args.forward_to) if args.forward_to else None
    command = shlex.split(args.command) if args.command else None
    if args.command and not command:
        raise UsageError("--command is empty")
    base_url, api_key = resolve_credentials(args)
    client = make_client(args)
    try:
        return asyncio.run(listen_loop(
            client, base_url, api_key, args.agent, forward_to=forward_to, command=command,
            max_events=args.max_events))
    except FatalListenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_API
    except KeyboardInterrupt:
        return EXIT_OK


# ---- argument parsing ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=argparse.SUPPRESS, help="server URL (env OAE_BASE_URL)")
    common.add_argument("--api-key", default=argparse.SUPPRESS, help="API key (env OAE_API_KEY)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="request timeout in seconds (max 60)")

    parser = argparse.ArgumentParser(prog="oae", description="Open Agent Email command-line client", parents=[common])
    sub = parser.add_subparsers(dest="command_name", required=True, metavar="COMMAND")

    def add(name: str, handler: Callable[[argparse.Namespace], int], help_text: str):
        p = sub.add_parser(name, help=help_text, parents=[common])
        p.set_defaults(handler=handler)
        return p

    def actions(p: argparse.ArgumentParser, names: Sequence[str]):
        inner = p.add_subparsers(dest="action", required=True, metavar="ACTION")
        return {name: inner.add_parser(name, parents=[common]) for name in names}

    p = add("login", cmd_login, "save server URL and API key (macOS keychain when available, else a 0600 file)")
    p.add_argument("--file", action="store_true", help="store the key in the config file instead of the keychain")
    add("logout", cmd_logout, "delete saved credentials")
    acts = actions(add("auth", cmd_auth, "log in, log out, or show where the credential comes from"), ["login", "status", "logout"])
    acts["login"].add_argument("--file", action="store_true", help="store the key in the config file instead of the keychain")
    add("whoami", cmd_whoami, "show the credential's scope")
    add("info", cmd_info, "show server discovery info")

    acts = actions(add("agents", cmd_agents, "manage inboxes (admin key)"), ["list", "create", "get"])
    acts["list"].add_argument("--include-deleted", action="store_true")
    acts["create"].add_argument("email")
    acts["create"].add_argument("--name")
    acts["create"].add_argument("--description")
    acts["create"].add_argument("--threat-sensitivity", default="medium", choices=["off", "low", "medium", "high"])
    acts["create"].add_argument("--no-review", action="store_true", help="disable the outbound approval gate")
    acts["get"].add_argument("email")

    acts = actions(add("messages", cmd_messages, "read, send and triage messages"),
                   ["list", "get", "send", "reply", "forward", "label", "trash", "restore"])
    for name, action_parser in acts.items():
        action_parser.add_argument("--agent", required=True, help="inbox address")
        if name not in {"list", "send"}:
            action_parser.add_argument("id", help="message id")
    acts["list"].add_argument("--direction", choices=["inbound", "outbound"])
    acts["list"].add_argument("--status")
    acts["list"].add_argument("--conversation-id")
    acts["list"].add_argument("--label")
    acts["list"].add_argument("--trashed", action="store_true")
    acts["list"].add_argument("--limit", type=int, default=20)
    for name in ("send", "reply"):
        p = acts[name]
        p.add_argument("--conversation-id", help="caller-owned conversation identifier")
        p.add_argument("--body")
        p.add_argument("--body-file", help="read body from a file, or - for stdin")
        p.add_argument("--template", help="saved template id")
        p.add_argument("--data-json", help="template variables as a JSON object")
        p.add_argument("--scheduled-at", "--send-at", dest="scheduled_at", type=_send_at_arg,
                       help="defer until this timezone-qualified time (ISO-8601, <= 90 days)")
        p.add_argument("--idempotency-key")
        p.add_argument("--dry-run", action="store_true", help="print the request that would be sent, without sending it")
    acts["send"].add_argument("--to", required=True)
    acts["send"].add_argument("--subject")
    acts["forward"].add_argument("--to", required=True)
    acts["forward"].add_argument("--note")
    acts["label"].add_argument("--set", default="", help="comma-separated labels (replaces all; empty clears)")

    acts = actions(add("conversations", cmd_conversations, "list and read threads"), ["list", "get"])
    for p in acts.values():
        p.add_argument("--agent", required=True)
    acts["list"].add_argument("--limit", type=int, default=20)
    acts["get"].add_argument("id")

    p = add("otp", cmd_otp, "find a recent verification code")
    p.add_argument("--agent", required=True)
    p.add_argument("--since", type=int, default=600, help="look back this many seconds")
    p.add_argument("--wait", type=float, default=0, help="wait up to N seconds (max 10)")

    acts = actions(add("reviews", cmd_reviews, "human review queue"), ["list", "approve", "reject"])
    acts["list"].add_argument("--status", choices=["pending", "approved", "rejected"])
    acts["list"].add_argument("--agent")
    for name in ("approve", "reject"):
        acts[name].add_argument("id")
        acts[name].add_argument("--notes")

    acts = actions(add("webhooks", cmd_webhooks, "webhook endpoints (admin key)"),
                   ["list", "create", "test", "rotate", "deliveries"])
    acts["list"].add_argument("--agent")
    acts["create"].add_argument("--agent", required=True)
    acts["create"].add_argument("--url", required=True)
    acts["create"].add_argument("--events", help="comma-separated event types (default all)")
    acts["test"].add_argument("id")
    acts["rotate"].add_argument("id")
    acts["deliveries"].add_argument("--endpoint")
    acts["deliveries"].add_argument("--status")
    acts["deliveries"].add_argument("--limit", type=int, default=20)

    acts = actions(add("events", cmd_events, "event log (admin key)"), ["list", "redeliver"])
    acts["list"].add_argument("--agent")
    acts["list"].add_argument("--type")
    acts["list"].add_argument("--status")
    acts["list"].add_argument("--since", help="inclusive RFC-3339 timestamp with timezone offset")
    acts["list"].add_argument("--until", help="inclusive RFC-3339 timestamp with timezone offset")
    acts["list"].add_argument("--limit", type=int, default=20)
    acts["redeliver"].add_argument("id")

    p = add("listen", cmd_listen, "stream inbound mail to a local handler")
    p.add_argument("--agent", required=True)
    p.add_argument("--forward-to", help="POST each message as JSON to this URL (http only on localhost)")
    p.add_argument("--command", help="run this command (no shell) with each message as JSON on stdin")
    p.add_argument("--max-events", type=int, help="exit after handling N messages")
    return parser


def _send_at_arg(value: str) -> str:
    try:
        return _format_send_at(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits with 2 on usage errors, 0 for --help
        return int(exc.code or 0)
    try:
        return args.handler(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:  # client-side validation
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except AgentEmailError as exc:
        code = f" [{exc.code}]" if exc.code else ""
        detail = f": {exc.detail}" if exc.detail else ""
        retry = f" (retry after {exc.retry_after_seconds:g}s)" if exc.retry_after_seconds is not None else ""
        print(f"error: {exc}{code}{detail}{retry}", file=sys.stderr)
        return EXIT_API
    except BrokenPipeError:
        return EXIT_OK


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
