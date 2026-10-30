"""api/db/session.py: Database connection, session management, and fallback handling.

Supports:
- PostgreSQL in production via DATABASE_URL
- SQLite fallback for local development and test environments
- Safe fallback if PostgreSQL is temporarily unreachable or unconfigured
- Configurable connection pool parameters (DATABASE_POOL_SIZE, DATABASE_MAX_OVERFLOW, DATABASE_SSL_MODE)
"""

from __future__ import annotations

import os
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from automation_framework.config.settings import REPORT_PATH
from automation_framework.utils.logger import logger

Base = declarative_base()

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker | None = None
_IS_FALLBACK: bool = False
_LAST_ERROR: str | None = None
_SCHEMA_INITIALIZED: bool = False
_SCHEMA_INIT_LOCK = threading.Lock()


def get_default_sqlite_url() -> str:
    REPORT_PATH.mkdir(parents=True, exist_ok=True)
    db_file = REPORT_PATH / "pione_testmate.db"
    return f"sqlite:///{db_file.as_posix()}"


def build_engine_and_factory() -> tuple[Engine, sessionmaker, bool, str | None]:
    """Create SQLAlchemy engine with retry and graceful SQLite fallback."""
    raw_url = os.getenv("DATABASE_URL", "").strip()
    pool_size = int(os.getenv("DATABASE_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("DATABASE_MAX_OVERFLOW", "10"))
    ssl_mode = os.getenv("DATABASE_SSL_MODE", "prefer").strip()

    is_fallback = False
    last_error = None

    if raw_url:
        connect_args: dict[str, Any] = {}
        if "postgresql" in raw_url:
            if ssl_mode and ssl_mode != "disable":
                connect_args["sslmode"] = ssl_mode

        try:
            engine_kwargs: dict[str, Any] = {
                "connect_args": connect_args,
                "pool_pre_ping": True,
            }
            if not raw_url.startswith("sqlite"):
                engine_kwargs["pool_size"] = pool_size
                engine_kwargs["max_overflow"] = max_overflow

            engine = create_engine(raw_url, **engine_kwargs)
            # Verify connectivity
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            logger.info(f"[DB] Successfully connected to configured database: {engine.dialect.name}")
            return engine, factory, False, None
        except Exception as exc:
            last_error = f"Configured DATABASE_URL failed: {exc}"
            logger.warning(f"[DB] {last_error}. Activating local SQLite fallback.")
            is_fallback = True

    # Fallback to local SQLite
    sqlite_url = get_default_sqlite_url()
    try:
        engine = create_engine(
            sqlite_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        logger.info(f"[DB] Using local SQLite database at {sqlite_url}")
        return engine, factory, is_fallback, last_error
    except Exception as exc:
        logger.exception(f"[DB] Failed to initialize SQLite database: {exc}")
        raise


def get_engine() -> Engine:
    global _ENGINE, _SESSION_FACTORY, _IS_FALLBACK, _LAST_ERROR
    if _ENGINE is None:
        _ENGINE, _SESSION_FACTORY, _IS_FALLBACK, _LAST_ERROR = build_engine_and_factory()
    return _ENGINE


def get_session_factory() -> sessionmaker:
    global _ENGINE, _SESSION_FACTORY, _IS_FALLBACK, _LAST_ERROR
    if _SESSION_FACTORY is None:
        _ENGINE, _SESSION_FACTORY, _IS_FALLBACK, _LAST_ERROR = build_engine_and_factory()
    return _SESSION_FACTORY


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Yield a transactional database session with automatic commit/rollback and retry."""
    # Repository functions are also used directly by the test suite and
    # background workers, where FastAPI's startup lifecycle is not guaranteed
    # to have run.  Ensure every supported database has the current model
    # tables before the first write instead of relying on a pre-existing local
    # SQLite file.
    init_db()
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute_with_retry(operation, max_retries: int = 3, base_delay: float = 0.1):
    """Execute a callable with retry on transient DB errors."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            with get_db_session() as session:
                return operation(session)
        except (OperationalError, DBAPIError) as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
            else:
                raise
        except Exception:
            raise
    raise last_exc or RuntimeError("Database operation exhausted retries")


def check_db_status() -> dict[str, Any]:
    """Return database connectivity, dialect, pool info, and fallback status."""
    global _IS_FALLBACK, _LAST_ERROR
    engine = get_engine()
    dialect = engine.dialect.name
    configured_url = bool(os.getenv("DATABASE_URL", "").strip())
    is_connected = False
    details = ""

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        is_connected = True
        details = "Connected to local SQLite store" if _IS_FALLBACK or dialect == "sqlite" else "Connected to PostgreSQL database"
    except Exception as exc:
        details = str(exc)

    return {
        "status": "connected" if is_connected else "disconnected",
        "connected": is_connected,
        "engine": "sqlite" if dialect == "sqlite" else "postgresql",
        "dialect": dialect,
        "is_fallback": _IS_FALLBACK,
        "fallback_active": _IS_FALLBACK,
        "database_url_configured": configured_url,
        "details": details,
        "last_warning": _LAST_ERROR,
    }


def init_db(engine: Engine | None = None) -> None:
    """Initialize all database tables from declarative metadata."""
    global _SCHEMA_INITIALIZED

    target_engine = engine or get_engine()
    # Import models to ensure they are registered with Base before the schema
    # is materialized.  A lock prevents concurrent first requests from racing
    # to create the same SQLite tables.
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_INITIALIZED and engine is None:
            return
        import api.db.models  # noqa: F401
        Base.metadata.create_all(bind=target_engine)
        if engine is None:
            _SCHEMA_INITIALIZED = True
        logger.info(f"[DB] Initialized database tables on dialect '{target_engine.dialect.name}'")
