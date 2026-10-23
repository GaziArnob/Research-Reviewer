from typing import Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from core.storage.db import AuditRow, CacheRow, get_engine


def make_key(paper_sha: str, step: str, version: str = "1", model: str = "none") -> str:
    return f"{paper_sha}:{step}:v{version}:{model}"


class Cache:
    """Step-result cache in PostgreSQL (JSONB). Falls back to memory when the DB is unavailable."""

    def __init__(self, engine: Optional[Engine] = None, use_db: bool = True):
        self.engine = engine if engine is not None else (get_engine() if use_db else None)
        self._mem: dict[str, dict] = {}

    @property
    def backend(self) -> str:
        return "postgresql" if self.engine is not None else "memory"

    def get(self, key: str) -> Optional[dict]:
        if self.engine is None:
            return self._mem.get(key)
        from sqlalchemy.orm import Session
        with Session(self.engine) as s:
            row = s.get(CacheRow, key)
            return row.payload if row else None

    def set(self, key: str, payload: dict, paper_sha: str, step: str) -> None:
        if self.engine is None:
            self._mem[key] = payload
            return
        stmt = insert(CacheRow).values(key=key, paper_sha=paper_sha, step=step, payload=payload)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"payload": payload})
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def delete_paper(self, paper_sha: str) -> None:
        """Deletion control: remove every cached result for one paper."""
        self._mem = {k: v for k, v in self._mem.items() if not k.startswith(paper_sha)}
        if self.engine is None:
            return
        from sqlalchemy import delete
        with self.engine.begin() as conn:
            conn.execute(delete(CacheRow).where(CacheRow.paper_sha == paper_sha))


def audit(action: str, paper_sha: Optional[str] = None, detail: Optional[dict] = None,
          actor: str = "local") -> None:
    engine = get_engine()
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(AuditRow.__table__.insert().values(actor=actor, action=action,
                                                         paper_sha=paper_sha, detail=detail))
