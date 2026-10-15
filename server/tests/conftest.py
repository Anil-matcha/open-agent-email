import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="oae-tests-")
# TEST_DATABASE_URL (e.g. postgresql+asyncpg://user:pw@localhost:5433/oae_test) runs the suite on another database.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["SMTP_CONFIG_ENCRYPTION_KEY"] = "Zm9yLXRlc3RzLW9ubHktZmVybmV0LWtleS0wMDAwMDA="
os.environ["API_RATE_LIMIT_REQUESTS"] = "100000"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer test-admin-key"}


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_agent(client, email):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": email, "display_name": email})
    assert r.status_code == 201, r.text


async def make_key(client, email, scope, **extra):
    r = await client.post("/v1/api-keys", headers=ADMIN,
                          json={"name": f"{scope}-key", "agent_email": email, "scope": scope, **extra})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['key']}"}, body["id"]
