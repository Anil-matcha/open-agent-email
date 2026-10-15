"""Alembic environment: async engine, URL taken from application settings."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (register all tables on Base.metadata)
from app.config import settings
from app.database import Base

target_metadata = Base.metadata


def _configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(_configure)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=settings.DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
