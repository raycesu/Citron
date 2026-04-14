import logging
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_logger = logging.getLogger(__name__)


def _resolve_database_url() -> str:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    if os.getenv("VERCEL"):
        # Vercel's filesystem is read-only except /tmp, which is ephemeral and
        # not shared across instances.  Data will be lost on every cold start.
        # Set DATABASE_URL to a persistent provider (e.g. Neon, PlanetScale,
        # Supabase) in your Vercel project environment variables to avoid this.
        _logger.warning(
            "DATABASE_URL is not set on Vercel. Falling back to ephemeral "
            "SQLite at /tmp/citron.db — data will not persist across "
            "deployments or cold starts. Set DATABASE_URL in your Vercel "
            "project environment variables."
        )
        return "sqlite:////tmp/citron.db"

    return "sqlite:///./citron.db"


DATABASE_URL = _resolve_database_url()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _migrate_add_missing_columns() -> None:
    """Add new columns to existing tables when absent (SQLite-safe, idempotent)."""
    migrations = [
        ("events", "last_seen_at", "DATETIME"),
        ("events", "consecutive_misses", "INTEGER NOT NULL DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
            except Exception:
                pass  # column already exists


def _migrate_postgres_events_location_to_text() -> None:
    """Widen events.location past VARCHAR(300) — scraped venues can exceed 300 chars."""
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE events ALTER COLUMN location TYPE TEXT"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            _logger.debug("events.location migration skipped: %s", exc)


def create_tables() -> None:
    from backend.models import Base
    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()
    _migrate_postgres_events_location_to_text()


def get_db():
    """FastAPI dependency – yields a database session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """Context manager for use outside of FastAPI dependency injection."""
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
