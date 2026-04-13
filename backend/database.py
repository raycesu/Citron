import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./citron.db")

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


def create_tables() -> None:
    from backend.models import Base
    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()


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
