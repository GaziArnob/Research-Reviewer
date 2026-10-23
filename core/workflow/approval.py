from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import PipelineResult
from core.storage.cache import audit
from core.storage.db import ReviewRow, get_engine


def save_draft(result: PipelineResult) -> Optional[int]:
    """Store the AI draft as a new review version. Returns the review id, or None without a database."""
    engine = get_engine()
    if engine is None:
        return None
    with Session(engine) as s:
        version = (s.scalar(select(func.max(ReviewRow.version)).where(ReviewRow.paper_sha == result.sha256)) or 0) + 1
        row = ReviewRow(paper_sha=result.sha256, version=version, payload=result.model_dump(mode="json"))
        s.add(row)
        s.commit()
        audit("review_draft_saved", result.sha256, {"review_id": row.id, "version": version})
        return row.id


def approve(review_id: int, approved_by: str) -> bool:
    """Human approval gate: nothing is final until a named person approves the review."""
    engine = get_engine()
    if engine is None or not approved_by.strip():
        return False
    with Session(engine) as s:
        row = s.get(ReviewRow, review_id)
        if row is None:
            return False
        row.approved, row.approved_by = True, approved_by.strip()
        row.approved_at = datetime.now(timezone.utc)
        s.commit()
        audit("review_approved", row.paper_sha, {"review_id": review_id}, actor=approved_by.strip())
        return True
