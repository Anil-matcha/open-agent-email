"""Inbox-scoped reusable email templates and a non-persistent preview endpoint."""
import datetime
import html
import json
import math
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, EmailTemplate
from app.schemas import (
    EmailTemplateCreate, EmailTemplatePreviewRequest, EmailTemplateResponse,
    EmailTemplateRenderData, EmailTemplateUpdate,
)
from app.security import require_agent_access, require_write
from app.services.cursors import decode_cursor, encode_cursor
from sqlalchemy import and_, or_


router = APIRouter(
    prefix="/v1/agents/{email}/templates",
    tags=["Templates"],
    dependencies=[Depends(require_agent_access)],
)

_VARIABLE = re.compile(r"\{\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}\}|\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")
_MAX_DATA_BYTES = 16_384
_MAX_NODES = 500
_MAX_DEPTH = 6


async def _ensure_agent(email: str, db: AsyncSession) -> None:
    if await db.scalar(select(Agent.id).where(Agent.email == email)) is None:
        raise HTTPException(status_code=404, detail="Agent inbox not found")


async def _get_template(email: str, template_id: str, db: AsyncSession) -> EmailTemplate:
    item = await db.scalar(
        select(EmailTemplate).where(EmailTemplate.agent_email == email, EmailTemplate.id == template_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return item


def _validate_data(value: Any, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _MAX_NODES or depth > _MAX_DEPTH:
        raise HTTPException(status_code=422, detail="Preview data exceeds complexity limits")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise HTTPException(status_code=422, detail="Preview object keys must be strings of at most 128 characters")
            _validate_data(child, depth + 1, count)
    elif isinstance(value, list):
        for child in value:
            _validate_data(child, depth + 1, count)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise HTTPException(status_code=422, detail="Preview string values are limited to 4,096 characters")
    elif isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(status_code=422, detail="Preview data numbers must be finite")
    elif value is not None and not isinstance(value, (int, float, bool)):
        raise HTTPException(status_code=422, detail="Preview data must contain JSON values")


def _lookup(data: dict[str, Any], path: str) -> str:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)


def _render(source: str, data: dict[str, Any], *, html_part: bool = False) -> tuple[str, list[str]]:
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1) or match.group(2)
        used.add(path)
        value = _lookup(data, path)
        # Triple braces explicitly request raw insertion. This is useful for
        # prebuilt HTML fragments; callers must escape any untrusted content.
        return html.escape(value, quote=True) if html_part and match.group(2) else value

    rendered = _VARIABLE.sub(substitute, source)
    if "{{" in rendered or "}}" in rendered:
        raise HTTPException(status_code=422, detail="Template has invalid variable syntax; use {{name}} or {{path.to.value}}")
    return rendered, sorted(used)


def _preview(subject: str, body_text: str, body_html: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Preview data must be valid JSON") from None
    if size > _MAX_DATA_BYTES:
        raise HTTPException(status_code=422, detail="Preview data is limited to 16 KB")
    _validate_data(data)
    rendered_subject, subject_vars = _render(subject, data)
    rendered_text, text_vars = _render(body_text, data)
    rendered_html, html_vars = _render(body_html, data, html_part=True)
    if not rendered_subject or "\r" in rendered_subject or "\n" in rendered_subject:
        raise HTTPException(status_code=422, detail="Rendered subject must be non-empty and cannot contain line breaks")
    if len(rendered_subject) > 512 or len(rendered_text) > 100_000 or len(rendered_html) > 100_000:
        raise HTTPException(status_code=422, detail="Rendered template exceeds the supported size")
    return {
        "subject": rendered_subject,
        "body_text": rendered_text,
        "body_html": rendered_html,
        "variables": sorted(set(subject_vars + text_vars + html_vars)),
    }


def _validate_sources(subject: str, body_text: str, body_html: str) -> None:
    _render(subject, {}, html_part=False)
    _render(body_text, {}, html_part=False)
    _render(body_html, {}, html_part=True)
    if "\r" in subject or "\n" in subject:
        raise HTTPException(status_code=422, detail="Subject cannot contain line breaks")


@router.get("", response_model=list[EmailTemplateResponse])
async def list_templates(
    email: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_agent(email, db)
    result = await db.execute(
        select(EmailTemplate).where(EmailTemplate.agent_email == email)
        .order_by(EmailTemplate.updated_at.desc(), EmailTemplate.id).limit(limit)
    )
    return result.scalars().all()


@router.get("/page")
async def list_templates_page(email: str, limit: int = Query(50, ge=1, le=200),
                              cursor: str | None = Query(None, max_length=2048), db: AsyncSession = Depends(get_db)):
    await _ensure_agent(email, db)
    scope = f"/v1/agents/{email}/templates"
    query = select(EmailTemplate).where(EmailTemplate.agent_email == email).order_by(EmailTemplate.updated_at.desc(), EmailTemplate.id).limit(limit + 1)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            updated_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(EmailTemplate.updated_at < updated_at, and_(EmailTemplate.updated_at == updated_at, EmailTemplate.id > item_id)))
    rows = list((await db.execute(query)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].updated_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [EmailTemplateResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}


@router.post("", response_model=EmailTemplateResponse, status_code=201, dependencies=[Depends(require_write)])
async def create_template(email: str, payload: EmailTemplateCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_agent(email, db)
    if not payload.body_text and not payload.body_html:
        raise HTTPException(status_code=422, detail="At least one of body_text or body_html is required")
    _validate_sources(payload.subject, payload.body_text, payload.body_html)
    now = datetime.datetime.utcnow()
    item = EmailTemplate(
        id=f"tpl_{uuid.uuid4().hex[:16]}", agent_email=email, name=payload.name, alias=payload.alias,
        subject=payload.subject, body_text=payload.body_text, body_html=payload.body_html,
        created_at=now, updated_at=now,
    )
    db.add(item)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A template with this name already exists in this inbox") from None
    await db.refresh(item)
    return item


@router.post("/preview")
async def preview_template(email: str, payload: EmailTemplatePreviewRequest, db: AsyncSession = Depends(get_db)):
    await _ensure_agent(email, db)
    if not payload.body_text and not payload.body_html:
        raise HTTPException(status_code=422, detail="At least one of body_text or body_html is required")
    return _preview(payload.subject, payload.body_text, payload.body_html, payload.data)


@router.post("/{template_id}/preview")
async def preview_saved_template(
    email: str,
    template_id: str,
    payload: EmailTemplateRenderData,
    db: AsyncSession = Depends(get_db),
):
    item = await _get_template(email, template_id, db)
    return _preview(item.subject, item.body_text, item.body_html, payload.data)


@router.get("/{template_id}", response_model=EmailTemplateResponse)
async def get_template(email: str, template_id: str, db: AsyncSession = Depends(get_db)):
    return await _get_template(email, template_id, db)


@router.patch("/{template_id}", response_model=EmailTemplateResponse, dependencies=[Depends(require_write)])
async def update_template(
    email: str, template_id: str, payload: EmailTemplateUpdate, db: AsyncSession = Depends(get_db),
):
    item = await _get_template(email, template_id, db)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one field to update")
    if "name" in changes and not changes["name"]:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    if changes.get("alias") == "":
        changes["alias"] = None
    if "subject" in changes and (not changes["subject"] or "\r" in changes["subject"] or "\n" in changes["subject"]):
        raise HTTPException(status_code=422, detail="Subject must be non-empty and cannot contain line breaks")
    _validate_sources(changes.get("subject", item.subject), changes.get("body_text", item.body_text), changes.get("body_html", item.body_html))
    for field, value in changes.items():
        if value is not None:
            setattr(item, field, value)
    if not item.body_text and not item.body_html:
        raise HTTPException(status_code=422, detail="At least one of body_text or body_html is required")
    item.updated_at = datetime.datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A template with this name already exists in this inbox") from None
    await db.refresh(item)
    return item


@router.delete("/{template_id}", status_code=204, dependencies=[Depends(require_write)])
async def delete_template(email: str, template_id: str, db: AsyncSession = Depends(get_db)):
    item = await _get_template(email, template_id, db)
    await db.delete(item)
    await db.commit()
    return Response(status_code=204)
