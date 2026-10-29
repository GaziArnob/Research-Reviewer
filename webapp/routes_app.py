import json
import re
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.annotate import annotate_pdf
from core.assistant.agent import answer_question
from core.ingest.loader import sha256_of
from core.convert.bundle import SOURCE_SUFFIXES, ConversionError, convert_pdf, convert_source
from core.convert.templates import list_paper_templates
from core.ingest.loader import _ARXIV_ID
from core.llm.router import get_llm
from core.pipeline import get_parsed
from core.templates.loader import list_templates, load_template
from core.templates.render import build_form, to_docx, to_markdown
from webapp import jobs
from webapp.db import Event, Feedback, Submission, User, log_event, utcnow
from webapp.deps import csrf_protect, flash, get_db, render, require_user
from webapp.security import hash_password, password_problem, verify_password
from webapp.services import delete_submission, owned_submission, quota_left, result_of, used_this_month
from webapp.settings import web

router = APIRouter(prefix="/app", dependencies=[Depends(require_user)])

TABS = [("overview", "Overview"), ("findings", "Findings"), ("evidence", "Claims & evidence"),
        ("literature", "Literature"), ("aicheck", "AI check"), ("ask", "Ask the paper"), ("form", "Review form"), ("activity", "Activity")]
PER_PAGE = 15


def _llm_available() -> bool:
    return get_llm().name != "none"


# ---------------------------------------------------------------- dashboard
@router.get("")
def dashboard(request: Request, user: User = Depends(require_user), s: Session = Depends(get_db)):
    rows = list(s.scalars(select(Submission).where(Submission.user_id == user.id).order_by(Submission.created_at.desc()).limit(8)))
    counts = Counter(dict(s.execute(select(Submission.status, func.count()).where(Submission.user_id == user.id)
                                    .group_by(Submission.status)).all()))
    findings = {r.id: _counts(r) for r in rows}
    return render(request, "dashboard.html", rows=rows, counts=counts, findings=findings, quota=quota_left(s, user),
                  used=used_this_month(s, user), total=sum(counts.values()))


def _counts(sub: Submission) -> dict:
    fs = (sub.result or {}).get("findings", [])
    return {"major": sum(f["kind"] == "major" for f in fs), "minor": sum(f["kind"] == "minor" for f in fs)}


# ---------------------------------------------------------------- new review
@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), s: Session = Depends(get_db)):
    return render(request, "new.html", quota=quota_left(s, user), llm_ok=_llm_available(), venues=list_templates(),
                  max_mb=web.max_upload_mb, max_files=web.max_files_per_request)


