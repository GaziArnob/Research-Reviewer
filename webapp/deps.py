from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from webapp import db
from webapp.db import User
from webapp.plans import PLANS, plan_of
from webapp.security import csrf_ok, csrf_token
from webapp.settings import web

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _dt(value: Optional[datetime], fmt: str = "%d %b %Y, %H:%M") -> str:
    return value.strftime(fmt) if value else ""


def _ago(value: Optional[datetime]) -> str:
    if not value:
        return ""
    from datetime import timezone
    delta = datetime.now(timezone.utc) - (value if value.tzinfo else value.replace(tzinfo=timezone.utc))
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


templates.env.filters["dt"] = _dt
templates.env.filters["ago"] = _ago
def _asset_version() -> str:
    """Changes whenever the CSS or JS file changes, so browsers never keep a stale copy after an update."""
    static = TEMPLATE_DIR.parent / "static"
    return str(int(max((f.stat().st_mtime for f in [static / "css" / "app.css", static / "css" / "site.css", static / "js" / "app.js", static / "js" / "boot.js", *(static / "brand").glob("*.png")] if f.exists()), default=0)))


templates.env.globals.update(brand=web.brand, tagline=web.tagline, plans=PLANS, plan_of=plan_of, asset_v=_asset_version)


def get_db() -> Iterator[Session]:
    with db.session() as s:
        yield s


def current_user(request: Request, s: Session = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("uid")
    user = s.get(User, uid) if uid else None
    request.state.user = user
    return user


def require_user(request: Request, user: Optional[User] = Depends(current_user)) -> User:
    if user is None:
        nxt = request.url.path if request.method == "GET" else "/app"
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={nxt}"})
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=404, detail="Not found")
    return user


async def csrf_protect(request: Request) -> None:
    """All state-changing routes: the token comes from a hidden form field or the X-CSRF-Token header."""
    token = request.headers.get("x-csrf-token")
    if token is None and "multipart/form-data" in request.headers.get("content-type", "") or \
            "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
        form = await request.form()
        token = form.get("csrf")
    if not csrf_ok(request, token):
        raise HTTPException(status_code=403, detail="Your session expired. Reload the page and try again.")


def flash(request: Request, kind: str, text: str) -> None:
    request.session.setdefault("flashes", []).append({"kind": kind, "text": text})


def render(request: Request, template: str, status_code: int = 200, **ctx):
    ctx.update(user=getattr(request.state, "user", None), csrf=csrf_token(request),
               flashes=request.session.pop("flashes", []), path=request.url.path, support=web.support_email,
               allow_signup=web.allow_signup)
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
