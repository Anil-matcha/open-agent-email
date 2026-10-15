from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import Agent, Message, ReviewItem, SecurityEvent
from app.schemas import StatsResponse
from app.security import require_admin
from app.services.ws_manager import ws_manager

router = APIRouter(prefix="/v1/stats", tags=["Stats"], dependencies=[Depends(require_admin)])

@router.get("", response_model=StatsResponse)
async def get_overview_stats(db: AsyncSession = Depends(get_db)):
    agents_count = (await db.execute(select(func.count(Agent.id)))).scalar() or 0
    total_messages = (await db.execute(select(func.count(Message.id)))).scalar() or 0
    inbound_count = (await db.execute(select(func.count(Message.id)).where(Message.direction == "inbound"))).scalar() or 0
    outbound_count = (await db.execute(select(func.count(Message.id)).where(Message.direction == "outbound"))).scalar() or 0
    unread_messages = (await db.execute(
        select(func.count(Message.id)).where(
            Message.direction == "inbound",
            Message.is_read.is_(False),
        )
    )).scalar() or 0
    pending_reviews = (await db.execute(select(func.count(ReviewItem.id)).where(ReviewItem.status == "pending"))).scalar() or 0
    threats_count = (await db.execute(select(func.count(SecurityEvent.id)))).scalar() or 0
    active_ws = ws_manager.total_active_connections()

    return StatsResponse(
        total_agents=agents_count,
        total_messages=total_messages,
        inbound_count=inbound_count,
        outbound_count=outbound_count,
        unread_messages=unread_messages,
        pending_reviews=pending_reviews,
        threats_neutralized=threats_count,
        active_websockets=active_ws
    )
