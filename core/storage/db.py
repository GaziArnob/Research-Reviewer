import sys
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class PaperRow(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CacheRow(Base):
    __tablename__ = "analysis_cache"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    paper_sha: Mapped[str] = mapped_column(String(64), index=True)
    step: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReviewRow(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_sha: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSONB)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(120), default="local")
    action: Mapped[str] = mapped_column(String(60))
    paper_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


@lru_cache(maxsize=1)
def get_engine() -> Optional[Engine]:
    """Engine for DATABASE_URL, or None when it is not configured or unreachable."""
    if not settings.database_url:
        return None
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True,
                               connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


def init_db() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    Base.metadata.create_all(engine)
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        ok = init_db()
        print("Tables created." if ok else "Database not reachable. Check DATABASE_URL in .env.")
        sys.exit(0 if ok else 1)
    print("Usage: python -m core.storage.db init")
