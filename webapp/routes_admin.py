import requests
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from core.config import settings as core_settings
from core.llm.router import get_llm
from core.stats import wilson
from webapp.db import Event, Feedback, Submission, User, log_event
from webapp.deps import csrf_protect, flash, get_db, render, require_admin
from webapp.plans import PLANS
from webapp.services import month_start

router = APIRouter(prefix="/app/admin", dependencies=[Depends(require_admin)])


def _ollama_status() -> str:
    if core_settings.llm_provider != "ollama":
        return f"not configured (LLM_PROVIDER={core_settings.llm_provider})"
    try:
        r = requests.get(core_settings.ollama_host.rstrip("/") + "/api/tags", timeout=2)
        names = [m["name"] for m in r.json().get("models", [])]
        return "running, models: " + (", ".join(names) or "none pulled")
    except Exception:  # noqa: BLE001
        return "not reachable"


@router.get("")
def admin(request: Request, user: User = Depends(require_admin), s: Session = Depends(get_db)):
    users = list(s.execute(select(User, func.count(Submission.id)).outerjoin(Submission, Submission.user_id == User.id)
                           .group_by(User.id).order_by(User.created_at)).all())
    by_status = dict(s.execute(select(Submission.status, func.count()).group_by(Submission.status)).all())
    month = s.scalar(select(func.count(Submission.id)).where(Submission.created_at >= month_start())) or 0
    failed = list(s.scalars(select(Submission).where(Submission.status == "failed").order_by(Submission.created_at.desc()).limit(8)))
    events = list(s.scalars(select(Event).order_by(Event.created_at.desc()).limit(15)))
    try:
        s.execute(text("SELECT 1"))
        db_state = "connected"
    except Exception:  # noqa: BLE001
        db_state = "error"
    fb_rows = []
    for module, votes, useful in s.execute(select(Feedback.module, func.count(Feedback.id),
                                                  func.sum(case((Feedback.verdict > 0, 1), else_=0))).group_by(Feedback.module)).all():
        lo, hi = wilson(int(useful or 0), votes)
        fb_rows.append({"module": module, "votes": votes, "useful": int(useful or 0), "rate": round(100 * int(useful or 0) / votes),
                        "low": round(100 * lo), "high": round(100 * hi)})
    fb_rows.sort(key=lambda r: -r["votes"])
    system = {"Database": db_state, "Language model": f"{get_llm().name} / {getattr(get_llm(), 'model', '-')}",
              "Ollama": _ollama_status(), "Online checks default": "on" if core_settings.online_checks else "off",
              "OCR": "on" if core_settings.ocr_enabled else "off"}
    return render(request, "admin.html", users=users, by_status=by_status, month=month, failed=failed, events=events,
                  system=system, plan_keys=list(PLANS), feedback_rows=fb_rows)


@router.get("/feedback.csv")
def feedback_csv(s: Session = Depends(get_db)):
    """Anonymous export of all votes (no user, paper or finding text), for analysis outside the app."""
    import csv
    import io
    from fastapi.responses import Response
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["module", "kind", "verdict", "reason", "date"])
    for r in s.scalars(select(Feedback).order_by(Feedback.created_at)):
        w.writerow([r.module, r.kind, r.verdict, r.reason or "", r.created_at.date().isoformat()])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="feedback.csv"'})


@router.post("/users/{uid}/plan", dependencies=[Depends(csrf_protect)])
def set_plan(request: Request, uid: int, plan: str = Form(""), user: User = Depends(require_admin), s: Session = Depends(get_db)):
    target = s.get(User, uid)
    if target is None or plan not in PLANS:
        flash(request, "bad", "Unknown user or plan.")
    else:
        target.plan = plan
        log_event(s, user.id, "plan_changed", None, {"user_id": uid, "plan": plan})
        flash(request, "ok", f"{target.email} is now on the {PLANS[plan].name} plan.")
    return RedirectResponse("/app/admin", status_code=303)
