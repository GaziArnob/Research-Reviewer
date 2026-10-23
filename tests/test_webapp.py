import json
import os
import re
import tempfile
from pathlib import Path

# Must be set before webapp is imported: settings are read once.
_TMP = Path(tempfile.mkdtemp(prefix="rr_web_"))
os.environ.update(JOB_MODE="sync", WEBAPP_DATABASE_URL=f"sqlite:///{(_TMP / 'web.db').as_posix()}",
                  SECRET_KEY="x" * 40, ALLOW_SIGNUP="true", COOKIE_SECURE="false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.llm.base import LLMResult  # noqa: E402
from core.storage.cache import Cache  # noqa: E402
from tests.conftest import make_pdf  # noqa: E402
from webapp import db, jobs  # noqa: E402
from webapp.main import create_app  # noqa: E402
from webapp.settings import web  # noqa: E402

PW = "correct horse battery"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(jobs, "_cache", Cache(use_db=False))  # never touch the real PostgreSQL step cache
    monkeypatch.setattr(web.__class__, "upload_dir", _TMP / "uploads", raising=False)
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    db.WBase.metadata.drop_all(db.engine())
    db.WBase.metadata.create_all(db.engine())


def token(c: TestClient, path: str = "/login") -> str:
    html = c.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def signup(c: TestClient, email="a@example.com", name="Ada Lovelace", password=PW):
    return c.post("/signup", data={"csrf": token(c, "/signup"), "name": name, "email": email, "password": password, "terms": "1"})


def login(c: TestClient, email="a@example.com", password=PW, nxt="/app"):
    return c.post("/login", data={"csrf": token(c), "email": email, "password": password, "next": nxt})


def upload(c: TestClient, pdf: bytes | None = None, name="paper.pdf", **form):
    tok = c.get("/app/new").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', tok).group(1)
    data = {"csrf": csrf, "venue": "", **form}
    files = [("files", (name, pdf if pdf is not None else make_pdf(), "application/pdf"))]
    return c.post("/app/new", data=data, files=files)


def paper_id(resp) -> int:
    return int(re.search(r"/app/papers/(\d+)", resp.headers["location"]).group(1))


def test_public_pages_render_and_security_headers(client):
    for path in ("/", "/trust", "/plans", "/legal/privacy", "/legal/terms", "/login", "/signup"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
        assert "<style" not in r.text.replace("</style", "") and "onclick" not in r.text  # strict CSP: no inline code
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "Review faster" in client.get("/").text and "not set up yet" in client.get("/plans").text


def test_signup_login_logout_and_csrf(client):
    assert client.post("/signup", data={"name": "x", "email": "x@y.zz", "password": PW, "terms": "1"}).status_code == 403  # no CSRF token
    assert "at least 10" in signup(client, password="short").text.lower() or "10 characters" in signup(client, password="short").text
    r = signup(client)
    assert r.status_code == 303 and r.headers["location"] == "/app/new"
    assert "Hello, Ada" in client.get("/app").text
    assert client.post("/logout", data={"csrf": token(client, "/app")}).status_code == 303
    assert client.get("/app").status_code == 303 and client.get("/app").headers["location"].startswith("/login")
    assert "already exists" in signup(client).text
    assert login(client, password="wrong password!").status_code == 401
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/app"


def test_login_cannot_redirect_to_other_sites_and_is_throttled(client):
    signup(client)
    client.post("/logout", data={"csrf": token(client, "/app")})
    assert login(client, nxt="https://evil.example/").headers["location"] == "/app"
    client.post("/logout", data={"csrf": token(client, "/app")})
    assert login(client, nxt="//evil.example").headers["location"] == "/app"
    client.post("/logout", data={"csrf": token(client, "/app")})
    from webapp.security import throttle
    throttle.fails.clear()
    codes = [login(client, password="nope nope nope").status_code for _ in range(8)]
    assert codes[:6] == [401] * 6 and codes[6:] == [429, 429]
    throttle.fails.clear()


def test_first_user_is_admin_and_others_cannot_see_admin(client):
    signup(client)
    assert client.get("/app/admin").status_code == 200
    other = TestClient(create_app(), follow_redirects=False)
    with other:
        signup(other, "b@example.com", "Bob")
        assert other.get("/app/admin").status_code == 404
        assert "Admin" not in other.get("/app").text


def test_upload_review_pages_exports_and_delete(client):
    signup(client)
    r = upload(client)
    assert r.status_code == 303
    pid = paper_id(r)
    assert not jobs.upload_path(pid).exists()  # the PDF is deleted after processing
    page = client.get(f"/app/papers/{pid}")
    assert page.status_code == 200 and "Ready" in page.text and "A Neural Ranking Study" in page.text
    for tab in ("findings", "evidence", "literature", "ask", "form", "activity"):
        assert client.get(f"/app/papers/{pid}/{tab}").status_code == 200, tab
    assert "Reproducibility" in client.get(f"/app/papers/{pid}/findings").text or "reproducibility" in client.get(f"/app/papers/{pid}/findings").text
    form = client.get(f"/app/papers/{pid}/form?venue=neurips2025").text
    assert "verified against the official reviewer page" in form and "You fill this in" in form
    icml = client.get(f"/app/papers/{pid}/form?venue=icml2025").text
    assert "forbids AI-written review text" in icml
    md = client.get(f"/app/papers/{pid}/form/export?venue=icml2025&fmt=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown") and "deliberately left blank" in md.text
    docx = client.get(f"/app/papers/{pid}/form/export?venue=arr_acl&fmt=docx")
    assert docx.content[:2] == b"PK" and "wordprocessingml" in docx.headers["content-type"]
    assert client.get(f"/app/papers/{pid}/form/export?venue=nope").status_code == 404
    assert "Ready" in client.get("/app/papers").text and "paper.pdf" in client.get("/app/papers?q=paper").text
    assert client.get("/app/papers?status=failed").text.count("paper.pdf") == 0

    r = client.post(f"/app/papers/{pid}/delete", data={"csrf": token(client, "/app")})
    assert r.status_code == 303 and client.get(f"/app/papers/{pid}").status_code == 404


def test_users_cannot_open_each_others_papers(client):
    signup(client)
    pid = paper_id(upload(client))
    bob = TestClient(create_app(), follow_redirects=False)
    with bob:
        signup(bob, "b@example.com", "Bob")
        for path in ("", "/findings", "/evidence", "/literature", "/ask", "/form", "/activity", "/form/export?venue=neurips2025"):
            assert bob.get(f"/app/papers/{pid}{path}").status_code == 404, path
        assert bob.post(f"/app/papers/{pid}/delete", data={"csrf": token(bob, "/app")}).status_code == 404
        assert bob.get(f"/app/api/status?ids={pid}").json() == []
        assert "paper.pdf" not in bob.get("/app/papers").text
    assert client.get(f"/app/papers/{pid}").status_code == 200  # still Ada's


def test_upload_validation_quota_and_anonymous_access(client):
    assert client.get("/app/new").status_code == 303 and client.post("/app/new").status_code in (303, 403)
    signup(client)
    assert upload(client, pdf=b"not a pdf").status_code == 303  # flash error, back to the form
    assert "not a PDF" in client.get("/app/new").text
    from webapp import plans
    from webapp.plans import Plan
    plans.PLANS["free"] = Plan("free", "Starter", 1, True, "t")
    try:
        assert upload(client).status_code == 303
        upload(client, name="second.pdf")
        assert "plan has 0 paper" in client.get("/app/new").text
    finally:
        plans.PLANS["free"] = Plan("free", "Starter", 10, True, "Individual reviewers trying the tool.")


class FakeQA:
    name, model = "fake", "t"

    def complete(self, system, user, *, json_schema=None, temperature=0.2):
        m = re.search(r"\[P(\d+)\]", user)
        return LLMResult(text=json.dumps({"action": "answer", "answer": "It compares against several baselines.",
                                          "cited": [int(m.group(1))] if m else []}), model="t")


def test_ask_the_paper(client, monkeypatch):
    signup(client)
    pid = paper_id(upload(client))
    monkeypatch.setattr("webapp.routes_app.get_llm", lambda: FakeQA())
    assert "Ask" in client.get(f"/app/papers/{pid}/ask").text
    headers = {"X-CSRF-Token": token(client, "/app"), "Content-Type": "application/json"}
    r = client.post(f"/app/papers/{pid}/ask", content=json.dumps({"q": "What baselines?"}), headers=headers)
    assert r.status_code == 200 and "several baselines" in r.text and "Passage P" in r.text
    assert client.post(f"/app/papers/{pid}/ask", content=json.dumps({"q": "x"})).status_code == 403  # no CSRF header
    assert "What baselines?" in client.get(f"/app/papers/{pid}/ask").text  # history is kept


def test_settings_export_password_and_account_deletion(client):
    signup(client)
    pid = paper_id(upload(client))
    assert client.get("/app/settings").status_code == 200
    data = client.get("/app/settings/export").json()
    assert data["account"]["email"] == "a@example.com" and data["papers"][0]["id"] == pid
    csrf = token(client, "/app")
    client.post("/app/settings/password", data={"csrf": csrf, "current": "wrong", "new": "another long password"})
    assert "Current password is wrong" in client.get("/app/settings").text
    client.post("/app/settings/password", data={"csrf": csrf, "current": PW, "new": "another long password"})
    assert "Password changed" in client.get("/app/settings").text
    # the only administrator cannot delete the account
    client.post("/app/settings/delete-account", data={"csrf": csrf, "confirm": "DELETE", "password": "another long password"})
    assert "only administrator" in client.get("/app/settings").text
    bob = TestClient(create_app(), follow_redirects=False)
    with bob:
        signup(bob, "b@example.com", "Bob")
        bpid = paper_id(upload(bob))
        r = bob.post("/app/settings/delete-account", data={"csrf": token(bob, "/app"), "confirm": "DELETE", "password": PW})
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert bob.get("/app").status_code == 303
    with db.session() as s:
        assert s.get(db.Submission, bpid) is None and s.get(db.Submission, pid) is not None


def test_failed_job_is_recorded_not_raised(client, monkeypatch):
    signup(client)
    monkeypatch.setattr(jobs, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    pid = paper_id(upload(client))
    page = client.get(f"/app/papers/{pid}").text
    assert "Failed" in page and "boom" in page and not jobs.upload_path(pid).exists()


def test_admin_can_change_plan(client):
    signup(client)
    bob = TestClient(create_app(), follow_redirects=False)
    with bob:
        signup(bob, "b@example.com", "Bob")
    admin = client.get("/app/admin").text
    uid = int(re.search(r"/app/admin/users/(\d+)/plan", admin.split("b@example.com")[1]).group(1))
    r = client.post(f"/app/admin/users/{uid}/plan", data={"csrf": token(client, "/app"), "plan": "team"})
    assert r.status_code == 303 and "Team" in client.get("/app/admin").text


def test_convert_page_and_download(client):
    assert client.get("/app/convert").status_code == 303  # signed-out users go to the sign-in page
    signup(client)
    page = client.get("/app/convert").text
    assert "Convert to a venue template" in page and "IEEE conference paper" in page and "IEEE journal" in page
    assert "Do not reformat a manuscript that was sent to you" in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    files = {"file": ("my paper.pdf", make_pdf(), "application/pdf")}
    # no CSRF token
    assert client.post("/app/convert", data={"template": "neurips2025"}, files=files).status_code == 403
    r = client.post("/app/convert", data={"csrf": csrf, "template": "ieee_conference", "anonymize": "default", "docx": "1"}, files=files)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"main.tex", "main.docx", "CONVERSION_REPORT.md"} <= set(z.namelist())
    assert r"\documentclass[conference]{IEEEtran}" in z.read("main.tex").decode()
    assert not list(web.upload_dir.glob("*my*")) and not list(web.upload_dir.glob("*.pdf"))  # nothing is stored
    # bad input comes back to the form with a message
    r = client.post("/app/convert", data={"csrf": csrf, "template": "nope"}, files=files)
    assert r.status_code == 303 and "Choose a template" in client.get("/app/convert").text
    bad = client.post("/app/convert", data={"csrf": csrf, "template": "icml2025"}, files={"file": ("x.pdf", b"nope", "application/pdf")})
    assert bad.status_code == 303 and "not a PDF" in client.get("/app/convert").text
    with db.session() as s:
        assert s.query(db.Event).filter_by(action="paper_converted").count() == 1
