"""Event log view over webhook deliveries (/v1/events)."""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WebhookDelivery
from app.routers import webhooks
from app.security import Principal, require_admin

router = APIRouter(prefix="/v1/events", tags=["Events"], dependencies=[Depends(require_admin)])


@router.get("")
async def list_events(agent_email: Optional[str] = Query(None, max_length=255),
                      event_type: Optional[str] = Query(None, min_length=1, max_length=64),
                      status: Optional[str] = Query(None),
                      since: Optional[datetime.datetime] = Query(None, description="Include events at or after this offset-qualified timestamp"),
                      until: Optional[datetime.datetime] = Query(None, description="Include events at or before this offset-qualified timestamp"),
                      limit: int = Query(50, ge=1, le=100),
                      cursor: Optional[str] = Query(None, max_length=2048),
                      db: AsyncSession = Depends(get_db)):
    page = await webhooks.page_deliveries(endpoint_id=None, status=status, event_type=event_type,
                                          agent_email=agent_email, since=since, until=until,
                                          limit=limit, cursor=cursor, db=db)
    return page


@router.get("/{event_id}")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.get(WebhookDelivery, event_id)
    if not item:
        raise HTTPException(status_code=404, detail="Event not found")
    if item.status == "expired":
        raise HTTPException(status_code=410, detail="Event has expired")
    return {"id": item.id, "endpoint_id": item.endpoint_id, "agent_email": item.agent_email,
            "event_type": item.event_type, "payload": item.payload, "status": item.status,
            "attempts": item.attempts, "last_error": item.last_error, "created_at": item.created_at,
            "delivered_at": item.delivered_at, "replay_count": item.replay_count}


@router.post("/{event_id}/redeliver", status_code=202)
async def redeliver_event(event_id: str, principal: Principal = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    return await webhooks.replay_delivery(event_id, principal=principal, db=db)
