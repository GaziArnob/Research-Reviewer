from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import PipelineResult
from webapp import jobs
from webapp.db import Submission, User, log_event
from webapp.plans import plan_of


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def used_this_month(db: Session, user: User) -> int:
    return db.scalar(select(func.count(Submission.id)).where(Submission.user_id == user.id,
                                                              Submission.created_at >= month_start())) or 0


def quota_left(db: Session, user: User) -> Optional[int]:
    """None means unlimited."""
    limit = plan_of(user.plan).papers_per_month
    return None if limit == 0 else max(0, limit - used_this_month(db, user))


def owned_submission(db: Session, user: User, sub_id: int) -> Submission:
    """Every paper route goes through here: a user can only ever open their own papers (404, not 403, so ids leak nothing)."""
    sub = db.get(Submission, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404, detail="Paper not found")
    return sub


def result_of(sub: Submission) -> Optional[PipelineResult]:
    return PipelineResult(**sub.result) if sub.result else None


def delete_submission(db: Session, user: User, sub: Submission) -> None:
    """Removes the paper's results. Shared step-cache rows go too unless another paper has the same hash."""
    sha, sid = sub.sha256, sub.id
    db.delete(sub)
    db.flush()
    if sha and not db.scalar(select(func.count(Submission.id)).where(Submission.sha256 == sha)):
        from core.storage.papers import delete_paper_data
        delete_paper_data(sha, jobs.cache())
    jobs.upload_path(sid).unlink(missing_ok=True)
    log_event(db, user.id, "paper_deleted", None, {"paper_id": sid})
