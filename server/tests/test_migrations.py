import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

import app.migrate as migrate
from app.database import Base


def _head():
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(migrate._config()).get_current_head()


def _versions(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("select version_num from alembic_version").fetchall()
    finally:
        con.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(migrate.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    return path


async def test_fresh_database_is_stamped_at_head(db_path):
    assert await migrate.database_state() == "empty"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    assert await migrate.database_state() == "legacy"
    await migrate.run_migrations("empty")
    assert _versions(db_path) == [(_head(),)]
    assert await migrate.database_state() == "versioned"


async def test_baseline_upgrade_from_nothing_builds_full_schema(db_path):
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "head")
    tables = set(inspect(create_engine(f"sqlite:///{db_path}")).get_table_names())
    assert set(Base.metadata.tables) <= tables


async def test_legacy_database_is_stamped_then_upgraded(db_path):
    # A pre-Alembic database has the baseline tables but no version table.
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), migrate.BASELINE_REVISION)
    con = sqlite3.connect(db_path)
    con.execute("drop table alembic_version")
    con.commit()
    con.close()
    assert await migrate.database_state() == "legacy"
    await migrate.run_migrations("legacy")
    assert _versions(db_path) == [(_head(),)]
    columns = {c["name"] for c in inspect(create_engine(f"sqlite:///{db_path}")).get_columns("agents")}
    assert "deleted_at" in columns


async def test_baseline_matches_models_no_drift(db_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"models drifted from migrations: {diff[:3]}"


async def test_review_link_migration_is_safe_when_table_already_exists(db_path):
    """Startup create_all can create the new table before migration 0003 runs on a legacy database."""
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "0002")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.tables["review_link_tokens"].create(engine)
    engine.dispose()
    await migrate.asyncio.to_thread(migrate.command.upgrade, migrate._config(), "head")
    inspector = inspect(create_engine(f"sqlite:///{db_path}"))
    assert "review_link_tokens" in inspector.get_table_names()
    assert {"review_notify_email", "review_expiry_hours", "review_expiry_action"} <= {
        c["name"] for c in inspector.get_columns("agents")}
