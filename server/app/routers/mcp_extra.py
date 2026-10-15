"""Additional MCP tools. Each delegates to the same code paths as the REST API."""

import datetime
import json

from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import base64

from app.models import (
    Agent, Contact, ContactEngagement, EmailTemplate, Message, MessageAttachment, RecipientSuppression,
)
from app.routers import (
    account, agents, api_keys, contacts, conversations, domains, engagements, events, messages, meta,
    outbox, starter_templates, suppressions, templates, webhooks,
)
from app.schemas import (
    AccountApiKeyCreate,
    AgentCreate, AgentResponse, AgentUpdate, ContactConsentWrite,
    ContactCreate, ContactResponse, ContactUpdate, ContactEngagementUpdate, ContactEngagementWrite,
    DomainCreate, DomainResponse, EmailTemplateCreate, EmailTemplatePreviewRequest,
    EmailTemplateRenderData, EmailTemplateResponse, EmailTemplateUpdate, MessageLabelsUpdate,
    OutboundJobResponse, RecipientSuppressionCreate,
)
from app.security import Principal

_EMAIL = {"agent_email": {"type": "string", "description": "Agent inbox address"}}
_MSG = {**_EMAIL, "message_id": {"type": "string"}}
_ID = {"type": "string"}
_PAGED = {"paged": {"type": "boolean", "default": False,
                    "description": "Return {items,next_cursor}; set true on the first page and pass cursor on later pages."}}


