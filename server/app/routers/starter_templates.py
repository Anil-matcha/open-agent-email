"""Stateless template validation and the built-in starter template catalog."""

import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, EmailTemplate
from app.routers.templates import _preview, _validate_sources
from app.schemas import EmailTemplatePreviewRequest, EmailTemplateResponse
from app.security import Principal, require_agent_access, require_read, require_write
from app.services.starter_templates import STARTER_TEMPLATES, get_starter
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(tags=["Templates"])


def _public(item: dict, *, include_body: bool) -> dict:
    out = {"alias": item["alias"], "name": item["name"], "description": item["description"],
           "subject": item["subject"]}
    if include_body:
        preview = _preview(item["subject"], item["body_text"], item["body_html"], item["sample_data"])
        out.update(body_text=item["body_text"], body_html=item["body_html"],
                   sample_data=item["sample_data"], variables=preview["variables"], preview=preview)
    return out


@router.post("/v1/templates/validate", dependencies=[Depends(require_read)])
async def validate_template(payload: EmailTemplatePreviewRequest):
    """Validate placeholder syntax and render with the supplied data. Nothing is stored."""
    if not payload.body_text and not payload.body_html:
        raise HTTPException(status_code=422, detail="At least one of body_text or body_html is required")
    _validate_sources(payload.subject, payload.body_text, payload.body_html)
    return {"valid": True, **_preview(payload.subject, payload.body_text, payload.body_html, payload.data)}


@router.get("/v1/starter-templates", dependencies=[Depends(require_read)])
async def list_starter_templates():
    return {"items": [_public(item, include_body=False) for item in STARTER_TEMPLATES]}


@router.get("/v1/starter-templates/page", dependencies=[Depends(require_read)])
async def list_starter_templates_page(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=2048),
):
    scope = "/v1/starter-templates"
    start = 0
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            start = int(key)
            if start < 0 or start >= len(STARTER_TEMPLATES) or STARTER_TEMPLATES[start]["alias"] != item_id:
                raise ValueError
        except (ValueError, TypeError):
            decode_cursor("invalid", scope=scope)
        start += 1
    chunk = STARTER_TEMPLATES[start:start + limit + 1]
    has_more = len(chunk) > limit
    chunk = chunk[:limit]
    next_cursor = encode_cursor(scope=scope, key=str(start + len(chunk) - 1),
                                item_id=chunk[-1]["alias"]) if has_more and chunk else None
    return {"items": [_public(item, include_body=False) for item in chunk], "next_cursor": next_cursor}


@router.get("/v1/starter-templates/{alias}", dependencies=[Depends(require_read)])
async def get_starter_template(alias: str):
    item = get_starter(alias)
    if item is None:
        raise HTTPException(status_code=404, detail="Starter template not found")
    return _public(item, include_body=True)


@router.post(
    "/v1/agents/{email}/templates/from-starter/{alias}",
    response_model=EmailTemplateResponse,
    status_code=201,
    dependencies=[Depends(require_write)],
    tags=["Templates"],
)
async def copy_starter_template(
    email: str,
    alias: str,
    _: Principal = Depends(require_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """Copy a starter into this inbox. The copy is independent and can be edited freely."""
    item = get_starter(alias)
    if item is None:
        raise HTTPException(status_code=404, detail="Starter template not found")
    if await db.scalar(select(Agent.id).where(Agent.email == email)) is None:
        raise HTTPException(status_code=404, detail="Agent inbox not found")
    now = datetime.datetime.utcnow()
    template = EmailTemplate(
        id=f"tpl_{uuid.uuid4().hex[:16]}", agent_email=email, name=item["name"],
        subject=item["subject"], body_text=item["body_text"], body_html=item["body_html"],
        created_at=now, updated_at=now,
    )
    db.add(template)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A template with this name already exists in this inbox") from None
    await db.refresh(template)
    return template
