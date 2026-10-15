import datetime
import csv
import io
import json
import uuid
from email.utils import parseaddr
from typing import Any, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models import Agent, Contact, ContactConsentEvent, ContactEngagement, ContactImportBatch, RecipientSuppression
from app.schemas import ContactCreate, ContactResponse, ContactUpdate, ContactConsentWrite, ContactConsentHistoryResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator
from app.security import require_agent_access, require_write
from app.services.cursors import decode_cursor, encode_cursor
from app.security import Principal
from app.services.idempotency import reserve_idempotency, complete_idempotency, release_idempotency_claim


router = APIRouter(
    prefix="/v1/agents/{email}/contacts",
    tags=["Contacts"],
    dependencies=[Depends(require_agent_access)],
)

# This route mirrors the account-scoped hosted shape while keeping storage in the
# existing per-inbox Contact table. main.py registers this router separately.
import_router = APIRouter(tags=["Contacts"], dependencies=[Depends(require_write)])


class ContactImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(validation_alias=AliasChoices("address", "email"))
    display_name: str | None = Field(None, max_length=255)
    company: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=4000)
    metadata: dict = Field(default_factory=dict)
    suppressed: bool = False
    stage: Literal["new", "contacted", "waiting", "closed"] | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_import_email(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if len(value) > 320:
            raise ValueError("address must be at most 320 characters")
        if "<" in value or ">" in value:
            display_name, mailbox = parseaddr(value)
            if not mailbox or "<" not in value or ">" not in value:
                raise ValueError("address must be a valid email address or mailbox")
            value = mailbox
        return value.strip().lower()

    @field_validator("display_name", "company", "notes")
    @classmethod
    def trim_import_text(cls, value):
        return value.strip() if value is not None else None

    @field_validator("display_name")
    @classmethod
    def require_nonempty_display_name(cls, value):
        if value == "":
            raise ValueError("display_name cannot be empty")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_import_metadata(cls, value):
        if not isinstance(value, dict) or len(value) > 50:
            raise ValueError("Metadata must be a flat object with at most 50 keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 128:
                raise ValueError("Metadata keys must be nonempty strings of at most 128 UTF-8 bytes")
            if item is not None and not isinstance(item, (str, int, float, bool)):
                raise ValueError("Metadata values must be flat scalar values")
            if isinstance(item, str) and len(item.encode("utf-8")) > 4096:
                raise ValueError("Metadata string values must be at most 4,096 UTF-8 bytes")
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Metadata values must be valid JSON scalars") from exc
        if len(serialized.encode("utf-8")) > 16 * 1024:
            raise ValueError("Metadata must be at most 16 KiB")
        return value


class ContactImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_email: str = Field(..., min_length=3, max_length=255)
    contacts: list[Any] = Field(..., min_length=1, max_length=1000)
    on_conflict: str = Field("merge", pattern="^(merge|skip)$")
    stage: Literal["new", "contacted", "waiting", "closed"] | None = None

    @field_validator("agent_email", mode="before")
    @classmethod
    def normalize_agent_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


@import_router.post("/v1/contacts/import")
async def import_contacts_json(request: Request,
                               payload: ContactImportRequest,
                               principal: Principal = Depends(require_write),
                               db: AsyncSession = Depends(get_db)):
    """Import structured contacts into one explicitly selected self-hosted inbox."""
    email = payload.agent_email
    if not principal.is_admin and principal.agent_email != email:
        raise HTTPException(status_code=403, detail="Token is not authorized for this agent inbox")
    await _ensure_agent_exists(email, db)
    raw_body = await request.body()
    if len(raw_body) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="JSON import must be at most 20 MB")

    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:contacts:json-import", key,
                {"agent_email": email, "body": raw_body.decode("latin1")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 200,
                                content=decision.response_body,
                                headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            detail = "Idempotency-Key was already used with a different request" if decision.outcome == "conflict" else "Idempotency-Key request is still in progress; retry unchanged"
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409, detail=detail)

    batch_id = f"cij_{uuid.uuid4().hex[:16]}"
    now = datetime.datetime.utcnow()
    result_rows = []
    created_ids = []
    counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    for index, raw_row in enumerate(payload.contacts, start=1):
        address = (raw_row.get("address", raw_row.get("email"))
                   if isinstance(raw_row, dict) else None)
        try:
            row = ContactImportRow.model_validate(raw_row)
            address = str(row.email)
        except ValidationError as exc:
            counts["failed"] += 1
            result_rows.append({"index": index - 1, "row": index, "address": address,
                                "email": address, "status": "failed", "reason": "invalid_row"})
            continue

        try:
            async with db.begin_nested():
                contact = (await db.execute(select(Contact).where(
                    Contact.agent_email == email, Contact.email == address
                ))).scalar_one_or_none()
                if contact is not None and payload.on_conflict == "skip":
                    counts["skipped"] += 1
                    result_rows.append({"index": index - 1, "row": index, "address": address,
                                        "email": address, "status": "skipped", "reason": "already_exists"})
                    continue

                created = contact is None
                if contact is None:
                    contact = Contact(
                        id=f"ct_{uuid.uuid4().hex[:12]}", agent_email=email, email=address,
                        name=(row.display_name or address), company=row.company or "", notes=row.notes or "",
                        metadata_json=dict(row.metadata), import_batch_id=batch_id,
                        created_at=now, updated_at=now)
                    db.add(contact)
                    created_ids.append(contact.id)
                else:
                    if row.display_name is not None:
                        contact.name = row.display_name
                    if row.company is not None:
                        contact.company = row.company
                    if row.notes is not None:
                        contact.notes = row.notes
                    if row.metadata:
                        contact.metadata_json = {**(contact.metadata_json or {}), **row.metadata}
                    contact.updated_at = now
                suppression = (await db.execute(select(RecipientSuppression).where(
                    RecipientSuppression.agent_email == email,
                    RecipientSuppression.email == address
                ))).scalar_one_or_none()
                if row.suppressed:
                    if suppression is None:
                        db.add(RecipientSuppression(
                            id=f"sp_{uuid.uuid4().hex[:12]}", agent_email=email,
                            email=address, reason="contact_import", created_at=now))
                suppression_active = row.suppressed or suppression is not None
                stage = row.stage if row.stage is not None else payload.stage
                effective_stage = None
                if stage is not None:
                    engagement = (await db.execute(select(ContactEngagement).where(
                        ContactEngagement.agent_email == email,
                        ContactEngagement.contact_id == contact.id
                    ))).scalar_one_or_none()
                    if engagement is None:
                        engagement = ContactEngagement(
                            id=f"eng_{uuid.uuid4().hex[:20]}", agent_email=email,
                            contact_id=contact.id, stage=stage, created_at=now, updated_at=now)
                        db.add(engagement)
                    effective_stage = engagement.stage
                await db.flush()
            status = "created" if created else "updated"
            counts[status] += 1
            result_rows.append({"index": index - 1, "row": index, "address": address,
                                "email": address, "status": status,
                                "contact_id": contact.id, "suppressed": suppression_active,
                                **({"stage": effective_stage} if effective_stage is not None else {})})
        except Exception:
            counts["failed"] += 1
            result_rows.append({"index": index - 1, "row": index, "address": address,
                                "email": address, "status": "failed", "reason": "write_failed"})

    batch = ContactImportBatch(id=batch_id, agent_email=email, contact_ids=created_ids, created_at=now)
    db.add(batch)
    response_body = {"batch_id": batch_id, **counts, "results": result_rows,
                     "notice": "Contacts are stored in the selected inbox. Suppression blocks outbound mail; this import never sends mail or records consent."}
    if decision is not None:
        await complete_idempotency(db, decision, status=200, body=response_body)
    await db.commit()
    if decision is not None:
        return JSONResponse(status_code=200, content=response_body)
    return response_body


async def _ensure_agent_exists(agent_email: str, db: AsyncSession) -> None:
    result = await db.execute(select(Agent.id).where(Agent.email == agent_email))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent inbox not found")


async def _get_contact(agent_email: str, contact_id: str, db: AsyncSession) -> Contact:
    # Preserve the original opaque-ID path while accepting the address-keyed
    # contract used by clients that address a contact by its canonical email.
    result = await db.execute(
        select(Contact).where(
            Contact.agent_email == agent_email,
            or_(Contact.id == contact_id, Contact.email == contact_id.strip().lower()),
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


async def _ensure_unique_email(agent_email: str, address: str, db: AsyncSession, exclude_id: str | None = None) -> None:
    query = select(Contact.id).where(Contact.agent_email == agent_email, Contact.email == address)
    if exclude_id:
        query = query.where(Contact.id != exclude_id)
    if (await db.execute(query)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A contact with this email already exists in this inbox")


@router.get("", response_model=List[ContactResponse])
async def list_contacts(email: str, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)):
    await _ensure_agent_exists(email, db)
    result = await db.execute(
        select(Contact).where(Contact.agent_email == email).order_by(Contact.name, Contact.email).limit(limit)
    )
    return result.scalars().all()


@router.get("/page")
async def list_contacts_page(email: str, limit: int = Query(50, ge=1, le=200),
                             cursor: str | None = Query(None, max_length=2048), db: AsyncSession = Depends(get_db)):
    await _ensure_agent_exists(email, db)
    scope = f"/v1/agents/{email}/contacts"
    query = select(Contact).where(Contact.agent_email == email).order_by(Contact.name, Contact.email).limit(limit + 1)
    if cursor:
        key, _ = decode_cursor(cursor, scope=scope)
        try:
            name, address = key.split("\0", 1)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(Contact.name > name, and_(Contact.name == name, Contact.email > address)))
    rows = list((await db.execute(query)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=f"{rows[-1].name}\0{rows[-1].email}", item_id=rows[-1].id) if has_more and rows else None
    return {"items": [ContactResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(email: str, contact_id: str, db: AsyncSession = Depends(get_db)):
    """Return a contact only when it belongs to the requested inbox."""
    return await _get_contact(email, contact_id, db)


@router.post("", response_model=ContactResponse, status_code=201, dependencies=[Depends(require_write)])
async def create_contact(email: str, payload: ContactCreate, request: Request,
                         principal: Principal = Depends(require_agent_access),
                         db: AsyncSession = Depends(get_db)):
    await _ensure_agent_exists(email, db)
    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        raw_body = await request.body()
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:contacts:create", key,
                {"agent_email": email, "body": raw_body.decode("latin1")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 201,
                                content=decision.response_body,
                                headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            messages = {
                "conflict": "Idempotency-Key was already used with a different request",
                "in_progress": "Idempotency-Key request is still in progress; retry unchanged",
                "expired_in_progress": "Idempotency-Key request did not finish; operator recovery is required",
            }
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409,
                                detail=messages[decision.outcome])
    try:
        await _ensure_unique_email(email, payload.email, db)
    except HTTPException:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise
    now = datetime.datetime.utcnow()
    contact = Contact(
        id=f"ct_{uuid.uuid4().hex[:12]}",
        agent_email=email,
        email=payload.email,
        name=payload.name,
        company=payload.company,
        notes=payload.notes,
        metadata_json=payload.metadata,
        created_at=now,
        updated_at=now,
    )
    db.add(contact)
    if decision is not None:
        await db.flush()
        response_body = jsonable_encoder(ContactResponse.model_validate(contact))
        await complete_idempotency(db, decision, status=201, body=response_body)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A contact with this email already exists in this inbox") from None
    await db.refresh(contact)
    if decision is not None:
        return JSONResponse(status_code=201, content=response_body)
    return contact


@router.patch("/{contact_id}", response_model=ContactResponse, dependencies=[Depends(require_write)])
async def update_contact(email: str, contact_id: str, payload: ContactUpdate, db: AsyncSession = Depends(get_db)):
    contact = await _get_contact(email, contact_id, db)
    changes = payload.model_dump(exclude_unset=True)
    if "email" in changes:
        await _ensure_unique_email(email, changes["email"], db, exclude_id=contact.id)
    for field, value in changes.items():
        if value is not None:
            setattr(contact, "metadata_json" if field == "metadata" else field, value)
    contact.updated_at = datetime.datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A contact with this email already exists in this inbox") from None
    await db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=204, dependencies=[Depends(require_write)])
async def delete_contact(email: str, contact_id: str, db: AsyncSession = Depends(get_db)):
    contact = await _get_contact(email, contact_id, db)
    await db.delete(contact)
    await db.commit()
    return Response(status_code=204)


@router.get("/{contact_id}/consent", response_model=ContactConsentHistoryResponse)
async def get_contact_consent(email: str, contact_id: str, db: AsyncSession = Depends(get_db)):
    contact = await _get_contact(email, contact_id, db)
    result = await db.execute(
        select(ContactConsentEvent).where(
            ContactConsentEvent.agent_email == email,
            ContactConsentEvent.contact_id == contact.id,
        ).order_by(ContactConsentEvent.id.desc()).limit(100)
    )
    events = result.scalars().all()
    return {"current": events[0].action if events else "unknown", "events": events}


@router.post("/{contact_id}/consent", response_model=ContactConsentHistoryResponse, status_code=201, dependencies=[Depends(require_write)])
async def record_contact_consent(email: str, contact_id: str, payload: ContactConsentWrite, db: AsyncSession = Depends(get_db)):
    contact = await _get_contact(email, contact_id, db)
    event = ContactConsentEvent(
        agent_email=email,
        contact_id=contact.id,
        action=payload.action,
        source=payload.source,
        evidence=payload.evidence,
        note=payload.note,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(event)
    await db.commit()
    return await get_contact_consent(email, contact_id, db)


@router.post("/imports", dependencies=[Depends(require_write)])
async def import_contacts(email: str, request: Request,
                          principal: Principal = Depends(require_agent_access),
                          db: AsyncSession = Depends(get_db)):
    """Import inert contacts from a bounded CSV with email,name,company,notes columns."""
    await _ensure_agent_exists(email, db)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1_000_000:
            raise HTTPException(status_code=413, detail="CSV file must be at most 1 MB")
        body.extend(chunk)
    try:
        raw_body = bytes(body)
        text = raw_body.decode("utf-8-sig")
        rows = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        raw_headers = rows.fieldnames or []
        normalized_headers = [str(header or "").strip().lower() for header in raw_headers]
        allowed_headers = {"email", "name", "company", "notes"}
        if len(set(normalized_headers)) != len(normalized_headers):
            raise HTTPException(status_code=400, detail="CSV headers must be unique after trimming and case normalization")
        unexpected_headers = set(normalized_headers) - allowed_headers
        if unexpected_headers:
            raise HTTPException(status_code=400, detail="CSV may contain only email, name, company, and notes columns")
        # DictReader otherwise silently overwrites duplicate header names.
        rows.fieldnames = normalized_headers
        headers = set(normalized_headers)
        if "email" not in headers:
            raise HTTPException(status_code=400, detail="CSV must include an email header")
        parsed = list(rows)
    except (UnicodeDecodeError, csv.Error):
        raise HTTPException(status_code=400, detail="CSV is malformed or is not UTF-8") from None
    if not parsed:
        raise HTTPException(status_code=400, detail="CSV must contain at least one contact row")
    if len(parsed) > 1000:
        raise HTTPException(status_code=413, detail="CSV may contain at most 1,000 data rows")

    # Normalize header casing/spacing and reject overlong records before writing anything.
    if any(None in row or any(value is None for value in row.values()) for row in parsed):
        raise HTTPException(status_code=400, detail="Every CSV row must have exactly the same number of columns as the header")
    normalized_rows = [{str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in parsed]
    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:contacts:imports:create", key,
                {"agent_email": email, "body": raw_body.decode("latin1")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 200,
                                content=decision.response_body,
                                headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            messages = {
                "conflict": "Idempotency-Key was already used with a different request",
                "in_progress": "Idempotency-Key request is still in progress; retry unchanged",
                "expired_in_progress": "Idempotency-Key request did not finish; operator recovery is required",
            }
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409,
                                detail=messages[decision.outcome])
    addresses = [row.get("email", "").strip().lower() for row in normalized_rows if row.get("email", "").strip()]
    existing = set((await db.execute(select(Contact.email).where(Contact.agent_email == email, Contact.email.in_(addresses)))).scalars().all())
    suppressed = set((await db.execute(select(RecipientSuppression.email).where(RecipientSuppression.agent_email == email, RecipientSuppression.email.in_(addresses)))).scalars().all())
    seen: set[str] = set()
    batch_id = f"ci_{uuid.uuid4().hex[:16]}"
    now = datetime.datetime.utcnow()
    outcomes = []
    created_ids = []
    new_contacts = []
    for index, row in enumerate(normalized_rows, start=2):
        address = row.get("email", "").lower()
        name = row.get("name", "") or address
        company, notes = row.get("company", ""), row.get("notes", "")
        try:
            validated = ContactCreate(email=address, name=name, company=company, notes=notes)
            address, name, company, notes = validated.email, validated.name, validated.company, validated.notes
        except ValidationError:
            outcomes.append({"row": index, "email": address, "status": "skipped", "reason": "invalid_row"})
            continue
        reason = None
        if address in seen:
            reason = "duplicate_in_file"
        elif address in existing:
            reason = "already_exists"
        elif address in suppressed:
            reason = "suppressed"
        if reason:
            outcomes.append({"row": index, "email": address, "status": "skipped", "reason": reason})
            continue
        seen.add(address)
        contact_id = f"ct_{uuid.uuid4().hex[:12]}"
        new_contacts.append(Contact(id=contact_id, agent_email=email, email=address, name=name, company=company, notes=notes, import_batch_id=batch_id, created_at=now, updated_at=now))
        created_ids.append(contact_id)
        outcomes.append({"row": index, "email": address, "status": "created"})
    batch = ContactImportBatch(id=batch_id, agent_email=email, contact_ids=created_ids, created_at=now)
    response_body = {"batch_id": batch_id, "created": len(created_ids), "skipped": len(outcomes) - len(created_ids), "results": outcomes,
                     "notice": "Imported contacts are records only. This operation does not send email or record consent."}
    db.add(batch)
    db.add_all(new_contacts)
    if decision is not None:
        await complete_idempotency(db, decision, status=200, body=response_body)
    await db.commit()
    if decision is not None:
        return JSONResponse(status_code=200, content=response_body)
    return response_body


@router.get("/imports")
async def list_contact_imports(email: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ContactImportBatch).where(ContactImportBatch.agent_email == email).order_by(ContactImportBatch.created_at.desc()).limit(50))
    batches = result.scalars().all()
    return [{"batch_id": batch.id, "created_at": batch.created_at, "created": len(batch.contact_ids)} for batch in batches]


@router.get("/imports/page")
async def list_contact_imports_page(email: str, limit: int = Query(50, ge=1, le=200),
                                    cursor: str | None = Query(None, max_length=2048),
                                    db: AsyncSession = Depends(get_db)):
    scope = f"/v1/agents/{email}/contacts/imports"
    query = select(ContactImportBatch).where(ContactImportBatch.agent_email == email)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Contact import cursor is invalid"}) from None
        query = query.where(or_(ContactImportBatch.created_at < created_at,
                                and_(ContactImportBatch.created_at == created_at, ContactImportBatch.id < item_id)))
    rows = list((await db.execute(query.order_by(ContactImportBatch.created_at.desc(), ContactImportBatch.id.desc()).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [{"batch_id": batch.id, "created_at": batch.created_at, "created": len(batch.contact_ids)} for batch in rows],
            "next_cursor": next_cursor}


@router.delete("/imports/{batch_id}", dependencies=[Depends(require_write)])
async def reverse_contact_import(email: str, batch_id: str, confirm: str = Query(""), db: AsyncSession = Depends(get_db)):
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Pass confirm=DELETE to reverse this import")
    result = await db.execute(select(ContactImportBatch).where(ContactImportBatch.agent_email == email, ContactImportBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Contact import batch not found")
    delete_query = delete(Contact).where(
        Contact.agent_email == email,
        Contact.import_batch_id == batch.id,
        Contact.updated_at == Contact.created_at,
    )
    # Structured imports can create an engagement in the same transaction as
    # the contact. Keep the contact if that engagement was subsequently edited;
    # deleting it would cascade away operator work. The distinct structured
    # batch prefix leaves the historical CSV reversal behavior unchanged.
    if batch.id.startswith("cij_"):
        edited_engagements = select(ContactEngagement.contact_id).where(
            ContactEngagement.agent_email == email,
            ContactEngagement.contact_id.in_(select(Contact.id).where(
                Contact.agent_email == email,
                Contact.import_batch_id == batch.id,
            )),
            or_(ContactEngagement.created_at > batch.created_at,
                ContactEngagement.updated_at > batch.created_at),
        )
        delete_query = delete_query.where(~Contact.id.in_(edited_engagements))
    deleted_result = await db.execute(delete_query)
    deleted = deleted_result.rowcount or 0
    retained = await db.scalar(
        select(func.count()).select_from(Contact).where(Contact.agent_email == email, Contact.import_batch_id == batch.id)
    ) or 0
    await db.delete(batch)
    await db.commit()
    return {"batch_id": batch_id, "deleted": deleted, "retained_after_edits": retained}