def _tool(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": required or []}}


def _limit(default, maximum):
    return {"type": "integer", "minimum": 1, "maximum": maximum, "default": default}


def _page_requested(args):
    return args.get("paged", False) is True or "cursor" in args


EXT_TOOLS = [
    _tool("whoami", "Describe the credential making this call (admin or inbox-scoped key, scope, inbox)."),
    _tool("list_conversations", "List recent email threads with message/unread counts, participants and labels.",
          {**_EMAIL, "limit": _limit(50, 200), "cursor": _ID,
           "paged": {"type": "boolean", "default": False, "description": "Return {items,next_cursor}; set true on the first page and pass cursor on later pages."}}, ["agent_email"]),
    _tool("get_conversation", "Fetch every message in one thread, oldest first.",
          {**_EMAIL, "conversation_id": {"type": "string"}}, ["agent_email", "conversation_id"]),
    _tool("get_message_lifecycle", "Chronological timeline of review, queueing, retries and provider delivery outcomes.",
          _MSG, ["agent_email", "message_id"]),
    _tool("get_agent_metrics", "Message counts by direction/status plus unread and pending-review totals.",
          _EMAIL, ["agent_email"]),
    _tool("get_protection", "Read the inbox's threat sensitivity and outbound approval gate.", _EMAIL, ["agent_email"]),
    _tool("update_protection", "Administrator only: set threat sensitivity (off/low/medium/high) and the approval gate.",
          {**_EMAIL, "threat_sensitivity": {"type": "string", "enum": ["off", "low", "medium", "high"]},
           "hitl_enabled": {"type": "boolean"},
           "review_notify_email": {"type": ["string", "null"], "description": "Reviewer address for emailed approve/reject links; null clears it"},
           "review_expiry_hours": {"type": ["integer", "null"], "minimum": 1, "maximum": 720, "description": "Resolve reviews still pending after this many hours; null disables expiry"},
           "review_expiry_action": {"type": ["string", "null"], "enum": ["approve", "reject", None], "description": "What expiry does; default reject"}},
          ["agent_email", "threat_sensitivity", "hitl_enabled"]),
    _tool("update_message_labels", "Replace a message's labels (triage tags such as needs-human).",
          {**_MSG, "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
          ["agent_email", "message_id", "labels"]),
    _tool("delete_message", "Move a message to trash (cancelling a scheduled send that has not been submitted). With permanent=true and confirm=DELETE it is deleted for good instead; that is refused while a review is pending or a send is queued.",
          {**_MSG, "permanent": {"type": "boolean", "default": False},
           "confirm": {"type": "string", "enum": ["DELETE"], "description": "Required when permanent is true"}},
          ["agent_email", "message_id"]),
    _tool("restore_message", "Restore a trashed message to the inbox.", _MSG, ["agent_email", "message_id"]),
    _tool("list_contacts", "List the inbox's contacts.",
          {**_EMAIL, "limit": _limit(50, 200), "cursor": _ID,
           "paged": {"type": "boolean", "default": False, "description": "Return {items,next_cursor}; set true on the first page and pass cursor on later pages."}}, ["agent_email"]),
    _tool("list_templates", "List the inbox's saved email templates.",
          {**_EMAIL, "limit": _limit(50, 200), "cursor": _ID,
           "paged": {"type": "boolean", "default": False, "description": "Return {items,next_cursor}; set true on the first page and pass cursor on later pages."}}, ["agent_email"]),
    _tool("list_suppressions", "List recipients this inbox will not email.",
          {**_EMAIL, "limit": _limit(50, 200), "cursor": _ID,
           "paged": {"type": "boolean", "default": False, "description": "Return {items,next_cursor}; set true on the first page and pass cursor on later pages."}}, ["agent_email"]),
    _tool("create_agent_suppression", "Block outbound email to one recipient from this inbox.",
          {**_EMAIL, "email": {"type": "string"}, "reason": {"type": "string"}}, ["agent_email", "email"]),
]

EXT_ADMIN = {"update_protection"}
EXT_WRITE = {"update_message_labels", "delete_message", "restore_message", "create_agent_suppression"}
EXT_READ = {"list_conversations", "get_conversation", "get_message_lifecycle", "get_agent_metrics",
            "get_protection", "list_contacts", "list_templates", "list_suppressions"}


def _dump(value):
    if isinstance(value, Response):
        if not value.body:
            return None
        try:
            return json.loads(value.body)
        except (TypeError, json.JSONDecodeError):
            return value.body.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if hasattr(value, "__table__"):
        return {c.name: _dump(getattr(value, c.name)) for c in value.__table__.columns
                if c.name not in {"body_html", "content", "key_hash"}}
    return value


def _int(args, key, default, lo, hi):
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{key} must be an integer between {lo} and {hi}")
    return value


async def run_ext_tool(db: AsyncSession, name: str, args: dict, principal: Principal) -> str:
    email = args.get("agent_email")
    if name in _HANDLERS:
        return json.dumps(_dump(await _HANDLERS[name](db, args, principal)))
    if name == "whoami":
        return json.dumps(await meta.whoami(principal))
    if name == "list_conversations":
        if not _page_requested(args):
            return json.dumps(_dump(await conversations.list_conversations(
                email, _int(args, "limit", 50, 1, 200), db)))
        return json.dumps(_dump(await conversations.list_conversations_page(
            email, _int(args, "limit", 50, 1, 200), args.get("cursor"), db)))
    if name == "get_conversation":
        return json.dumps(_dump(await conversations.get_conversation(email, args["conversation_id"], db)))
    if name == "get_message_lifecycle":
        return json.dumps(_dump(await messages.get_message_lifecycle(email, args["message_id"], db)))
    if name == "get_agent_metrics":
        return json.dumps(await meta.agent_metrics(email, db))
    if name == "get_protection":
        return json.dumps(await meta.get_protection(email, db))
    if name == "update_protection":
        body = meta.ProtectionUpdate(
            threat_sensitivity=args["threat_sensitivity"], hitl_enabled=args["hitl_enabled"],
            **_fields(args, "review_notify_email", "review_expiry_hours", "review_expiry_action"))
        return json.dumps(await meta.update_protection(email, body, db))
    if name == "update_message_labels":
        req = MessageLabelsUpdate(labels=args["labels"])
        return json.dumps(_dump(await messages.update_message_labels(email, args["message_id"], req, db)))
    if name == "delete_message":
        permanent = args.get("permanent", False)
        if not isinstance(permanent, bool):
            raise ValueError("permanent must be a boolean")
        return json.dumps(_dump(await messages.trash_message(
            email, args["message_id"], permanent, args.get("confirm"), principal, db)))
    if name == "restore_message":
        return json.dumps(_dump(await messages.restore_message(email, args["message_id"], db)))
    if name == "list_contacts":
        if not _page_requested(args):
            return json.dumps(_dump(await contacts.list_contacts(
                email, _int(args, "limit", 100, 1, 500), db)))
        return json.dumps(_dump(await contacts.list_contacts_page(
            email, _int(args, "limit", 50, 1, 200), args.get("cursor"), db)))
    if name == "list_templates":
        if not _page_requested(args):
            agent = await db.scalar(select(Agent.id).where(Agent.email == email))
            if not agent:
                raise ValueError(f"Agent {email} not found")
            rows = (await db.execute(select(EmailTemplate).where(
                EmailTemplate.agent_email == email))).scalars().all()
            return json.dumps([{"id": t.id, "name": t.name, "subject": t.subject} for t in rows])
        page = await templates.list_templates_page(
            email, _int(args, "limit", 50, 1, 200), args.get("cursor"), db)
        return json.dumps({"items": [{"id": t.id, "name": t.name, "subject": t.subject}
                                      for t in page["items"]], "next_cursor": page["next_cursor"]})
    if name == "list_suppressions":
        if not _page_requested(args):
            rows = (await db.execute(select(RecipientSuppression).where(
                RecipientSuppression.agent_email == email))).scalars().all()
            return json.dumps([{"id": r.id, "email": r.email, "reason": r.reason} for r in rows])
        page = await suppressions.list_suppressions_page(
            email, _int(args, "limit", 50, 1, 200), args.get("cursor"), db)
        return json.dumps({"items": _dump(page["items"]), "next_cursor": page["next_cursor"]})
    if name == "create_agent_suppression":
        req = RecipientSuppressionCreate(email=args["email"], reason=args.get("reason"))
        return json.dumps(_dump(await suppressions.add_suppression(email, req, db)))
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Inbox, integration and administration tools. Each handler calls the same
# function as the matching REST route. The caller's scope is checked before
# dispatch in mcp.execute_mcp_tool: inbox match for EXT_READ/EXT_WRITE, an
# administrator credential for EXT_ADMIN.
# ---------------------------------------------------------------------------

MAX_ATTACHMENT_DATA_BYTES = 5 * 1024 * 1024
MAX_IMPORT_CSV_CHARS = 1_000_000


def _m(model, obj):
    return model.model_validate(obj).model_dump(mode="json")


def _require(args, *keys):
    missing = [k for k in keys if k not in args]
    if missing:
        raise ValueError(f"Missing required argument(s): {', '.join(missing)}")


def _fields(args, *keys):
    return {k: args[k] for k in keys if k in args}


class _McpRequest:
    """Request adapter for route handlers invoked directly by MCP tools."""

    def __init__(self, body: bytes = b"", idempotency_key: str | None = None):
        self._body = body
        self.headers = Headers(raw=[(b"idempotency-key", idempotency_key.encode("latin1"))]
                                if idempotency_key is not None else [])

    async def body(self) -> bytes:
        return self._body

    async def stream(self):
        yield self._body


def _mcp_request(args, body: bytes = b""):
    key = args.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not key):
        raise ValueError("idempotency_key must be a non-empty string")
    return _McpRequest(body, key)


def _json_request_body(args, *keys):
    return json.dumps(_fields(args, *keys), separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def _h_get_attachment(db, args, principal):
    _require(args, "agent_email", "message_id")
    inline = args.get("inline", False)
    if not isinstance(inline, bool):
        raise ValueError("inline must be a boolean")
    if not await db.scalar(select(Message.id).where(
            Message.id == args["message_id"], Message.agent_email == args["agent_email"])):
        raise ValueError(f"Message {args['message_id']} not found")
    rows = (await db.execute(
        select(MessageAttachment).where(MessageAttachment.message_id == args["message_id"])
        .order_by(MessageAttachment.created_at, MessageAttachment.id)
    )).scalars().all()
    if "index" in args:
        index = args["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("index must be a non-negative integer")
        if "attachment_id" in args:
            raise ValueError("provide index or attachment_id, not both")
        if index >= len(rows):
            raise ValueError("Attachment not found")
        return await messages.get_message_attachment_by_index(
            args["agent_email"], args["message_id"], index, inline, db
        )
    if inline:
        raise ValueError("inline requires index")
    if "attachment_id" in args:
        if args.get("inline"):
            raise ValueError("inline is available only with index")
        rows = [r for r in rows if r.id == args["attachment_id"]]
        if not rows:
            raise ValueError("Attachment not found")
    return [{"id": r.id, "filename": r.filename, "content_type": r.content_type, "size_bytes": r.size_bytes}
            for r in rows]


async def _h_get_attachment_data(db, args, principal):
    _require(args, "agent_email", "message_id", "attachment_id")
    row = await db.scalar(
        select(MessageAttachment).join(Message, Message.id == MessageAttachment.message_id).where(
            Message.id == args["message_id"], Message.agent_email == args["agent_email"],
            MessageAttachment.id == args["attachment_id"])
    )
    if not row:
        raise ValueError("Attachment not found")
    if row.size_bytes > MAX_ATTACHMENT_DATA_BYTES:
        raise ValueError(
            f"Attachment is {row.size_bytes} bytes; inline data is limited to {MAX_ATTACHMENT_DATA_BYTES}. "
            "Download it over the REST attachment endpoint instead."
        )
    return {"id": row.id, "filename": row.filename, "content_type": row.content_type,
            "size_bytes": row.size_bytes, "content_base64": base64.b64encode(row.content).decode("ascii")}


async def _h_get_template(db, args, principal):
    _require(args, "agent_email", "template_id")
    return _m(EmailTemplateResponse, await templates.get_template(args["agent_email"], args["template_id"], db))


async def _h_create_template(db, args, principal):
    _require(args, "agent_email")
    payload = EmailTemplateCreate(**_fields(args, "name", "subject", "body_text", "body_html"))
    return _m(EmailTemplateResponse, await templates.create_template(args["agent_email"], payload, db))


async def _h_update_template(db, args, principal):
    _require(args, "agent_email", "template_id")
    payload = EmailTemplateUpdate(**_fields(args, "name", "subject", "body_text", "body_html"))
    return _m(EmailTemplateResponse,
              await templates.update_template(args["agent_email"], args["template_id"], payload, db))


async def _h_delete_template(db, args, principal):
    _require(args, "agent_email", "template_id")
    await templates.delete_template(args["agent_email"], args["template_id"], db)
    return {"deleted": True, "template_id": args["template_id"]}


async def _h_preview_template(db, args, principal):
    _require(args, "agent_email")
    data = args.get("data") or {}
    if "template_id" in args:
        return await templates.preview_saved_template(
            args["agent_email"], args["template_id"], EmailTemplateRenderData(data=data), db)
    payload = EmailTemplatePreviewRequest(**_fields(args, "subject", "body_text", "body_html"), data=data)
    return await templates.preview_template(args["agent_email"], payload, db)


async def _h_create_contact(db, args, principal):
    _require(args, "agent_email")
    payload = ContactCreate(**_fields(args, "email", "name", "company", "notes"))
    request = _mcp_request(args, _json_request_body(args, "email", "name", "company", "notes"))
    result = await contacts.create_contact(args["agent_email"], payload, request, principal, db)
    return _m(ContactResponse, _dump(result))


async def _h_update_contact(db, args, principal):
    _require(args, "agent_email", "contact_id")
    payload = ContactUpdate(**_fields(args, "email", "name", "company", "notes"))
    return _m(ContactResponse, await contacts.update_contact(args["agent_email"], args["contact_id"], payload, db))


async def _h_delete_contact(db, args, principal):
    _require(args, "agent_email", "contact_id")
    await contacts.delete_contact(args["agent_email"], args["contact_id"], db)
    return {"deleted": True, "contact_id": args["contact_id"]}


async def _h_import_contacts(db, args, principal):
    _require(args, "agent_email", "csv")
    csv_text = args["csv"]
    if not isinstance(csv_text, str) or len(csv_text) > MAX_IMPORT_CSV_CHARS:
        raise ValueError(f"csv must be a string of at most {MAX_IMPORT_CSV_CHARS} characters")
    request = _mcp_request(args, csv_text.encode("utf-8"))
    return await contacts.import_contacts(args["agent_email"], request, principal, db)


async def _h_import_contacts_json(db, args, principal):
    _require(args, "agent_email", "contacts")
    if not isinstance(args["contacts"], list):
        raise ValueError("contacts must be an array")
    body = {"agent_email": args["agent_email"], "contacts": args["contacts"],
            "on_conflict": args.get("on_conflict", "merge")}
    raw_body = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload = contacts.ContactImportRequest.model_validate(body)
    request = _mcp_request(args, raw_body)
    return await contacts.import_contacts_json(request, payload, principal, db)


async def _h_test_agent(db, args, principal):
    _require(args, "agent_email")
    return _dump(await messages.test_agent_send(args["agent_email"], _mcp_request(args), db))


async def _h_delete_contact_import(db, args, principal):
    _require(args, "agent_email", "batch_id")
    return await contacts.reverse_contact_import(args["agent_email"], args["batch_id"], args.get("confirm", ""), db)


async def _h_get_contact_consent(db, args, principal):
    _require(args, "agent_email", "contact_id")
    return _dump(await contacts.get_contact_consent(args["agent_email"], args["contact_id"], db))


async def _h_get_contact(db, args, principal):
    _require(args, "agent_email", "contact_id")
    return _m(ContactResponse, await contacts._get_contact(args["agent_email"], args["contact_id"], db))


async def _h_record_contact_consent(db, args, principal):
    _require(args, "agent_email", "contact_id")
    payload = ContactConsentWrite(**_fields(args, "action", "source", "evidence", "note"))
    return _dump(await contacts.record_contact_consent(args["agent_email"], args["contact_id"], payload, db))


async def _h_list_outreach_contacts(db, args, principal):
    _require(args, "agent_email")
    due = args.get("due", False)
    if not isinstance(due, bool):
        raise ValueError("due must be a boolean")
    if not _page_requested(args):
        rows = await engagements.list_engagements(
            args["agent_email"], due, _int(args, "limit", 100, 1, 500), db)
        return [r.model_dump(mode="json") for r in rows]
    page = await engagements.list_engagements_page(
        args["agent_email"], due, _int(args, "limit", 50, 1, 200), args.get("cursor"), db)
    return _dump(page)


async def _h_set_outreach_contact(db, args, principal):
    _require(args, "agent_email", "contact_id")
    email = args["agent_email"]
    existing = await db.scalar(select(ContactEngagement).where(
        ContactEngagement.agent_email == email, ContactEngagement.contact_id == args["contact_id"]))
    fields = _fields(args, "stage", "next_due_at", "notes")
    if existing:
        result = await engagements.update_engagement(email, existing.id, ContactEngagementUpdate(**fields), db)
    else:
        result = await engagements.create_engagement(
            email, ContactEngagementWrite(contact_id=args["contact_id"], **fields), db)
    return result.model_dump(mode="json")


async def _h_delete_outreach_contact(db, args, principal):
    _require(args, "agent_email", "engagement_id")
    await engagements.delete_engagement(args["agent_email"], args["engagement_id"], db)
    return {"deleted": True, "engagement_id": args["engagement_id"]}


async def _h_get_outreach_contact(db, args, principal):
    _require(args, "agent_email", "engagement_id")
    row = (await db.execute(select(ContactEngagement, Contact).join(
        Contact, Contact.id == ContactEngagement.contact_id,
    ).where(ContactEngagement.agent_email == args["agent_email"], Contact.agent_email == args["agent_email"],
            ContactEngagement.id == args["engagement_id"])) ).one_or_none()
    if not row:
        raise ValueError("Outreach contact not found")
    return (await engagements._response(row, db)).model_dump(mode="json")


async def _h_get_domain(db, args, principal):
    _require(args, "domain")
    return _m(DomainResponse, await domains.get_domain(args["domain"], db))


async def _h_delete_agent_suppression(db, args, principal):
    _require(args, "agent_email", "suppression_id")
    await suppressions.remove_suppression(args["agent_email"], args["suppression_id"], db)
    return {"deleted": True, "suppression_id": args["suppression_id"]}


# --- administration (administrator credential only) ---

async def _h_list_webhooks(db, args, principal):
    if not _page_requested(args):
        return await webhooks.list_webhooks(args.get("agent_email"), _int(args, "limit", 100, 1, 200), db)
    return await webhooks.list_webhooks_page(
        args.get("agent_email"), _int(args, "limit", 50, 1, 200), args.get("cursor"), db)


async def _h_get_webhook(db, args, principal):
    _require(args, "webhook_id")
    return await webhooks.get_webhook(args["webhook_id"], db)


async def _h_create_webhook(db, args, principal):
    payload = webhooks.WebhookCreate(**_fields(args, "agent_email", "url", "enabled", "events"))
    request = _mcp_request(args, _json_request_body(args, "agent_email", "url", "enabled", "events"))
    return _dump(await webhooks.create_webhook(payload, request, principal, db))  # includes the signing secret, shown only now


async def _h_update_webhook(db, args, principal):
    _require(args, "webhook_id")
    payload = webhooks.WebhookUpdate(**_fields(args, "url", "enabled", "events"))
    return await webhooks.update_webhook(args["webhook_id"], payload, db)


async def _h_delete_webhook(db, args, principal):
    _require(args, "webhook_id")
    await webhooks.delete_webhook(args["webhook_id"], db)
    return {"deleted": True, "webhook_id": args["webhook_id"]}


async def _h_rotate_webhook_secret(db, args, principal):
    _require(args, "webhook_id")
    return await webhooks.rotate_webhook_secret(args["webhook_id"], _mcp_request(args), principal, db)  # new secret, shown only now


async def _h_test_webhook(db, args, principal):
    _require(args, "webhook_id")
    return await webhooks.send_test_webhook(args["webhook_id"], db)


async def _h_list_webhook_deliveries(db, args, principal):
    _require(args, "webhook_id")
    if not _page_requested(args):
        return await webhooks.list_webhook_deliveries(
            args["webhook_id"], args.get("status"), _int(args, "limit", 100, 1, 200), db)
    return await webhooks.page_deliveries(
        endpoint_id=args["webhook_id"], status=args.get("status"), limit=_int(args, "limit", 50, 1, 100),
        cursor=args.get("cursor"), db=db)


async def _h_list_events(db, args, principal):
    since = datetime.datetime.fromisoformat(args["since"]) if args.get("since") else None
    until = datetime.datetime.fromisoformat(args["until"]) if args.get("until") else None
    return await events.list_events(
        agent_email=args.get("agent_email"), event_type=args.get("event_type"), status=args.get("status"),
        since=since, until=until, limit=_int(args, "limit", 50, 1, 100), cursor=args.get("cursor"), db=db)


async def _h_get_event(db, args, principal):
    _require(args, "event_id")
    return await events.get_event(args["event_id"], db)


async def _h_redeliver_event(db, args, principal):
    _require(args, "event_id")
    return await events.redeliver_event(args["event_id"], principal, db)


async def _h_list_api_keys(db, args, principal):
    page = await api_keys.list_account_api_keys(
        _int(args, "limit", 100, 1, 100), args.get("cursor"), db)
    return _dump(page)


async def _h_create_api_key(db, args, principal):
    payload = AccountApiKeyCreate(**_fields(args, "name", "scope", "agent_email", "expires_in_days"))
    request = _mcp_request(args, _json_request_body(args, "name", "scope", "agent_email", "expires_in_days"))
    result = await api_keys.create_account_api_key(payload, request, principal, db)
    return _dump(result)  # key shown only now


async def _h_delete_api_key(db, args, principal):
    _require(args, "key_id")
    if args.get("confirm") != "DELETE":
        raise ValueError("confirm must be DELETE")
    return await api_keys.revoke_account_api_key(args["key_id"], "DELETE", db)


async def _h_list_domains(db, args, principal):
    if not _page_requested(args):
        return [_m(DomainResponse, d) for d in await domains.list_domains(db)]
    return await domains.list_domains_page(_int(args, "limit", 50, 1, 200), args.get("cursor"), db)


async def _h_register_domain(db, args, principal):
    _require(args, "domain")
    return _m(DomainResponse, await domains.create_domain(DomainCreate(domain=args["domain"]), db))


async def _h_verify_domain(db, args, principal):
    _require(args, "domain")
    return _m(DomainResponse, await domains.verify_domain(args["domain"], db))


async def _h_delete_domain(db, args, principal):
    _require(args, "domain")
    request = _mcp_request(args)
    await domains.delete_domain(args["domain"], request, principal, db)
    return {"deleted": True, "domain": args["domain"]}


async def _h_create_agent(db, args, principal):
    payload = AgentCreate(**_fields(args, "email", "display_name", "description", "threat_sensitivity",
                                    "hitl_enabled", "auto_reply_enabled", "auto_reply_prompt"))
    return _m(AgentResponse, await agents.create_agent(payload, db))


async def _h_get_agent(db, args, principal):
    _require(args, "agent_email")
    return _m(AgentResponse, await agents.get_agent(args["agent_email"], db))


async def _h_update_agent(db, args, principal):
    _require(args, "agent_email")
    payload = AgentUpdate(**_fields(args, "display_name", "description", "status", "threat_sensitivity",
                                    "hitl_enabled", "auto_reply_enabled", "auto_reply_prompt"))
    return _m(AgentResponse, await agents.update_agent(args["agent_email"], payload, db))


async def _h_delete_agent(db, args, principal):
    _require(args, "agent_email")
    purge = args.get("purge", False)
    if not isinstance(purge, bool):
        raise ValueError("purge must be a boolean")
    await agents.delete_agent(args["agent_email"], purge, db)
    return {"deleted": True, "purged": purge, "agent_email": args["agent_email"]}


async def _h_get_account_metrics(db, args, principal):
    def parse_datetime(key):
        raw = args.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ValueError(f"{key} must be an RFC 3339 datetime string")
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{key} must be an RFC 3339 datetime string") from exc

    bucket = args.get("bucket")
    group_by = args.get("group_by")
    if bucket not in {None, "day"}:
        raise ValueError("bucket must be 'day'")
    if group_by not in {None, "agent"}:
        raise ValueError("group_by must be 'agent'")
    return _dump(await account.get_account_metrics(
        start=parse_datetime("start"), end=parse_datetime("end"),
        bucket=bucket, group_by=group_by, db=db,
    ))


async def _h_list_outbox(db, args, principal):
    if not _page_requested(args):
        rows = await outbox.list_outbound_jobs(args.get("status"), _int(args, "limit", 50, 1, 200), db)
        return [_m(OutboundJobResponse, r) for r in rows]
    return await outbox.page_outbound_jobs(
        args.get("status"), _int(args, "limit", 50, 1, 100), args.get("cursor"), db)


async def _h_retry_outbox_job(db, args, principal):
    _require(args, "job_id")
    confirm = args.get("confirm_duplicate_risk", False)
    if not isinstance(confirm, bool):
        raise ValueError("confirm_duplicate_risk must be a boolean")
    return _m(OutboundJobResponse, await outbox.retry_outbound_job(args["job_id"], confirm, principal, db))


_AE = _EMAIL
_TPL_FIELDS = {"name": _ID, "subject": _ID, "body_text": _ID, "body_html": _ID}
_CONTACT_FIELDS = {"email": _ID, "name": _ID, "company": _ID, "notes": _ID}
_WH_ID = {"webhook_id": {"type": "string", "description": "Webhook endpoint id (wh_...)"}}
_EVENTS = {"type": "array", "items": _ID}
_DELIVERY_STATUS = {"type": "string", "enum": ["pending", "processing", "delivered", "failed"]}
_AGENT_FIELDS = {
    "display_name": _ID, "description": _ID,
    "threat_sensitivity": {"type": "string", "enum": ["off", "low", "medium", "high"]},
    "hitl_enabled": {"type": "boolean"}, "auto_reply_enabled": {"type": "boolean"}, "auto_reply_prompt": _ID,
}

# (name, description, properties, required, kind, handler)
_NEW_SPECS = [
    ("get_attachment", "List attachment metadata, fetch one attachment by ID, or use a zero-based index to get metadata and a short-lived download URL. inline=true includes base64 only for indexed attachments up to 256 KiB.",
     {**_AE, "message_id": _ID, "attachment_id": _ID,
      "index": {"type": "integer", "minimum": 0, "description": "Zero-based attachment position; mutually exclusive with attachment_id."},
      "inline": {"type": "boolean", "default": False, "description": "With index, include base64 data only up to 256 KiB."}},
     ["agent_email", "message_id"], "read", _h_get_attachment),
    ("get_attachment_data", "Return one attachment's content as base64. Limited to 5 MB inline; larger files must be fetched over REST.",
     {**_AE, "message_id": _ID, "attachment_id": _ID}, ["agent_email", "message_id", "attachment_id"], "read", _h_get_attachment_data),
    ("get_template", "Fetch one saved email template.",
     {**_AE, "template_id": _ID}, ["agent_email", "template_id"], "read", _h_get_template),
    ("preview_template", "Render a saved template (template_id) or ad-hoc subject/body with placeholder data. Nothing is sent.",
     {**_AE, "template_id": _ID, "subject": _ID, "body_text": _ID, "body_html": _ID, "data": {"type": "object"}},
     ["agent_email"], "read", _h_preview_template),
    ("create_template", "Create a saved template with {{variable}} placeholders; needs a name, a subject, and body_text or body_html.",
     {**_AE, **_TPL_FIELDS}, ["agent_email", "name", "subject"], "write", _h_create_template),
    ("update_template", "Change fields of a saved template.",
     {**_AE, "template_id": _ID, **_TPL_FIELDS}, ["agent_email", "template_id"], "write", _h_update_template),
    ("delete_template", "Delete a saved template.",
     {**_AE, "template_id": _ID}, ["agent_email", "template_id"], "write", _h_delete_template),
    ("create_contact", "Add an inert contact record to the inbox. It does not imply consent and sends nothing.",
     {**_AE, **_CONTACT_FIELDS, "idempotency_key": _ID}, ["agent_email", "email", "name"], "write", _h_create_contact),
    ("update_contact", "Change a contact's fields.",
     {**_AE, "contact_id": _ID, **_CONTACT_FIELDS}, ["agent_email", "contact_id"], "write", _h_update_contact),
    ("delete_contact", "Delete a contact and its follow-up record.",
     {**_AE, "contact_id": _ID}, ["agent_email", "contact_id"], "write", _h_delete_contact),
    ("import_contacts", "Import contacts from CSV text (header: email,name,company,notes; at most 1000 rows and 1 MB). Reports every row and skips duplicates and suppressed recipients.",
     {**_AE, "csv": _ID, "idempotency_key": _ID}, ["agent_email", "csv"], "write", _h_import_contacts),
    ("import_contacts_json", "Import up to 1,000 structured contacts into the explicitly selected inbox. Rows are inert and never imply consent.",
     {**_AE, "contacts": {"type": "array", "minItems": 1, "maxItems": 1000, "items": {"type": "object"}},
      "on_conflict": {"type": "string", "enum": ["merge", "skip"], "default": "merge"}, "idempotency_key": _ID},
     ["agent_email", "contacts"], "write", _h_import_contacts_json),
    ("test_agent", "Queue a platform-originated test message to this inbox. Requires full access or an administrator credential.",
     {**_AE, "idempotency_key": _ID}, ["agent_email"], "write", _h_test_agent),
    ("delete_contact_import", "Reverse an import batch, deleting only contacts not edited since import. Requires confirm=DELETE.",
     {**_AE, "batch_id": _ID, "confirm": {"type": "string", "enum": ["DELETE"]}},
     ["agent_email", "batch_id", "confirm"], "write", _h_delete_contact_import),
    ("get_contact_consent", "Read a contact's append-only consent history and current state (granted, revoked, unknown).",
     {**_AE, "contact_id": _ID}, ["agent_email", "contact_id"], "read", _h_get_contact_consent),
    ("record_contact_consent", "Append an operator-entered consent change (granted or revoked) with its source and evidence.",
     {**_AE, "contact_id": _ID, "action": {"type": "string", "enum": ["granted", "revoked"]},
      "source": _ID, "evidence": _ID, "note": _ID},
     ["agent_email", "contact_id", "action", "source"], "write", _h_record_contact_consent),
    ("list_outreach_contacts", "List follow-up records (stage, next due time, reply and suppression facts). due=true returns only currently due, unsuppressed ones.",
     {**_AE, "due": {"type": "boolean", "default": False}, "limit": _limit(50, 200), "cursor": _ID, **_PAGED},
     ["agent_email"], "read", _h_list_outreach_contacts),
    ("set_outreach_contact", "Create or update a contact's follow-up record: stage (new, contacted, waiting, closed), optional next_due_at ISO time, notes. Never sends mail.",
     {**_AE, "contact_id": _ID, "stage": {"type": "string", "enum": ["new", "contacted", "waiting", "closed"]},
      "next_due_at": {"type": "string", "format": "date-time"}, "notes": _ID},
     ["agent_email", "contact_id"], "write", _h_set_outreach_contact),
    ("delete_outreach_contact", "Delete a follow-up record by engagement_id.",
     {**_AE, "engagement_id": _ID}, ["agent_email", "engagement_id"], "write", _h_delete_outreach_contact),
    ("delete_agent_suppression", "Remove a suppression so the inbox may email that recipient again.",
     {**_AE, "suppression_id": _ID}, ["agent_email", "suppression_id"], "write", _h_delete_agent_suppression),
    ("get_contact", "Fetch one contact in an inbox.",
     {**_AE, "contact_id": _ID}, ["agent_email", "contact_id"], "read", _h_get_contact),
    ("get_outreach_contact", "Fetch one contact follow-up record by engagement_id.",
     {**_AE, "engagement_id": _ID}, ["agent_email", "engagement_id"], "read", _h_get_outreach_contact),
    ("list_webhooks", "Administrator only: list webhook endpoints, optionally for one inbox. Signing secrets are never returned.",
     {**_AE, "limit": _limit(50, 200), "cursor": _ID, **_PAGED}, [], "admin", _h_list_webhooks),
    ("get_webhook", "Administrator only: fetch one webhook endpoint (no secret).",
     _WH_ID, ["webhook_id"], "admin", _h_get_webhook),
    ("create_webhook", "Administrator only: register an HTTPS webhook (port 443, public host) for an inbox. The signing secret is returned once, in this response only.",
     {**_AE, "url": _ID, "enabled": {"type": "boolean"}, "events": _EVENTS, "idempotency_key": _ID}, ["agent_email", "url"], "admin", _h_create_webhook),
    ("update_webhook", "Administrator only: change a webhook's url, enabled flag or event filter.",
     {**_WH_ID, "url": _ID, "enabled": {"type": "boolean"}, "events": _EVENTS}, ["webhook_id"], "admin", _h_update_webhook),
    ("delete_webhook", "Administrator only: delete a webhook endpoint and its delivery history.",
     _WH_ID, ["webhook_id"], "admin", _h_delete_webhook),
    ("rotate_webhook_secret", "Administrator only: issue a new signing secret, returned once; the old one stays valid for 24 hours.",
     {**_WH_ID, "idempotency_key": _ID}, ["webhook_id"], "admin", _h_rotate_webhook_secret),
    ("test_webhook", "Administrator only: queue a signed synthetic event to an enabled webhook.",
     _WH_ID, ["webhook_id"], "admin", _h_test_webhook),
    ("list_webhook_deliveries", "Administrator only: delivery attempts for one webhook, newest first.",
     {**_WH_ID, "status": _DELIVERY_STATUS, "limit": _limit(50, 100), "cursor": _ID, **_PAGED}, ["webhook_id"], "admin", _h_list_webhook_deliveries),
    ("list_events", "Administrator only: cursor-paged event log across webhooks, filterable by inbox, type and status.",
     {**_AE, "event_type": _ID, "status": _DELIVERY_STATUS, "since": {"type": "string", "format": "date-time"},
      "until": {"type": "string", "format": "date-time"}, "limit": _limit(50, 100), "cursor": _ID},
     [], "admin", _h_list_events),
    ("get_event", "Administrator only: fetch one event with its payload and delivery state.",
     {"event_id": _ID}, ["event_id"], "admin", _h_get_event),
    ("redeliver_event", "Administrator only: re-queue a failed or delivered event for another delivery.",
     {"event_id": _ID}, ["event_id"], "admin", _h_redeliver_event),
    ("list_api_keys", "Administrator only: list canonical account and inbox API keys (prefix, scope, inbox and expiry; never the key itself).",
     {"limit": _limit(100, 100), "cursor": _ID}, [], "admin", _h_list_api_keys),
    ("create_api_key", "Administrator only: create a canonical account-scoped or inbox-scoped key. The key is returned once, in this response only.",
     {"name": _ID, "agent_email": _ID, "scope": {"type": "string", "enum": ["account", "agent"]},
      "expires_in_days": {"type": "integer", "minimum": 1, "maximum": 3650}, "idempotency_key": _ID},
     ["name", "scope"], "admin", _h_create_api_key),
    ("delete_api_key", "Administrator only: revoke a canonical account or inbox API key immediately. Requires confirm=DELETE.",
     {"key_id": _ID, "confirm": {"type": "string", "enum": ["DELETE"]}}, ["key_id", "confirm"], "admin", _h_delete_api_key),
    ("list_domains", "Administrator only: list registered domains and their DNS record status.",
     {"limit": _limit(50, 200), "cursor": _ID, **_PAGED}, [], "admin", _h_list_domains),
    ("get_domain", "Administrator only: fetch one registered domain and its DNS record status.",
     {"domain": _ID}, ["domain"], "admin", _h_get_domain),
    ("register_domain", "Administrator only: register a domain, generate its DKIM key, and return the exact DNS records to publish.",
     {"domain": _ID}, ["domain"], "admin", _h_register_domain),
    ("verify_domain", "Administrator only: look up the domain's MX, SPF, DKIM and DMARC records and update their status (verified, pending, mismatch, error). The domain becomes verified when MX, SPF and DKIM all match.",
     {"domain": _ID}, ["domain"], "admin", _h_verify_domain),
    ("delete_domain", "Administrator only: remove a registered domain.",
     {"domain": _ID, "idempotency_key": _ID}, ["domain"], "admin", _h_delete_domain),
    ("create_agent", "Administrator only: create an inbox.",
     {"email": _ID, **_AGENT_FIELDS}, ["email", "display_name"], "admin", _h_create_agent),
    ("get_agent", "Administrator only: fetch one inbox's configuration.",
     _AE, ["agent_email"], "admin", _h_get_agent),
    ("update_agent", "Administrator only: change an inbox's name, status, screening or approval settings.",
     {**_AE, "status": _ID, **_AGENT_FIELDS}, ["agent_email"], "admin", _h_update_agent),
    ("delete_agent", "Administrator only: delete an inbox. By default it is soft-deleted (stops receiving mail, hidden, restorable); purge=true permanently removes it with its messages, contacts and keys.",
     {**_AE, "purge": {"type": "boolean", "default": False}}, ["agent_email"], "admin", _h_delete_agent),
    ("get_account_metrics", "Administrator only: account-wide lifecycle metrics over a half-open cohort window, with optional daily buckets and per-inbox groups.",
     {"start": {"type": "string", "format": "date-time"}, "end": {"type": "string", "format": "date-time"},
      "bucket": {"type": "string", "enum": ["day"]}, "group_by": {"type": "string", "enum": ["agent"]}},
     [], "admin", _h_get_account_metrics),
    ("list_outbox", "Administrator only: durable outbound queue jobs, optionally by status.",
     {"status": {"type": "string", "enum": ["pending", "processing", "complete", "failed", "uncertain", "blocked"]},
      "limit": _limit(50, 100), "cursor": _ID, **_PAGED}, [], "admin", _h_list_outbox),
    ("retry_outbox_job", "Administrator only: retry a failed or uncertain job. An uncertain job may already have been accepted by the relay, so confirm_duplicate_risk=true is required for it.",
     {"job_id": _ID, "confirm_duplicate_risk": {"type": "boolean", "default": False}},
     ["job_id"], "admin", _h_retry_outbox_job),
]

# --- account-level, starter-template and review-setting tools ---

async def _h_list_account_suppressions(db, args, principal):
    if not _page_requested(args):
        return await account.list_account_suppressions(
            args.get("address"), _int(args, "limit", 200, 1, 1000), db)
    return await account.list_account_suppressions_page(
        args.get("address"), _int(args, "limit", 50, 1, 200), args.get("cursor"), db)


async def _h_delete_account_suppression(db, args, principal):
    _require(args, "address")
    return await account.remove_account_suppression(args["address"], db)


async def _h_export_account(db, args, principal):
    return await account.export_account(_int(args, "limit_per_collection", account.EXPORT_DEFAULT_LIMIT,
                                             1, account.EXPORT_MAX_LIMIT), db)


async def _h_list_account_contacts(db, args, principal):
    if not _page_requested(args):
        return await account.list_account_contacts(
            args.get("agent_email"), args.get("q"), _int(args, "limit", 100, 1, 500), db)
    return await account.list_account_contacts_page(
        args.get("agent_email"), args.get("q"), _int(args, "limit", 50, 1, 200), args.get("cursor"), db)


async def _h_restore_agent(db, args, principal):
    _require(args, "agent_email")
    return _m(AgentResponse, await agents.restore_agent(args["agent_email"], db))


async def _h_get_review_settings(db, args, principal):
    _require(args, "agent_email")
    return await meta.get_review_settings(args["agent_email"], db)


async def _h_list_starter_templates(db, args, principal):
    if not _page_requested(args):
        return await starter_templates.list_starter_templates()
    return await starter_templates.list_starter_templates_page(
        _int(args, "limit", 50, 1, 200), args.get("cursor"))


async def _h_get_starter_template(db, args, principal):
    _require(args, "alias")
    return await starter_templates.get_starter_template(args["alias"])


async def _h_validate_template(db, args, principal):
    _require(args, "subject")
    payload = EmailTemplatePreviewRequest(**_fields(args, "subject", "body_text", "body_html", "data"))
    return await starter_templates.validate_template(payload)


async def _h_copy_starter_template(db, args, principal):
    _require(args, "agent_email", "alias")
    created = await starter_templates.copy_starter_template(args["agent_email"], args["alias"], principal, db)
    return _m(EmailTemplateResponse, created)


_MORE_SPECS = [
    ("list_account_suppressions", "Administrator only: suppressed recipients across every inbox, optionally for one address.",
     {"address": _ID, "limit": _limit(50, 200), "cursor": _ID, **_PAGED}, [], "admin", _h_list_account_suppressions),
    ("delete_account_suppression", "Administrator only: remove an address from every inbox's suppression list.",
     {"address": _ID}, ["address"], "admin", _h_delete_account_suppression),
    ("export_account", "Administrator only: bounded configuration export (inboxes, contacts, templates, suppressions, webhook endpoints) without message bodies, attachments, keys or secrets. `truncated` reports capped collections.",
     {"limit_per_collection": _limit(account.EXPORT_DEFAULT_LIMIT, account.EXPORT_MAX_LIMIT)}, [], "admin", _h_export_account),
    ("list_account_contacts", "Administrator only: contacts across inboxes, filterable by inbox and by address, name or company text.",
     {**_AE, "q": _ID, "limit": _limit(50, 200), "cursor": _ID, **_PAGED}, [], "admin", _h_list_account_contacts),
    ("restore_agent", "Administrator only: restore a soft-deleted inbox so it receives mail again.",
     _AE, ["agent_email"], "admin", _h_restore_agent),
    ("get_review_settings", "Administrator only: the inbox's review notification address, expiry window and expiry action.",
     _AE, ["agent_email"], "admin", _h_get_review_settings),
    ("list_starter_templates", "List the built-in starter email templates (verification code, password reset, welcome, receipt, and more).",
     {"limit": _limit(50, 200), "cursor": _ID, **_PAGED}, [], "any", _h_list_starter_templates),
    ("get_starter_template", "Fetch one starter template by alias with its body, variables and sample data.",
     {"alias": _ID}, ["alias"], "any", _h_get_starter_template),
    ("validate_template", "Check placeholder syntax and render sample data without saving anything.",
     {"subject": _ID, "body_text": _ID, "body_html": _ID, "data": {"type": "object"}}, ["subject"], "any", _h_validate_template),
    ("copy_starter_template", "Copy a starter template into an inbox as an editable saved template.",
     {**_AE, "alias": _ID}, ["agent_email", "alias"], "write", _h_copy_starter_template),
]

_HANDLERS = {}
for _name, _desc, _props, _req, _kind, _handler in _NEW_SPECS + _MORE_SPECS:
    EXT_TOOLS.append(_tool(_name, _desc, dict(_props), _req))
    _HANDLERS[_name] = _handler
    {"read": EXT_READ, "write": EXT_WRITE, "admin": EXT_ADMIN, "any": set()}[_kind].add(_name)

EXT_NAMES = {t["name"] for t in EXT_TOOLS}
