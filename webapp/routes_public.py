import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from webapp.db import User, log_event, utcnow
from webapp.deps import csrf_protect, current_user, flash, get_db, render
from webapp.plans import DEFAULT_PLAN
from webapp.security import LoginThrottle, hash_password, password_problem, throttle, verify_password
from webapp.settings import web

router = APIRouter()
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(value: Optional[str]) -> str:
    """Only local /app paths: a login link must never redirect to another site."""
    if value and value.startswith("/app") and not value.startswith("//") and not urlparse(value).netloc:
        return value
    return "/app"


@router.api_route("/", methods=["GET", "HEAD"])
def landing(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "landing.html")


_ai_limit = LoginThrottle(limit=20, window=600)  # 20 checks per 10 minutes per address; the text is never stored or logged
MAX_AI_CHARS = 60_000


@router.get("/ai-check")
def ai_check_form(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "ai_check.html", text="", report=None, error="")


@router.post("/ai-check", dependencies=[Depends(csrf_protect)])
def ai_check_run(request: Request, text: str = Form(""), user: Optional[User] = Depends(current_user)):
    from core.checks import ai_text
    key = request.client.host if request.client else "?"
    if _ai_limit.blocked(key):
        return render(request, "ai_check.html", 429, text=text[:MAX_AI_CHARS], report=None, error="Too many checks from this address. Wait a few minutes.")
    _ai_limit.fail(key)  # counts every request
    if len(text) > MAX_AI_CHARS:
        return render(request, "ai_check.html", 413, text=text[:MAX_AI_CHARS], report=None,
                      error=f"The text is longer than {MAX_AI_CHARS:,} characters (about 9,000 words). Paste a shorter part.")
    if not text.strip():
        return render(request, "ai_check.html", 400, text="", report=None, error="Paste some text first.")
    return render(request, "ai_check.html", text=text, report=ai_text.full_report(text), error="")


@router.get("/trust")
def trust(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "trust.html")


@router.get("/plans")
def plans_page(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "plans.html")


@router.get("/legal/privacy")
def privacy(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "legal.html", kind="privacy", heading="Privacy notice")


@router.get("/legal/terms")
def terms(request: Request, user: Optional[User] = Depends(current_user)):
    return render(request, "legal.html", kind="terms", heading="Terms of use")


@router.get("/healthz")
def healthz(s: Session = Depends(get_db)):
    try:
        s.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "database unavailable"}, status_code=503)


@router.get("/login")
def login_form(request: Request, next: str = "/app", user: Optional[User] = Depends(current_user)):
    if user:
        return RedirectResponse("/app", status_code=303)
    return render(request, "login.html", next=_safe_next(next))


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/app"),
          s: Session = Depends(get_db)):
    email = email.strip().lower()
    key = (request.client.host if request.client else "?", email)
    if throttle.blocked(key):
        return render(request, "login.html", 429, error="Too many attempts. Wait 15 minutes and try again.", email=email, next=_safe_next(next))
    user = s.scalar(select(User).where(User.email == email))
    # Same message for unknown email and wrong password, and the hash is checked either way (no timing hint).
    ok = verify_password(password, user.password_hash if user else hash_password("x" * 12))
    if not user or not ok:
        throttle.fail(key)
        log_event(s, user.id if user else None, "login_failed", None, {"email_known": bool(user)})
        return render(request, "login.html", 401, error="Wrong email or password.", email=email, next=_safe_next(next))
    throttle.clear(key)
    request.session.clear()  # new session on login: prevents session fixation
    request.session["uid"] = user.id
    user.last_login = utcnow()
    log_event(s, user.id, "login")
    return RedirectResponse(_safe_next(next), status_code=303)


@router.get("/signup")
def signup_form(request: Request, user: Optional[User] = Depends(current_user)):
    if user:
        return RedirectResponse("/app", status_code=303)
    if not web.allow_signup:
        return RedirectResponse("/login", status_code=303)
    return render(request, "signup.html")


@router.post("/signup", dependencies=[Depends(csrf_protect)])
def signup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""),
           terms: str = Form(""), s: Session = Depends(get_db)):
    if not web.allow_signup:
        return RedirectResponse("/login", status_code=303)
    name, email = name.strip()[:120], email.strip().lower()[:254]

    def again(msg: str):
        return render(request, "signup.html", 400, error=msg, name=name, email=email)

    if not name:
        return again("Please enter your name.")
    if not _EMAIL.match(email):
        return again("Please enter a valid email address.")
    if (problem := password_problem(password)):
        return again(problem)
    if terms != "1":
        return again("Please accept the terms to continue.")
    if s.scalar(select(User).where(User.email == email)):
        return again("An account with this email already exists. Try signing in.")
    first = (s.scalar(select(func.count(User.id))) or 0) == 0
    user = User(email=email, name=name, password_hash=hash_password(password), plan=DEFAULT_PLAN,
                role="admin" if first else "user")  # the first account is the owner of the installation
    s.add(user)
    s.flush()
    log_event(s, user.id, "signup", None, {"role": user.role})
    request.session.clear()
    request.session["uid"] = user.id
    flash(request, "ok", "Welcome! Upload your first paper to get started.")
    return RedirectResponse("/app/new", status_code=303)


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/logo.svg", status_code=301)
