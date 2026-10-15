import asyncio
import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import SmtpConfig
from app.schemas import SmtpConfigUpdate, SmtpConfigResponse, SmtpTestRequest, SmtpTestResponse
from app.services.email_service import _send_smtp_sync
from app.security import require_admin
from app.services.smtp_secrets import decrypt_password, encrypt_password
from app.config import settings

router = APIRouter(prefix="/v1/settings", tags=["Settings"], dependencies=[Depends(require_admin)])

@router.get("/smtp", response_model=SmtpConfigResponse)
async def get_smtp_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpConfig).where(SmtpConfig.id == "default"))
    config = result.scalar_one_or_none()
    if not config:
        config = SmtpConfig(
            id="default",
            host="",
            port="587",
            username="",
            password="",
            use_tls=True,
            use_ssl=False,
            from_email="",
            enabled=False,
            updated_at=datetime.datetime.utcnow()
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return SmtpConfigResponse(
        id=config.id,
        host=config.host,
        port=str(config.port),
        username=config.username,
        password_configured=bool(config.password),
        password_encryption_enabled=bool(settings.SMTP_CONFIG_ENCRYPTION_KEY),
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        from_email=config.from_email,
        enabled=config.enabled,
        updated_at=config.updated_at
    )

@router.post("/smtp", response_model=SmtpConfigResponse)
async def update_smtp_config(payload: SmtpConfigUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpConfig).where(SmtpConfig.id == "default"))
    config = result.scalar_one_or_none()
    if not config:
        config = SmtpConfig(id="default")
        db.add(config)

    config.host = payload.host.strip()
    config.port = str(payload.port).strip()
    config.username = payload.username.strip()
    if payload.password is not None and payload.password != "":
        try:
            config.password = encrypt_password(payload.password)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    config.use_tls = payload.use_tls
    config.use_ssl = payload.use_ssl
    config.from_email = payload.from_email.strip()
    config.enabled = payload.enabled
    config.updated_at = datetime.datetime.utcnow()

    await db.commit()
    await db.refresh(config)

    return SmtpConfigResponse(
        id=config.id,
        host=config.host,
        port=str(config.port),
        username=config.username,
        password_configured=bool(config.password),
        password_encryption_enabled=bool(settings.SMTP_CONFIG_ENCRYPTION_KEY),
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        from_email=config.from_email,
        enabled=config.enabled,
        updated_at=config.updated_at
    )

@router.post("/smtp/test", response_model=SmtpTestResponse)
async def test_smtp_connection(payload: SmtpTestRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpConfig).where(SmtpConfig.id == "default"))
    config = result.scalar_one_or_none()
    if not config or not config.host:
        raise HTTPException(status_code=400, detail="SMTP relay host is not configured yet.")

    from_addr = config.from_email or "gateway@open-agent-email.dev"
    port_num = int(config.port) if str(config.port).isdigit() else 587

    test_subject = "Open Agent Email — Upstream SMTP Relay Test"
    test_body = (
        f"Congratulations!\n\n"
        f"Your upstream SMTP relay ({config.host}:{config.port}) is successfully connected and verified.\n"
        f"Outbound agent emails will be delivered over the internet through this relay.\n\n"
        f"Sent at: {datetime.datetime.utcnow().isoformat()} UTC\n"
        f"Gateway: Open Agent Email"
    )

    try:
        password = decrypt_password(config.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    success, message = await asyncio.to_thread(
        _send_smtp_sync,
        host=config.host,
        port=port_num,
        username=config.username,
        password=password,
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        from_addr=from_addr,
        to_addr=payload.test_recipient,
        subject=test_subject,
        body_text=test_body
    )

    return SmtpTestResponse(
        success=success,
        message=message if success else f"SMTP connection error: {message}"
    )
