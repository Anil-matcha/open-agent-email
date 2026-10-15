from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
import asyncio

from app.config import settings
from app.database import engine, Base, AsyncSessionLocal
from app.services.ws_manager import ws_manager
from app.routers import agents, messages, reviews, domains, settings_router, api_keys, mcp, stats, outbox, webhooks, contacts, suppressions, public_unsubscribe, templates, ses_feedback, engagements, conversations, meta, events
from app.routers import account as account_router, starter_templates as starter_templates_router
from app.routers import public_review
from app.routers import attachment_downloads
from app.security import authenticate_token
from app.rate_limit import ApiRateLimitMiddleware
from app.request_limits import RequestBodyLimitMiddleware
from app.errors import install_error_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("open_agent_email")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: Create tables if not present
    logger.info("Initializing database schema...")
    from app.migrate import database_state, run_migrations
    migration_state = await database_state()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        def apply_additive_schema_updates(sync_conn):
            from sqlalchemy import inspect, text
            inspector = inspect(sync_conn)
            timestamp_type = "DATETIME" if sync_conn.dialect.name == "sqlite" else "TIMESTAMP"
            contact_columns = {column["name"] for column in inspector.get_columns("contacts")}
            if "import_batch_id" not in contact_columns:
                sync_conn.execute(text("ALTER TABLE contacts ADD COLUMN import_batch_id VARCHAR(64)"))
                sync_conn.execute(text("CREATE INDEX ix_contacts_import_batch_id ON contacts (import_batch_id)"))
            engagement_columns = {column["name"] for column in inspector.get_columns("contact_engagements")}
            if "due_notified_at" not in engagement_columns:
                sync_conn.execute(text(f"ALTER TABLE contact_engagements ADD COLUMN due_notified_at {timestamp_type}"))
            key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
            message_columns = {column["name"] for column in inspector.get_columns("messages")}
            if "agent_email" not in key_columns:
                sync_conn.execute(text("ALTER TABLE api_keys ADD COLUMN agent_email VARCHAR(255)"))
            if "expires_at" not in key_columns:
                sync_conn.execute(text(f"ALTER TABLE api_keys ADD COLUMN expires_at {timestamp_type}"))
            if "delivery_error" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN delivery_error TEXT"))
            if "provider_delivery_status" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN provider_delivery_status VARCHAR(32)"))
            if "provider_delivery_error" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN provider_delivery_error TEXT"))
            review_columns = {column["name"] for column in inspector.get_columns("review_items")}
            if "reviewed_by" not in review_columns:
                sync_conn.execute(text("ALTER TABLE review_items ADD COLUMN reviewed_by VARCHAR(128)"))
            if "raw_message_id" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN raw_message_id VARCHAR(998)"))
            if "idempotency_key" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN idempotency_key VARCHAR(128)"))
            if "request_hash" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN request_hash VARCHAR(64)"))
            if "auth_evidence" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN auth_evidence JSON"))
            if "inbound_dedup_hash" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN inbound_dedup_hash VARCHAR(64)"))
            if "labels" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN labels JSON NOT NULL DEFAULT '[]'"))
            if "deleted_at" not in message_columns:
                sync_conn.execute(text(f"ALTER TABLE messages ADD COLUMN deleted_at {timestamp_type}"))
                sync_conn.execute(text("CREATE INDEX ix_messages_deleted_at ON messages (deleted_at)"))
            if "scheduled_for" not in message_columns:
                sync_conn.execute(text(f"ALTER TABLE messages ADD COLUMN scheduled_for {timestamp_type}"))
            if "is_read" not in message_columns:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT FALSE"))
                sync_conn.execute(text("UPDATE messages SET is_read = TRUE WHERE direction = 'outbound'"))
            outbox_columns = {column["name"] for column in inspector.get_columns("outbound_jobs")}
            if "retried_by" not in outbox_columns:
                sync_conn.execute(text("ALTER TABLE outbound_jobs ADD COLUMN retried_by VARCHAR(128)"))
            if "retried_at" not in outbox_columns:
                sync_conn.execute(text(f"ALTER TABLE outbound_jobs ADD COLUMN retried_at {timestamp_type}"))
            retry_event_columns = {column["name"] for column in inspector.get_columns("outbound_retry_events")}
            if "previous_status" not in retry_event_columns:
                sync_conn.execute(text("ALTER TABLE outbound_retry_events ADD COLUMN previous_status VARCHAR(24)"))
            webhook_delivery_columns = {column["name"] for column in inspector.get_columns("webhook_deliveries")}
            for name, definition in {
                "lease_token": "VARCHAR(64)", "replayed_by": "VARCHAR(128)",
                "replayed_at": f"{timestamp_type}", "replay_count": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in webhook_delivery_columns:
                    sync_conn.execute(text(f"ALTER TABLE webhook_deliveries ADD COLUMN {name} {definition}"))
            webhook_endpoint_columns = {column["name"] for column in inspector.get_columns("webhook_endpoints")}
            if "event_types" not in webhook_endpoint_columns:
                # Existing endpoints receive every event to preserve their prior behavior.
                sync_conn.execute(text("ALTER TABLE webhook_endpoints ADD COLUMN event_types JSON NOT NULL DEFAULT '[\"*\"]'"))
            for name, definition in {
                "previous_signing_secret": "TEXT",
                "previous_secret_expires_at": timestamp_type,
            }.items():
                if name not in webhook_endpoint_columns:
                    sync_conn.execute(text(f"ALTER TABLE webhook_endpoints ADD COLUMN {name} {definition}"))
            if sync_conn.dialect.name == "postgresql":
                smtp_columns = {column["name"]: column for column in inspector.get_columns("smtp_configs")}
                if str(smtp_columns.get("password", {}).get("type", "")).upper() != "TEXT":
                    sync_conn.execute(text("ALTER TABLE smtp_configs ALTER COLUMN password TYPE TEXT"))
            message_indexes = {index["name"] for index in inspector.get_indexes("messages")}
            if "ix_messages_raw_message_id" not in message_indexes:
                sync_conn.execute(text("CREATE INDEX ix_messages_raw_message_id ON messages (raw_message_id)"))
            if "uq_messages_agent_idempotency_key" not in message_indexes:
                sync_conn.execute(text("CREATE UNIQUE INDEX uq_messages_agent_idempotency_key ON messages (agent_email, idempotency_key)"))
            if "uq_messages_agent_inbound_dedup_hash" not in message_indexes:
                sync_conn.execute(text("CREATE UNIQUE INDEX uq_messages_agent_inbound_dedup_hash ON messages (agent_email, inbound_dedup_hash)"))
            if "ix_messages_is_read" not in message_indexes:
                sync_conn.execute(text("CREATE INDEX ix_messages_is_read ON messages (is_read)"))
        await conn.run_sync(apply_additive_schema_updates)
    from sqlalchemy import select
    from app.models import SmtpConfig, WebhookEndpoint
    from app.services.smtp_secrets import (
        PREFIX, encrypt_password, decrypt_password, decrypt_with_current_key,
        encrypt_secret, decrypt_secret,
    )
    async with AsyncSessionLocal() as db:
        configs = (await db.execute(select(SmtpConfig))).scalars().all()
        for config in configs:
            if not config.password:
                continue
            if config.password.startswith(PREFIX):
                # Fail fast on missing/wrong keys and migrate values from the previous key.
                plaintext = decrypt_password(config.password)
                if decrypt_with_current_key(config.password) is None:
                    config.password = encrypt_password(plaintext)
            elif settings.SMTP_CONFIG_ENCRYPTION_KEY:
                # Upgrade legacy plaintext values in the same database transaction.
                config.password = encrypt_password(config.password)
            else:
                logger.warning("SMTP relay credential remains plaintext; configure SMTP_CONFIG_ENCRYPTION_KEY to protect it")
        endpoints = (await db.execute(select(WebhookEndpoint))).scalars().all()
        for endpoint in endpoints:
            for attribute, purpose in (
                ("signing_secret", "webhook signing secret"),
                ("previous_signing_secret", "previous webhook signing secret"),
            ):
                stored = getattr(endpoint, attribute)
                if not stored:
                    continue
                plaintext = decrypt_secret(stored, purpose)
                if decrypt_with_current_key(stored) is None:
                    setattr(endpoint, attribute, encrypt_secret(plaintext, purpose))
        await db.commit()
    await run_migrations(migration_state)
    logger.info("Database schema ready.")

    from app.services.outbound_worker import run_outbound_worker
    from app.services.webhook_delivery import run_webhook_worker
    from app.services.contact_due_webhooks import run_contact_due_sweeper
    outbound_task = asyncio.create_task(run_outbound_worker(), name="outbound-mail-worker")
    app.state.outbound_worker_task = outbound_task
    webhook_task = asyncio.create_task(run_webhook_worker(), name="webhook-delivery-worker")
    app.state.webhook_worker_task = webhook_task
    contact_due_task = asyncio.create_task(run_contact_due_sweeper(), name="contact-due-webhook-sweeper")
    app.state.contact_due_worker_task = contact_due_task
    from app.services.review_expiry import run_review_expiry_sweeper
    review_expiry_task = asyncio.create_task(run_review_expiry_sweeper(), name="review-expiry-sweeper")
    app.state.review_expiry_task = review_expiry_task
    from app.services.message_purge import run_trash_janitor
    trash_janitor_task = asyncio.create_task(run_trash_janitor(), name="trash-retention-janitor")
    app.state.trash_janitor_task = trash_janitor_task
    from app.services.idempotency import run_idempotency_sweeper
    idempotency_task = asyncio.create_task(run_idempotency_sweeper(), name="idempotency-retention-sweeper")
    app.state.idempotency_sweeper_task = idempotency_task

    # 2. Start inbound SMTP server daemon on port 2525
    smtp_server = None
    try:
        from smtp_receiver import start_smtp_server
        smtp_server = await start_smtp_server()
        logger.info("Inbound SMTP listener active on port 2525.")
    except Exception as e:
        logger.warning(f"Could not bind SMTP port 2525 on startup: {e}")
    app.state.smtp_server = smtp_server

    yield

    # 3. Shutdown
    if smtp_server:
        logger.info("Shutting down inbound SMTP listener...")
        smtp_server.close()
        await smtp_server.wait_closed()

    outbound_task.cancel()
    try:
        await outbound_task
    except asyncio.CancelledError:
        pass

    webhook_task.cancel()
    try:
        await webhook_task
    except asyncio.CancelledError:
        pass

    contact_due_task.cancel()
    try:
        await contact_due_task
    except asyncio.CancelledError:
        pass

    review_expiry_task.cancel()
    try:
        await review_expiry_task
    except asyncio.CancelledError:
        pass

    trash_janitor_task.cancel()
    try:
        await trash_janitor_task
    except asyncio.CancelledError:
        pass

    idempotency_task.cancel()
    try:
        await idempotency_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down database connection...")
    await engine.dispose()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="The Open-Source Email Gateway & Authenticated Inboxes for AI Agents and Autonomous Systems.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

install_error_handlers(app)

# CORS setup
# Register rate limiting before CORS so CORS headers are still added to 429 replies.
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.HTTP_MAX_REQUEST_BYTES,
)
app.add_middleware(
    ApiRateLimitMiddleware,
    requests=settings.API_RATE_LIMIT_REQUESTS,
    window_seconds=settings.API_RATE_LIMIT_WINDOW_SECONDS,
    max_clients=settings.API_RATE_LIMIT_MAX_CLIENTS,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Live Event WebSocket for Dashboard
@app.websocket("/v1/ws/live")
async def live_dashboard_websocket(websocket: WebSocket):
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        token = websocket.query_params.get("api_key", "")
    async with AsyncSessionLocal() as db:
        try:
            principal = await authenticate_token(token, db)
        except Exception:
            await websocket.close(code=1008)
            return
    if not principal.is_admin:
        await websocket.close(code=1008)
        return
    await ws_manager.connect_broadcast(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect_broadcast(websocket)
    except Exception:
        ws_manager.disconnect_broadcast(websocket)

# Routers
app.include_router(stats.router)
app.include_router(outbox.router)
app.include_router(webhooks.router)
app.include_router(events.router)
app.include_router(agents.router)
app.include_router(contacts.router)
app.include_router(contacts.import_router)
app.include_router(engagements.router)
app.include_router(suppressions.router)
app.include_router(public_unsubscribe.router)
app.include_router(public_review.router)
app.include_router(ses_feedback.router)
app.include_router(templates.router)
app.include_router(messages.router)
app.include_router(messages.test_router)
app.include_router(attachment_downloads.router)
app.include_router(conversations.router)
app.include_router(meta.router)
app.include_router(reviews.router)
app.include_router(domains.router)
app.include_router(settings_router.router)
app.include_router(api_keys.router)
app.include_router(api_keys.account_router)
app.include_router(mcp.router)
app.include_router(account_router.router)
app.include_router(starter_templates_router.router)

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "service": "open-agent-email",
        "version": settings.VERSION,
        "shared_domain": settings.SHARED_DOMAIN
    }


@app.get("/ready")
async def readiness_check():
    from sqlalchemy import func, select, text
    from app.models import OutboundJob, WebhookDelivery

    database_ready = False
    pending_jobs = None
    pending_webhooks = None
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            pending_jobs = await db.scalar(
                select(func.count()).select_from(OutboundJob).where(OutboundJob.status == "pending")
            )
            pending_webhooks = await db.scalar(
                select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == "pending")
            )
            database_ready = True
    except Exception:
        logger.exception("Readiness check could not access the database")

    smtp_server = getattr(app.state, "smtp_server", None)
    smtp_ready = bool(smtp_server and smtp_server.is_serving())
    worker = getattr(app.state, "outbound_worker_task", None)
    worker_ready = bool(worker and not worker.done())
    webhook_worker = getattr(app.state, "webhook_worker_task", None)
    webhook_worker_ready = bool(webhook_worker and not webhook_worker.done())
    ready = database_ready and smtp_ready and worker_ready and webhook_worker_ready
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "components": {
                "database": "ready" if database_ready else "unavailable",
                "smtp_listener": "ready" if smtp_ready else "unavailable",
                "outbound_worker": "ready" if worker_ready else "unavailable",
                "webhook_worker": "ready" if webhook_worker_ready else "unavailable",
            },
            "outbox_pending": pending_jobs,
            "webhooks_pending": pending_webhooks,
        },
    )

@app.get("/")
async def root():
    return {
        "message": "Welcome to Open Agent Email API",
        "docs": "/docs",
        "mcp": "/mcp/info",
        "health": "/health"
    }
