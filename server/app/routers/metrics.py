"""Prometheus text-format metrics for alerting on queues, workers and SMTP intake."""
import datetime
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics_registry
from app.config import settings
from app.database import get_db
from app.models import OutboundJob, WebhookDelivery
from app.security import authenticate_token
from app.tenancy import DEFAULT_TENANT_ID

router = APIRouter(tags=["metrics"])

OUTBOX_STATUSES = ("pending", "processing", "complete", "failed", "uncertain", "blocked")
WEBHOOK_STATUSES = ("pending", "processing", "delivered", "failed")
SMTP_RESULTS = ("accepted", "rejected", "deferred", "scanner_unavailable")


async def require_metrics_access(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    scrape_token = settings.METRICS_TOKEN.strip()
    if scrape_token and hmac.compare_digest(token, scrape_token):
        return
    principal = await authenticate_token(token, db)
    # Counters and queue depths cover the whole server, so only the operator may read them.
    if not principal.is_admin or principal.tenant_id != DEFAULT_TENANT_ID:
        raise HTTPException(status_code=403, detail="Administrator or metrics token required")


class _Out:
    def __init__(self):
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_text: str) -> None:
        self.lines.append(f"# HELP {name} {help_text}")
        self.lines.append(f"# TYPE {name} {kind}")

    def sample(self, name: str, value: float, **labels: str) -> None:
        label_text = ""
        if labels:
            label_text = "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
        self.lines.append(f"{name}{label_text} {value:g}")


@router.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require_metrics_access)])
async def metrics(request: Request, db: AsyncSession = Depends(get_db)):
    out = _Out()
    now = datetime.datetime.utcnow()

    out.metric("oae_outbox_jobs", "gauge", "Outbound jobs by status.")
    rows = dict((await db.execute(select(OutboundJob.status, func.count()).group_by(OutboundJob.status))).all())
    for status in sorted(set(OUTBOX_STATUSES) | set(rows)):
        out.sample("oae_outbox_jobs", rows.get(status, 0), status=status)

    oldest_pending = await db.scalar(
        select(func.min(OutboundJob.available_at)).where(OutboundJob.status == "pending", OutboundJob.available_at <= now))
    out.metric("oae_outbox_oldest_pending_age_seconds", "gauge",
               "Age of the oldest job that is due but not yet claimed; 0 when none.")
    out.sample("oae_outbox_oldest_pending_age_seconds",
               max(0.0, (now - oldest_pending).total_seconds()) if oldest_pending else 0)

    oldest_processing = await db.scalar(
        select(func.min(OutboundJob.locked_at)).where(OutboundJob.status == "processing"))
    out.metric("oae_outbox_oldest_processing_age_seconds", "gauge",
               "Age of the longest-running claimed job; 0 when none.")
    out.sample("oae_outbox_oldest_processing_age_seconds",
               max(0.0, (now - oldest_processing).total_seconds()) if oldest_processing else 0)

    out.metric("oae_webhook_deliveries", "gauge", "Webhook deliveries by status.")
    hooks = dict((await db.execute(select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status))).all())
    for status in sorted(set(WEBHOOK_STATUSES) | set(hooks)):
        out.sample("oae_webhook_deliveries", hooks.get(status, 0), status=status)

    app = request.app
    outbound = getattr(app.state, "outbound_worker_task", None)
    webhook = getattr(app.state, "webhook_worker_task", None)
    smtp = getattr(app.state, "smtp_server", None)
    out.metric("oae_outbound_worker_up", "gauge", "1 when this process's outbound worker task is running.")
    out.sample("oae_outbound_worker_up", 1 if outbound and not outbound.done() else 0)
    out.metric("oae_webhook_worker_up", "gauge", "1 when this process's webhook worker task is running.")
    out.sample("oae_webhook_worker_up", 1 if webhook and not webhook.done() else 0)
    out.metric("oae_smtp_listener_up", "gauge", "1 when this process's SMTP listener is accepting connections.")
    out.sample("oae_smtp_listener_up", 1 if smtp and smtp.is_serving() else 0)

    beat = metrics_registry.gauge("outbound_worker_heartbeat_timestamp_seconds")
    if beat is not None:
        out.metric("oae_outbound_worker_heartbeat_age_seconds", "gauge",
                   "Seconds since this process's outbound worker last started a poll iteration.")
        out.sample("oae_outbound_worker_heartbeat_age_seconds", max(0.0, time.time() - beat))
    out.metric("oae_outbound_worker_iteration_errors_total", "counter", "Outbound worker loop iterations that raised.")
    out.sample("oae_outbound_worker_iteration_errors_total",
               sum(metrics_registry.counters("outbound_worker_iteration_errors_total").values()))

    import smtp_receiver  # top-level module next to run.py
    out.metric("oae_smtp_active_sessions", "gauge", "SMTP sessions currently open.")
    out.sample("oae_smtp_active_sessions", smtp_receiver._ACTIVE_SMTP_CONNECTIONS)
    out.metric("oae_smtp_sessions_total", "counter", "SMTP sessions admitted since start.")
    out.sample("oae_smtp_sessions_total", sum(metrics_registry.counters("smtp_sessions_total").values()))
    out.metric("oae_smtp_sessions_refused_total", "counter", "SMTP sessions refused because the connection cap was reached.")
    out.sample("oae_smtp_sessions_refused_total", sum(metrics_registry.counters("smtp_sessions_refused_total").values()))
    out.metric("oae_smtp_messages_total", "counter", "Inbound SMTP messages by outcome.")
    by_result = {dict(labels).get("result"): v for labels, v in metrics_registry.counters("smtp_messages_total").items()}
    for result in SMTP_RESULTS:
        out.sample("oae_smtp_messages_total", by_result.get(result, 0), result=result)

    return PlainTextResponse("\n".join(out.lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")
