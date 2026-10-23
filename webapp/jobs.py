import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from core.ingest.loader import fetch_arxiv
from core.llm.router import get_llm
from core.pipeline import run_pipeline
from core.storage.cache import Cache
from webapp import db
from webapp.db import Submission, log_event, utcnow
from webapp.settings import web

log = logging.getLogger("webapp.jobs")

# Pipeline steps in order. The log is written when a step FINISHES, so the label shown is the one that starts next.
_ORDER = [("parse", "Checking for hidden instructions"), ("injection", "Checking structure"), ("preflight", "Reproducibility checks"),
          ("reproducibility", "Statistics checks"), ("statistics", "Writing checks"), ("writing", "Matching citations"),
          ("citations", "Verifying references (this takes a minute)"), ("references", "Searching related work"),
          ("related", "Summarising"), ("llm_understanding", "Extracting claims"), ("llm_claims", "Drafting evidence-linked comments"),
          ("llm_review", "Looking for weaknesses"), ("llm_critic", "Comparing with related work"), ("llm_novelty", "Finishing")]


def _pretty(entry: str) -> str:
    for key, nxt in _ORDER:
        if entry.startswith(key):
            return nxt
    return "Working"


def upload_path(sub_id: int) -> Path:
    return web.upload_dir / f"sub_{sub_id}.pdf"


_cache: Optional[Cache] = None


def cache() -> Cache:
    """PostgreSQL-backed step cache when available, memory otherwise (tests)."""
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def process_submission(sub_id: int) -> None:
    """Runs one paper through the pipeline. Safe to call from a worker thread; never raises."""
    try:
        with db.session() as s:
            sub = s.get(Submission, sub_id)
            if sub is None or sub.status not in ("queued", "running"):
                return
            sub.status, sub.started_at, sub.progress = "running", utcnow(), "Starting"
            opts, source, ref, filename, uid = dict(sub.options or {}), sub.source, sub.source_ref, sub.filename, sub.user_id

        if source == "arxiv":
            pdf, filename = fetch_arxiv(ref or "")
        else:
            pdf = upload_path(sub_id).read_bytes()

        def progress(entry: str) -> None:
            try:
                with db.session() as s2:
                    s2.get(Submission, sub_id).progress = _pretty(entry)
            except Exception:  # progress is cosmetic, never fail the job over it
                pass

        llm = get_llm() if opts.get("llm") else None
        result = run_pipeline(pdf, filename, online=bool(opts.get("online")), cache=cache(), llm=llm, progress=progress)

        with db.session() as s:
            sub = s.get(Submission, sub_id)
            sub.filename, sub.title, sub.sha256 = filename, result.title or filename, result.sha256
            sub.result = result.model_dump(mode="json")
            sub.status = "rejected" if result.status == "rejected" else "done"
            sub.progress, sub.finished_at = "", utcnow()
            log_event(s, uid, "review_finished", sub_id, {"status": sub.status, "findings": len(result.findings)})
    except Exception as exc:  # noqa: BLE001 - the job boundary: record the failure instead of crashing the worker
        log.exception("job %s failed", sub_id)
        with db.session() as s:
            sub = s.get(Submission, sub_id)
            if sub is not None:
                sub.status, sub.finished_at, sub.progress = "failed", utcnow(), ""
                sub.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                log_event(s, sub.user_id, "review_failed", sub_id, {"error": type(exc).__name__})
    finally:
        try:
            upload_path(sub_id).unlink(missing_ok=True)  # the PDF is never kept after processing
        except OSError:
            pass


class JobRunner:
    def __init__(self):
        self._pool: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()

    def submit(self, sub_id: int) -> None:
        if web.job_mode == "sync":
            process_submission(sub_id)
            return
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=web.job_workers, thread_name_prefix="review")
        self._pool.submit(process_submission, sub_id)

    def recover(self) -> int:
        """After a restart: re-queue waiting papers; mark interrupted ones failed (their PDF is gone)."""
        requeue = []
        with db.session() as s:
            for sub in s.scalars(select(Submission).where(Submission.status.in_(("queued", "running")))):
                if upload_path(sub.id).exists() or sub.source == "arxiv":
                    sub.status = "queued"
                    requeue.append(sub.id)
                else:
                    sub.status, sub.error, sub.finished_at = "failed", "Interrupted by a restart; please upload again.", utcnow()
        for sid in requeue:
            self.submit(sid)
        return len(requeue)


runner = JobRunner()
