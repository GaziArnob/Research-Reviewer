from typing import Optional
from urllib.parse import quote

from core.config import settings
from core.http import Response, get

BASE = "https://api.crossref.org"


def _params(extra: Optional[dict] = None) -> dict:
    p = dict(extra or {})
    if settings.contact_email:
        p["mailto"] = settings.contact_email
    return p


def get_work(doi: str) -> Response:
    """status 200 = DOI exists, 404 = unknown DOI, 0 = network failure."""
    return get(f"{BASE}/works/{quote(doi, safe='/')}", params=_params())


def search_title(title: str) -> Response:
    return get(f"{BASE}/works", params=_params({"query.bibliographic": title, "rows": 3}))


def is_retracted(message: dict) -> bool:
    """Best effort: Crossref lists retraction notices in 'updated-by' (not complete for all publishers)."""
    for upd in message.get("updated-by", []) or []:
        if upd.get("type") in ("retraction", "withdrawal"):
            return True
    return False