@router.post("/new", dependencies=[Depends(csrf_protect)])
async def new_submit(request: Request, files: list[UploadFile] = File(default=[]), arxiv: str = Form(""),
                     online: str = Form(""), llm: str = Form(""), venue: str = Form(""),
                     user: User = Depends(require_user), s: Session = Depends(get_db)):
    def back(msg: str):
        flash(request, "bad", msg)
        return RedirectResponse("/app/new", status_code=303)

    files = [f for f in files if f and f.filename]
    ids = [m.group(1) for ref in re.split(r"[\s,;]+", arxiv.strip()) if ref and (m := _ARXIV_ID.search(ref))]
    if not files and not ids:
        return back("Add at least one PDF or arXiv id.")
    if len(files) + len(ids) > web.max_files_per_request:
        return back(f"Please add at most {web.max_files_per_request} papers at a time.")
    left = quota_left(s, user)
    if left is not None and len(files) + len(ids) > left:
        return back(f"Your plan has {left} paper(s) left this month.")

    options = {"online": online == "1", "llm": llm == "1" and _llm_available(), "venue": venue or user.default_venue}
    pending = []
    for f in files:
        data = await f.read(web.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > web.max_upload_mb * 1024 * 1024:
            return back(f"{f.filename} is larger than {web.max_upload_mb} MB.")
        if not data.startswith(b"%PDF"):
            return back(f"{f.filename} is not a PDF file.")
        pending.append(("upload", re.sub(r"[^\w.\- ]", "_", f.filename)[:200], None, data))
    pending += [("arxiv", f"arXiv {i}", i, None) for i in ids]

    created = []
    web.upload_dir.mkdir(parents=True, exist_ok=True)
    for source, name, ref, data in pending:
        sub = Submission(user_id=user.id, filename=name, source=source, source_ref=ref, options=options)
        s.add(sub)
        s.flush()
        if data is not None:
            jobs.upload_path(sub.id).write_bytes(data)
        log_event(s, user.id, "review_requested", sub.id, {"source": source, "online": options["online"], "llm": options["llm"]})
        created.append(sub.id)
    s.commit()  # the worker must see the rows
    for sid in created:
        jobs.runner.submit(sid)
    flash(request, "ok", f"{len(created)} paper(s) added. They are being processed now.")
    return RedirectResponse(f"/app/papers/{created[0]}" if len(created) == 1 else "/app/papers", status_code=303)


# ---------------------------------------------------------------- list
@router.get("/papers")
def papers(request: Request, status: str = "", q: str = "", page: int = 1, user: User = Depends(require_user),
           s: Session = Depends(get_db)):
    stmt = select(Submission).where(Submission.user_id == user.id)
    if status in ("queued", "running", "done", "rejected", "failed"):
        stmt = stmt.where(Submission.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Submission.title.ilike(like), Submission.filename.ilike(like)))
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    rows = list(s.scalars(stmt.order_by(Submission.created_at.desc()).limit(PER_PAGE).offset((page - 1) * PER_PAGE)))
    return render(request, "papers.html", rows=rows, counts={r.id: _counts(r) for r in rows}, status=status, q=q, page=page,
                  pages=max(1, -(-total // PER_PAGE)), total=total)


@router.get("/api/status")
def api_status(ids: str = "", user: User = Depends(require_user), s: Session = Depends(get_db)):
    wanted = [int(i) for i in ids.split(",") if i.strip().isdigit()][:50]
    rows = s.scalars(select(Submission).where(Submission.user_id == user.id, Submission.id.in_(wanted)))
    return [{"id": r.id, "status": r.status, "progress": r.progress} for r in rows]


# ---------------------------------------------------------------- one paper
def _paper(request, s, user, sub_id, tab, **extra):
    sub = owned_submission(s, user, sub_id)
    res = result_of(sub)
    ctx = dict(sub=sub, res=res, tabs=TABS, tab=tab, ready=sub.status == "done")
    ctx.update(extra)
    return ctx


@router.get("/papers/{sub_id}")
def overview(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    ctx = _paper(request, s, user, sub_id, "overview")
    res = ctx["res"]
    majors = [f for f in res.findings if f.kind == "major"] if res else []
    ctx.update(majors=majors[:5], counts=Counter(f.kind for f in res.findings) if res else {},
               rejected_reason=[f for f in res.findings if f.module == "validation"] if res else [])
    return render(request, "paper_overview.html", **ctx)


@router.get("/papers/{sub_id}/findings")
def findings(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    ctx = _paper(request, s, user, sub_id, "findings")
    res = ctx["res"]
    order = {"major": 0, "minor": 1, "question": 2, "strength": 3, "flag": 4}
    items = sorted(res.findings, key=lambda f: (order.get(f.kind, 9), f.module)) if res else []
    fb = {}
    for r in s.scalars(select(Feedback).where(Feedback.user_id == user.id, Feedback.submission_id == sub_id)):
        fb[r.finding_id] = r.verdict
        fb["r:" + r.finding_id] = r.reason or ""
    ctx.update(items=items, kinds=sorted({f.kind for f in items}, key=lambda k: order.get(k, 9)),
               states=sorted({f.status for f in items}), fb=fb)
    return render(request, "paper_findings.html", **ctx)


@router.post("/papers/{sub_id}/feedback", dependencies=[Depends(csrf_protect)])
async def feedback(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    sub = owned_submission(s, user, sub_id)
    body = await request.json()
    res = result_of(sub)
    finding = next((f for f in (res.findings if res else []) if f.id == str(body.get("finding_id", ""))), None)
    verdict = body.get("verdict")
    if finding is None or verdict not in (1, -1, 0):
        raise HTTPException(400, "Unknown finding or verdict")
    reason = body.get("reason") if verdict == -1 else None
    if reason not in (None, "wrong", "vague", "known", "other"):
        raise HTTPException(400, "Unknown reason")
    row = s.scalar(select(Feedback).where(Feedback.user_id == user.id, Feedback.submission_id == sub_id,
                                          Feedback.finding_id == finding.id))
    if verdict == 0:
        if row:
            s.delete(row)
    elif row:
        row.verdict = verdict
        row.reason = reason
    else:
        s.add(Feedback(user_id=user.id, submission_id=sub_id, finding_id=finding.id, module=finding.module,
                       kind=finding.kind, verdict=verdict, reason=reason))
    return {"ok": True}


@router.post("/papers/{sub_id}/annotate", dependencies=[Depends(csrf_protect)])
async def annotate(request: Request, sub_id: int, file: UploadFile = File(None), user: User = Depends(require_user),
                   s: Session = Depends(get_db)):
    """Highlights the evidence quotes in the user's own copy of the PDF. The tool deletes the PDF after the review,
    so the same file is uploaded again and checked against the stored hash. Nothing is kept."""
    sub = owned_submission(s, user, sub_id)
    res = result_of(sub)

    def back(msg: str):
        flash(request, "bad", msg)
        return RedirectResponse(f"/app/papers/{sub_id}/findings", status_code=303)

    if res is None or file is None or not file.filename:
        return back("Choose the PDF of this paper.")
    data = await file.read(web.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > web.max_upload_mb * 1024 * 1024 or not data.startswith(b"%PDF"):
        return back("That is not a PDF within the size limit.")
    if sha256_of(data) != sub.sha256:
        return back("This is not the same file that was reviewed (its hash differs). Upload the original PDF.")
    out, stats = annotate_pdf(data, res.findings)
    log_event(s, user.id, "annotated_pdf", sub.id, stats)
    name = re.sub(r"[^\w\-]", "_", (sub.title or sub.filename)[:50]) or "paper"
    return Response(out, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}_annotated.pdf"'})


@router.get("/papers/{sub_id}/evidence")
def evidence(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    return render(request, "paper_evidence.html", **_paper(request, s, user, sub_id, "evidence"))


@router.get("/papers/{sub_id}/literature")
def literature(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    ctx = _paper(request, s, user, sub_id, "literature")
    res = ctx["res"]
    related_findings = [f for f in res.findings if f.module in ("novelty", "literature")] if res else []
    trends = (res.trends if res else {}) or {}
    ctx.update(related_findings=related_findings, related_years=trends.get("related_by_year") or {},
               ref_years=trends.get("references_by_year") or {})
    return render(request, "paper_literature.html", **ctx)


@router.get("/papers/{sub_id}/aicheck")
def ai_check(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    from core.checks import ai_text
    ctx = _paper(request, s, user, sub_id, "aicheck")
    sub = ctx["sub"]
    parsed = get_parsed(jobs.cache(), sub.sha256) if sub.sha256 and sub.status == "done" else None
    report = None
    if parsed is not None:
        whole = ai_text.full_report(parsed.body_text)
        per_section = []
        for sec in parsed.sections:
            if sec.name.lower() in ("references", "front matter"):
                continue
            a = ai_text.analyse(sec.text)
            if a["words"] >= 150:
                per_section.append({"name": sec.name, "words": a["words"], "density": a["marker_density"], "hits": len(a["marker_sentences"])})
        report = dict(whole, per_section=per_section)
    return render(request, "paper_aicheck.html", **ctx, report=report)


@router.get("/papers/{sub_id}/ask")
def ask_page(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    sub = owned_submission(s, user, sub_id)
    return render(request, "paper_ask.html", **_paper(request, s, user, sub_id, "ask"), llm_ok=_llm_available(),
                  history=list(reversed(sub.qa or [])))


@router.post("/papers/{sub_id}/ask", dependencies=[Depends(csrf_protect)])
async def ask(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    sub = owned_submission(s, user, sub_id)
    body = await request.json()
    question = str(body.get("q", "")).strip()[:500]
    if not question:
        raise HTTPException(400, "Empty question")
    llm = get_llm()
    parsed = get_parsed(jobs.cache(), sub.sha256) if sub.sha256 and sub.status == "done" else None
    if llm.name == "none" or parsed is None:
        return HTMLResponse('<div class="alert a-warn"><div>Questions need a configured language model and a processed paper.</div></div>', 409)
    qa = answer_question(parsed, question, llm)
    entry = {"q": qa.question, "a": qa.answer, "mode": qa.mode, "warnings": qa.warnings,
             "cites": [{"id": c.chunk_id, "section": c.section, "page": c.page, "text": c.text} for c in qa.citations],
             "at": utcnow().isoformat()}
    sub.qa = (sub.qa or []) + [entry]
    log_event(s, user.id, "question_asked", sub.id)
    return render(request, "_qa_item.html", item=entry)


@router.get("/papers/{sub_id}/form")
def venue_form(request: Request, sub_id: int, venue: str = "", user: User = Depends(require_user), s: Session = Depends(get_db)):
    ctx = _paper(request, s, user, sub_id, "form")
    venues = list_templates()
    ids = [v.id for v in venues]
    venue = venue if venue in ids else (ctx["sub"].options or {}).get("venue") if (ctx["sub"].options or {}).get("venue") in ids else (
        user.default_venue if user.default_venue in ids else ids[0])
    if ctx["res"] is None:
        return render(request, "paper_form.html", **ctx, venues=venues, venue=venue, form=None, tpl=None)
    parsed = get_parsed(jobs.cache(), ctx["sub"].sha256) if ctx["sub"].sha256 else None
    tpl = load_template(venue)
    return render(request, "paper_form.html", **ctx, venues=venues, venue=venue, tpl=tpl,
                  form=build_form(tpl, ctx["res"], parsed), used_online=bool((ctx["sub"].options or {}).get("online")))


@router.get("/papers/{sub_id}/form/export")
def export_form(sub_id: int, venue: str, fmt: str = "md", user: User = Depends(require_user), s: Session = Depends(get_db)):
    sub = owned_submission(s, user, sub_id)
    res = result_of(sub)
    if res is None or venue not in [v.id for v in list_templates()]:
        raise HTTPException(404, "Nothing to export")
    parsed = get_parsed(jobs.cache(), sub.sha256) if sub.sha256 else None
    form = build_form(load_template(venue), res, parsed)
    log_event(s, user.id, "form_exported", sub.id, {"venue": venue, "format": fmt})
    name = re.sub(r"[^\w\-]", "_", f"{venue}_{sub.id}")
    if fmt == "docx":
        return Response(to_docx(form), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})
    return Response(to_markdown(form), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@router.get("/papers/{sub_id}/activity")
def activity(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    ctx = _paper(request, s, user, sub_id, "activity")
    events = list(s.scalars(select(Event).where(Event.submission_id == sub_id, Event.user_id == user.id).order_by(Event.created_at.desc())))
    return render(request, "paper_activity.html", **ctx, events=events)


@router.post("/papers/{sub_id}/delete", dependencies=[Depends(csrf_protect)])
def delete(request: Request, sub_id: int, user: User = Depends(require_user), s: Session = Depends(get_db)):
    sub = owned_submission(s, user, sub_id)
    delete_submission(s, user, sub)
    flash(request, "ok", "The paper and its results were deleted.")
    return RedirectResponse("/app/papers", status_code=303)


# ---------------------------------------------------------------- settings
@router.get("/settings")
def settings_page(request: Request, user: User = Depends(require_user), s: Session = Depends(get_db)):
    return render(request, "settings.html", venues=list_templates(), quota=quota_left(s, user), used=used_this_month(s, user),
                  llm=get_llm())


@router.post("/settings/profile", dependencies=[Depends(csrf_protect)])
def save_profile(request: Request, name: str = Form(""), default_venue: str = Form(""), user: User = Depends(require_user),
                 s: Session = Depends(get_db)):
    if not name.strip():
        flash(request, "bad", "Name cannot be empty.")
    else:
        user.name = name.strip()[:120]
        user.default_venue = default_venue if default_venue in [v.id for v in list_templates()] else None
        log_event(s, user.id, "profile_updated")
        flash(request, "ok", "Profile saved.")
    return RedirectResponse("/app/settings", status_code=303)


@router.post("/settings/password", dependencies=[Depends(csrf_protect)])
def change_password(request: Request, current: str = Form(""), new: str = Form(""), user: User = Depends(require_user),
                    s: Session = Depends(get_db)):
    if not verify_password(current, user.password_hash):
        flash(request, "bad", "Current password is wrong.")
    elif (problem := password_problem(new)):
        flash(request, "bad", problem)
    else:
        user.password_hash = hash_password(new)
        log_event(s, user.id, "password_changed")
        flash(request, "ok", "Password changed.")
    return RedirectResponse("/app/settings", status_code=303)


@router.get("/settings/export")
def export_account(user: User = Depends(require_user), s: Session = Depends(get_db)):
    subs = s.scalars(select(Submission).where(Submission.user_id == user.id).order_by(Submission.id))
    data = {"account": {"email": user.email, "name": user.name, "plan": user.plan, "created_at": str(user.created_at)},
            "papers": [{"id": x.id, "filename": x.filename, "title": x.title, "status": x.status, "created_at": str(x.created_at),
                        "result": x.result, "questions": x.qa} for x in subs]}
    log_event(s, user.id, "account_exported")
    return Response(json.dumps(data, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="my-data.json"'})


@router.post("/settings/delete-account", dependencies=[Depends(csrf_protect)])
def delete_account(request: Request, password: str = Form(""), confirm: str = Form(""), user: User = Depends(require_user),
                   s: Session = Depends(get_db)):
    if confirm.strip().lower() != "delete" or not verify_password(password, user.password_hash):
        flash(request, "bad", "Type DELETE and enter your password to delete the account.")
        return RedirectResponse("/app/settings", status_code=303)
    if user.role == "admin" and (s.scalar(select(func.count(User.id)).where(User.role == "admin")) or 0) <= 1:
        flash(request, "bad", "You are the only administrator. Make someone else an administrator first.")
        return RedirectResponse("/app/settings", status_code=303)
    for sub in list(s.scalars(select(Submission).where(Submission.user_id == user.id))):
        delete_submission(s, user, sub)
    log_event(s, None, "account_deleted", None, {"user_id": user.id})
    s.delete(user)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- convert a manuscript to a venue template
@router.get("/convert")
def convert_page(request: Request, user: User = Depends(require_user)):
    return render(request, "convert.html", templates_=list_paper_templates(), max_mb=web.max_upload_mb)


@router.post("/convert", dependencies=[Depends(csrf_protect)])
async def convert_submit(request: Request, file: UploadFile = File(None), template: str = Form(""), anonymize: str = Form("default"),
                         docx: str = Form(""), tables: str = Form(""), user: User = Depends(require_user), s: Session = Depends(get_db)):
    """The PDF is converted in memory and never stored; the answer is a ZIP (LaTeX, images, Word, report)."""
    def back(msg: str):
        flash(request, "bad", msg)
        return RedirectResponse("/app/convert", status_code=303)

    if template not in [t.id for t in list_paper_templates()]:
        return back("Choose a template.")
    if file is None or not file.filename:
        return back("Choose a file.")
    data = await file.read(web.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > web.max_upload_mb * 1024 * 1024:
        return back(f"The file is larger than {web.max_upload_mb} MB.")
    is_pdf = data.startswith(b"%PDF")
    if not is_pdf and not file.filename.lower().endswith(SOURCE_SUFFIXES):
        return back("Upload a PDF, a .tex file, a .zip of the LaTeX project, or a .docx file.")
    anon = {"yes": True, "no": False}.get(anonymize)  # anything else = the venue's default
    try:
        if is_pdf:
            result = convert_pdf(data, file.filename, template, anonymous=anon, with_docx=docx == "1", tables=tables == "1")
        else:
            result = convert_source(data, file.filename, template, anonymous=anon, with_docx=docx == "1")
    except ConversionError as exc:
        return back(str(exc))
    log_event(s, user.id, "paper_converted", None, {"template": template, "anonymous": result.stats["anonymous"]})
    name = re.sub(r"[^\w\-]", "_", f"{template}_{file.filename.rsplit('.', 1)[0]}")[:80]
    return Response(result.zip_bytes(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
