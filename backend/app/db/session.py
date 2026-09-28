"""Engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base
from app.settings import settings

engine: Engine = create_engine(
    settings.database_url,
    future=True,
    # FastAPI serves requests on a threadpool; SQLite's default same-thread check
    # would reject those. Safe here because access is read-only after startup and
    # the single writer is the run creator, which holds a transaction.
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite ignores foreign keys unless asked. We rely on them for integrity."""
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Tables that are NOT rebuilt from source, because they are not derived from it.
# Everything else in this database can be reconstructed from data/*.csv and
# config/*.yaml; what a person typed cannot be.
PRESERVED_TABLES = {"task_state"}


def reset_schema() -> None:
    """Rebuild the schema from scratch, preserving human-owned tables.

    The database is a derived artifact, so there are no migrations to run and
    dropping everything is the simplest correct thing -- with one exception.
    Human state (who claimed a task, what they wrote on it) is the only content
    here that cannot be recomputed from the CSVs and the YAML, so it survives
    the rebuild. That asymmetry is the point rather than an oversight: a nightly
    re-evaluation must never erase the work people did during the day.

    A real deployment would use Alembic and would not drop anything.
    """
    droppable = [
        table
        for table in reversed(Base.metadata.sorted_tables)
        if table.name not in PRESERVED_TABLES
    ]
    Base.metadata.drop_all(engine, tables=droppable)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
