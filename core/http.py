import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from core.config import settings


@dataclass
class Response:
    status: int  # 0 = network error
    data: Any = None
    text: str = ""


def _headers(extra: Optional[dict] = None) -> dict:
    ua = "ResearchReviewer/0.1"
    if settings.contact_email:
        ua += f" (mailto:{settings.contact_email})"
    h = {"User-Agent": ua}
    h.update(extra or {})
    return h


def get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
        json: bool = True, retries: int = 2, timeout: int = 15) -> Response:
    """GET with small retry/backoff. Never raises; status 0 means the network failed."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=_headers(headers), timeout=timeout)
        except requests.RequestException:
            if attempt == retries:
                return Response(0)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503) and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue
        if json:
            try:
                return Response(r.status_code, r.json(), r.text)
            except ValueError:
                return Response(r.status_code, None, r.text)
        return Response(r.status_code, None, r.text)
    return Response(0)
