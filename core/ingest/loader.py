import hashlib
import re
from pathlib import Path

import requests

from core.http import _headers


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pdf(path: str | Path) -> bytes:
    return Path(path).read_bytes()


_ARXIV_ID = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)


def fetch_arxiv(url_or_id: str) -> tuple[bytes, str]:
    """Download an arXiv paper. Returns (pdf_bytes, filename). Raises ValueError on failure."""
    m = _ARXIV_ID.search(url_or_id)
    if not m:
        raise ValueError("Could not find an arXiv id in the input.")
    arxiv_id = m.group(1)
    try:
        r = requests.get(f"https://arxiv.org/pdf/{arxiv_id}", headers=_headers(), timeout=60)
    except requests.RequestException as e:
        raise ValueError(f"arXiv download failed: {e}") from e
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise ValueError(f"arXiv download failed (status {r.status_code}).")
    return r.content, f"{arxiv_id}.pdf"
