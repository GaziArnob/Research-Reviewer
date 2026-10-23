from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from webapp.settings import web

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WBase(DeclarativeBase):
    pass


class User(WBase):
    __tablename__ = "web_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    plan: Mapped[str] = mapped_column(String(20), default="free")
    default_venue: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Submission(WBase):
    """One paper under review. Results are stored per submission so that users never share data."""
    __tablename__ = "web_submissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("web_users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | arxiv
    source_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|rejected|failed
    progress: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    options: Mapped[dict] = mapped_column(Json, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(Json, nullable=True)
    qa: Mapped[list] = mapped_column(Json, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(WBase):
    """Audit trail: who did what, when. No manuscript content is stored here."""
    __tablename__ = "web_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    submission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[Optional[dict]] = mapped_column(Json, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(WBase):
    """A reviewer's verdict on one finding (useful or not). Used to measure which checks deserve trust."""
    __tablename__ = "web_feedback"
    __table_args__ = (UniqueConstraint("user_id", "submission_id", "finding_id", name="uq_feedback"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    submission_id: Mapped[int] = mapped_column(Integer, index=True)
    finding_id: Mapped[str] = mapped_column(String(16))
    module: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[int] = mapped_column(Integer)  # 1 useful, -1 not useful
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine: Optional[Engine] = None
_Session: Optional[sessionmaker] = None


def configure(url: Optional[str] = None, engine: Optional[Engine] = None) -> Engine:
    """Create the engine (once) and the tables. `url` defaults to WEBAPP_DATABASE_URL or DATABASE_URL."""
    global _engine, _Session
    if engine is None:
        url = url or web.database_url
        if not url:
            raise RuntimeError("No database configured: set DATABASE_URL (PostgreSQL) in .env.")
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    _engine = engine
    _Session = sessionmaker(engine, expire_on_commit=False)
    WBase.metadata.create_all(engine)
    return engine


def engine() -> Engine:
    if _engine is None:
        configure()
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        configure()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log_event(db: Session, user_id: Optional[int], action: str, submission_id: Optional[int] = None,
              detail: Optional[dict] = None) -> None:
    db.add(Event(user_id=user_id, submission_id=submission_id, action=action, detail=detail))
