from unittest.mock import AsyncMock, Mock

import pytest

from app.services.ws_manager import ConnectionManager


@pytest.mark.asyncio
async def test_new_agent_socket_closes_previous_with_replaced_code():
    manager = ConnectionManager()
    old = Mock()
    old.accept = AsyncMock()
    old.close = AsyncMock()
    new = Mock()
    new.accept = AsyncMock()
    new.close = AsyncMock()

    await manager.connect_agent("a@agents.local", old)
    await manager.connect_agent("a@agents.local", new)

    old.close.assert_awaited_once_with(code=4000, reason="replaced")
    assert manager.agent_connections["a@agents.local"] == {new}
    manager.disconnect_agent("a@agents.local", old)
    assert manager.agent_connections["a@agents.local"] == {new}


@pytest.mark.asyncio
async def test_close_all_sends_shutdown_code_to_every_socket_and_continues_past_errors():
    manager = ConnectionManager()
    sockets = []
    for _ in range(3):
        ws = Mock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        sockets.append(ws)
    await manager.connect_agent("a@agents.local", sockets[0])
    await manager.connect_agent("b@agents.local", sockets[1])
    await manager.connect_broadcast(sockets[2])
    sockets[0].close.side_effect = RuntimeError("already gone")

    await manager.close_all(code=1001, reason="shutting_down")

    for ws in sockets:
        ws.close.assert_awaited_once_with(code=1001, reason="shutting_down")
    assert manager.total_active_connections() == 0
