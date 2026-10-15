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
