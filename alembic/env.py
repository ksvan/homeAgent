from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection

from alembic import context

# Ensure the project root is on sys.path so app.* imports work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.models import (  # noqa: E402, F401  — imported for side effects (metadata registration)
    ActionPolicy,
    AgentRunLog,
    ChannelMapping,
    ConversationMessage,
    ConversationSummary,
    DeviceSnapshot,
    EpisodicMemory,
    EventLog,
    Household,
    HouseholdProfile,
    PendingAction,
    Task,
    User,
    UserProfile,
)
from sqlmodel import SQLModel  # noqa: E402

# ---------------------------------------------------------------------------
# Which tables live in which database
# ---------------------------------------------------------------------------
DATABASES: dict[str, set[str]] = {
    "users": {"household", "user", "channelmapping", "task", "actionpolicy"},
    "memory": {
        "userprofile",
        "householdprofile",
        "episodicmemory",
        "conversationmessage",
        "conversationsummary",
    },
    "cache": {"devicesnapshot", "eventlog", "agentrunlog", "pendingaction"},
}

# Alembic branch labels must match the branch_labels in each migration file.
BRANCH_LABELS: dict[str, str] = {
    "users": "users_db",
    "memory": "memory_db",
    "cache": "cache_db",
}

# ---------------------------------------------------------------------------
# Standard Alembic boilerplate
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _filtered_metadata(table_names: set[str]) -> SQLModel.metadata.__class__:  # type: ignore[name-defined]
    """Return a MetaData containing only the tables that belong to one database."""
    from sqlalchemy import MetaData

    meta = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        if table.name in table_names:
            table.to_metadata(meta)
    return meta


def _enable_wal(connection: Connection) -> None:
    connection.execute(text("PRAGMA journal_mode=WAL"))


def _run_for_db(db_name: str, table_names: set[str]) -> None:
    """Run pending migrations for a single database."""
    settings = get_settings()
    db_path = settings.db_path(db_name)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    target_metadata = _filtered_metadata(table_names)
    url = f"sqlite:///{db_path}"
    connectable = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(connectable, "connect")
    def set_wal(dbapi_conn: object, _: object) -> None:
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

    # Let the migration script know which database it is targeting so it can
    # skip op.create_table() calls that don't belong to this database.
    os.environ["ALEMBIC_CURRENT_DB"] = db_name

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            include_name=lambda name, type_, parent_names: (
                name in table_names if type_ == "table" else True
            ),
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (for SQL generation)."""
    settings = get_settings()
    for db_name, table_names in DATABASES.items():
        url = f"sqlite:///{settings.db_path(db_name)}"
        os.environ["ALEMBIC_CURRENT_DB"] = db_name
        context.configure(
            url=url,
            target_metadata=_filtered_metadata(table_names),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against live database connections."""
    for db_name, table_names in DATABASES.items():
        _run_for_db(db_name, table_names)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
