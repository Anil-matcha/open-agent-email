"""Run Alembic migrations at startup (and from the CLI: `python -m app.migrate`).

Databases created before Alembic was introduced are brought to the baseline shape by the
legacy additive updates in `app.main`, stamped at the baseline revision, then upgraded.
Fresh databases are created from the current models and stamped at head.
All schema changes after the baseline must be written as migrations.
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

logger = logging.getLogger("open_agent_email.migrate")
BASELINE_REVISION = "0001"
_ROOT = Path(__file__).resolve().parent.parent


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    return config


async def database_state() -> str:
    """Return 'empty', 'legacy' (tables, no alembic_version) or 'versioned'."""
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    if "alembic_version" in tables:
        return "versioned"
    return "legacy" if "agents" in tables else "empty"


def _apply(state: str) -> None:
    config = _config()
    if state == "empty":
        command.stamp(config, "head")
        logger.info("New database stamped at the current migration head")
        return
    if state == "legacy":
        command.stamp(config, BASELINE_REVISION)
        logger.info("Legacy database stamped at baseline %s", BASELINE_REVISION)
    command.upgrade(config, "head")


async def run_migrations(state: str) -> None:
    # Alembic's env.py drives its own event loop, so run it off the server loop.
    await asyncio.to_thread(_apply, state)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migrations(asyncio.run(database_state())))
