from typing import Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from core.storage.cache import audit
from core.storage.db import CacheRow, PaperRow, ReviewRow, get_engine


def register_paper(sha: str, filename: str, title: str, status: str) -> None:
    engine = get_engine()
    if engine is None:
        return
    stmt = insert(PaperRow).values(sha256=sha, filename=filename, title=title, status=status)
    stmt = stmt.on_conflict_do_update(index_elements=["sha256"],
                                      set_={"filename": filename, "title": title, "status": status})
    with engine.begin() as conn:
        conn.execute(stmt)


def delete_paper_data(sha: str, cache=None) -> Optional[dict]:
    """Remove everything stored about a paper: cached results, review drafts and the paper row.

    The PDF itself is never stored. A short audit entry (hash and counts only, no content) is kept.
    Returns row counts, or None when no database is configured (memory cache is still cleared).
    """
    if cache is not None:
        cache._mem = {k: v for k, v in cache._mem.items() if not k.startswith(sha)}
    engine = get_engine()
    if engine is None:
        return None
    with engine.begin() as conn:
        counts = {
            "cache_rows": conn.execute(delete(CacheRow).where(CacheRow.paper_sha == sha)).rowcount,
            "reviews": conn.execute(delete(ReviewRow).where(ReviewRow.paper_sha == sha)).rowcount,
            "papers": conn.execute(delete(PaperRow).where(PaperRow.sha256 == sha)).rowcount,
        }
    audit("paper_deleted", sha, counts)
    return counts
