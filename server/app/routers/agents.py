import uuid
import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.database import get_db
from app.models import Agent
from app.schemas import AgentCreate, AgentUpdate, AgentResponse
from app.services.ws_manager import ws_manager
from app.security import require_admin
from app.security import authenticate_token
from app.database import AsyncSessionLocal
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(prefix="/v1/agents", tags=["Agents"], dependencies=[Depends(require_admin)])

@router.get("", response_model=List[AgentResponse])
async def list_agents(include_deleted: bool = Query(False, description="Also list soft-deleted inboxes"),
                      db: AsyncSession = Depends(get_db)):
    query = select(Agent).order_by(Agent.created_at.desc())
    if not include_deleted:
        query = query.where(Agent.deleted_at.is_(None))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/page")
async def list_agents_page(
    include_deleted: bool = Query(False, description="Also list soft-deleted inboxes"),
    limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    scope = f"/v1/agents|include_deleted={include_deleted}"
    query = select(Agent).order_by(Agent.created_at.desc(), Agent.id.desc())
    if not include_deleted:
        query = query.where(Agent.deleted_at.is_(None))
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(Agent.created_at < created_at, and_(Agent.created_at == created_at, Agent.id < item_id)))
    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [AgentResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}

@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(agent_in: AgentCreate, db: AsyncSession = Depends(get_db)):
    # Check if email taken
    res = await db.execute(select(Agent).where(Agent.email == agent_in.email))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Agent with this email already exists")

    agent_id = f"ag_{uuid.uuid4().hex[:10]}"
    agent = Agent(
        id=agent_id,
        email=agent_in.email,
        display_name=agent_in.display_name or agent_in.email.split("@", 1)[0],
        description=agent_in.description or "",
        threat_sensitivity=agent_in.threat_sensitivity,
        hitl_enabled=agent_in.hitl_enabled,
        auto_reply_enabled=agent_in.auto_reply_enabled,
        auto_reply_prompt=agent_in.auto_reply_prompt or "You are an autonomous AI assistant.",
        status="active",
        created_at=datetime.datetime.utcnow(),
        updated_at=datetime.datetime.utcnow()
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent

@router.get("/{email}", response_model=AgentResponse)
async def get_agent(email: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Agent).where(Agent.email == email))
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@router.patch("/{email}", response_model=AgentResponse)
async def update_agent(email: str, update_in: AgentUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Agent).where(Agent.email == email))
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = update_in.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        setattr(agent, field, val)
    agent.updated_at = datetime.datetime.utcnow()

    await db.commit()
    await db.refresh(agent)
    return agent

@router.delete("/{email}", status_code=204)
async def delete_agent(email: str, purge: bool = Query(False, description="Permanently delete the inbox and all its data"),
                       db: AsyncSession = Depends(get_db)):
    """Soft-delete by default: the inbox stops receiving mail and is hidden, but can be restored."""
    res = await db.execute(select(Agent).where(Agent.email == email))
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if purge:
        await db.delete(agent)
    elif agent.deleted_at is None:
        agent.deleted_at = datetime.datetime.utcnow()
    await db.commit()
    return None


@router.post("/{email}/restore", response_model=AgentResponse)
async def restore_agent(email: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Agent).where(Agent.email == email))
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.deleted_at = None
    await db.commit()
    await db.refresh(agent)
    return agent

@router.websocket("/{email}/ws")
async def agent_websocket_endpoint(websocket: WebSocket, email: str):
    """
    Real-time inbound message push stream for an agent.
    Agents connect without needing public URLs or ngrok tunnels.
    """
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        token = websocket.query_params.get("api_key", "")
    async with AsyncSessionLocal() as db:
        try:
            principal = await authenticate_token(token, db)
        except HTTPException:
            await websocket.close(code=1008)
            return
    if not principal.is_admin and principal.agent_email != email:
        await websocket.close(code=1008)
        return
    await ws_manager.connect_agent(email, websocket)
    try:
        while True:
            # Keep-alive ping/pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect_agent(email, websocket)
    except Exception:
        ws_manager.disconnect_agent(email, websocket)
