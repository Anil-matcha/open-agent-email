import uuid
import datetime
import asyncio
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AsyncSessionLocal, get_db
from app.models import Domain
from app.schemas import DomainCreate, DomainResponse
from app.security import Principal, require_admin
from app.services import domain_dns
from app.services.smtp_secrets import encrypt_secret
from app.services.webhook_events import enqueue_domain_webhook_event
from app.services.cursors import decode_cursor, encode_cursor
from app.services.idempotency import reserve_idempotency, complete_idempotency, release_idempotency_claim
from sqlalchemy import and_, or_

router = APIRouter(prefix="/v1/domains", tags=["Domains"], dependencies=[Depends(require_admin)])

@router.get("", response_model=List[DomainResponse])
async def list_domains(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    return result.scalars().all()


@router.get("/page")
async def list_domains_page(
    limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    scope = "/v1/domains"
    query = select(Domain).order_by(Domain.created_at.desc(), Domain.id.desc())
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(Domain.created_at < created_at, and_(Domain.created_at == created_at, Domain.id < item_id)))
    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [DomainResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}


@router.get("/{domain_name}", response_model=DomainResponse)
async def get_domain(domain_name: str, db: AsyncSession = Depends(get_db)):
    clean_domain = domain_name.strip().lower().rstrip(".")
    domain = await db.scalar(select(Domain).where(Domain.domain == clean_domain))
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain

@router.post("", response_model=DomainResponse, status_code=201)
async def create_domain(payload: DomainCreate, db: AsyncSession = Depends(get_db)):
    clean_domain = payload.domain.strip().lower().rstrip(".")
    if not domain_dns.valid_domain(clean_domain):
        raise HTTPException(status_code=400, detail="Invalid domain name format")

    existing = await db.execute(select(Domain).where(Domain.domain == clean_domain))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Domain {clean_domain} is already registered")

    private_pem, public_b64 = await asyncio.to_thread(domain_dns.generate_dkim_keypair)
    try:
        encrypted_key = encrypt_secret(private_pem, "domain DKIM private key")
    except RuntimeError as exc:
        # Never store a signing key in plaintext.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    records = domain_dns.expected_records(clean_domain, public_b64)

    new_domain = Domain(
        id=f"dom_{uuid.uuid4().hex[:12]}",
        domain=clean_domain,
        status="pending_dns",
        mx_record=records.mx,
        mx_status="pending",
        spf_record=records.spf,
        spf_status="pending",
        dkim_selector=domain_dns.DKIM_SELECTOR,
        dkim_record=records.dkim,
        dkim_status="pending",
        dmarc_record=records.dmarc,
        dmarc_status="pending",
        dkim_private_key=encrypted_key,
        created_at=datetime.datetime.utcnow()
    )
    db.add(new_domain)
    await db.commit()
    await db.refresh(new_domain)
    return new_domain

@router.post("/{domain_name}/verify", response_model=DomainResponse)
async def verify_domain(domain_name: str, db: AsyncSession = Depends(get_db)):
    clean_domain = domain_name.strip().lower().rstrip(".")
    res = await db.execute(select(Domain).where(Domain.domain == clean_domain))
    domain = res.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    if not domain.dkim_private_key:
        raise HTTPException(
            status_code=409,
            detail="This domain was registered with placeholder DKIM data; delete and register it again",
        )

    public_b64 = domain.dkim_record.partition("p=")[2].strip()
    mx, spf, dkim, dmarc = await asyncio.gather(
        asyncio.to_thread(domain_dns.check_mx, clean_domain),
        asyncio.to_thread(domain_dns.check_spf, clean_domain),
        asyncio.to_thread(domain_dns.check_dkim, clean_domain, domain.dkim_selector, public_b64),
        asyncio.to_thread(domain_dns.check_dmarc, clean_domain),
    )
    previous_status = domain.status
    previous_checks = (domain.mx_status, domain.spf_status, domain.dkim_status)
    domain.mx_status, domain.spf_status, domain.dkim_status, domain.dmarc_status = mx, spf, dkim, dmarc
    domain.status = domain_dns.overall_status(mx, spf, dkim)
    domain.last_checked_at = datetime.datetime.utcnow()

    # Emit only on a change: into verified, or out of verified / into a new mismatch. Repeated
    # verify calls that find the same state do not repeat the event.
    records = {"mx": mx, "spf": spf, "dkim": dkim, "dmarc": dmarc}
    if domain.status == "verified" and previous_status != "verified":
        event_type = "domain.sending_verified"
    elif domain.status != "verified" and (
        previous_status == "verified"
        or ("mismatch" in (mx, spf, dkim) and previous_checks != (mx, spf, dkim))
    ):
        event_type = "domain.sending_failed"
    else:
        event_type = None
    if event_type:
        await enqueue_domain_webhook_event(
            db, event_type=event_type, domain=domain.domain, status=domain.status,
            records=records, created_at=domain.last_checked_at,
        )
    await db.commit()
    await db.refresh(domain)
    return domain

@router.delete("/{domain_name}", status_code=204)
async def delete_domain(domain_name: str, request: Request,
                        principal: Principal = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    clean_domain = domain_name.strip().lower()
    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:domains:delete", key,
                {"domain": clean_domain, "body": (await request.body()).decode("latin1")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return Response(status_code=decision.response_status or 204,
                             headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            messages = {
                "conflict": "Idempotency-Key was already used with a different request",
                "in_progress": "Idempotency-Key request is still in progress; retry unchanged",
                "expired_in_progress": "Idempotency-Key request did not finish; operator recovery is required",
            }
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409,
                                detail=messages[decision.outcome])
    res = await db.execute(select(Domain).where(Domain.domain == clean_domain))
    domain = res.scalar_one_or_none()
    if not domain:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=404, detail="Domain not found")

    await db.delete(domain)
    if decision is not None:
        await complete_idempotency(db, decision, status=204, body=None)
    await db.commit()
    return Response(status_code=204)
