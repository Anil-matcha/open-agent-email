from typing import Dict, List, Set
from fastapi import WebSocket
import json
import logging

logger = logging.getLogger("ws_manager")

class ConnectionManager:
    """
    Manages WebSocket connections for:
    1. Per-agent real-time inbound mail delivery (`/v1/agents/{email}/ws`)
    2. Global dashboard live event feed (`/v1/ws/live`)
    """

    def __init__(self):
        # agent_email -> set of active WebSockets
        self.agent_connections: Dict[str, Set[WebSocket]] = {}
        # global dashboard listeners
        self.broadcast_connections: Set[WebSocket] = set()

    async def connect_agent(self, email: str, websocket: WebSocket):
        await websocket.accept()
        # One live socket per inbox. A replacement is terminal for the older
        # client so its reconnect loop cannot steal the connection back.
        previous = self.agent_connections.pop(email, set())
        self.agent_connections[email] = {websocket}
        for old in previous:
            try:
                await old.close(code=4000, reason="replaced")
            except Exception as exc:
                logger.debug("Could not close replaced WebSocket for %s: %s", email, exc)
        logger.info(f"WebSocket connected for agent: {email}")

    def disconnect_agent(self, email: str, websocket: WebSocket):
        if email in self.agent_connections:
            self.agent_connections[email].discard(websocket)
            if not self.agent_connections[email]:
                del self.agent_connections[email]
        logger.info(f"WebSocket disconnected for agent: {email}")

    async def connect_broadcast(self, websocket: WebSocket):
        await websocket.accept()
        self.broadcast_connections.add(websocket)
        logger.info("WebSocket connected to global broadcast stream")

    def disconnect_broadcast(self, websocket: WebSocket):
        self.broadcast_connections.discard(websocket)
        logger.info("WebSocket disconnected from global broadcast stream")

    async def send_agent_message(self, email: str, event_type: str, data: dict):
        payload = json.dumps({"type": event_type, "data": data})
        if email in self.agent_connections:
            for ws in list(self.agent_connections[email]):
                try:
                    await ws.send_text(payload)
                except Exception as e:
                    logger.warning(f"Failed to send to agent {email} WS: {e}")
                    self.disconnect_agent(email, ws)

    async def broadcast_event(self, event_type: str, data: dict):
        payload = json.dumps({"type": event_type, "data": data})
        for ws in list(self.broadcast_connections):
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.warning(f"Failed to broadcast WS message: {e}")
                self.disconnect_broadcast(ws)

    def total_active_connections(self) -> int:
        agent_count = sum(len(conns) for conns in self.agent_connections.values())
        return agent_count + len(self.broadcast_connections)

ws_manager = ConnectionManager()
