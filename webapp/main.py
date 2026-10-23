import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from webapp import db, jobs
from webapp.deps import render
from webapp.routes_admin import router as admin_router
from webapp.routes_app import router as app_router
from webapp.routes_public import router as public_router
from webapp.security import SecurityHeaders
from webapp.settings import web

log = logging.getLogger("webapp")
HERE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.configure()
    web.upload_dir.mkdir(parents=True, exist_ok=True)
    n = jobs.runner.recover()
    if n:
        log.info("re-queued %d paper(s) after restart", n)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=web.brand, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(SessionMiddleware, secret_key=web.secret_key, session_cookie="rr_session", max_age=60 * 60 * 12,
                       same_site="lax", https_only=web.cookie_secure)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(public_router)
    app.include_router(app_router)
    app.include_router(admin_router)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code in (301, 302, 303, 307):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/app/api") or "application/json" in request.headers.get("content-type", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titles = {403: "Not allowed", 404: "Page not found"}
        return render(request, "error.html", exc.status_code, code=exc.status_code,
                      title=titles.get(exc.status_code, "Something went wrong"), detail=exc.detail if exc.status_code == 403 else "")

    return app


app = create_app()
