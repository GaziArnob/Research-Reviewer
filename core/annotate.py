import re
from typing import Iterable

import pymupdf

from core.models import Finding

COLORS = {"major": (0.86, 0.15, 0.15), "minor": (0.95, 0.65, 0.1), "strength": (0.2, 0.65, 0.3),
          "question": (0.1, 0.55, 0.8), "flag": (0.5, 0.5, 0.5)}


def _rects(page, quote: str):
    """Find the quote on the page. Long quotes break across lines or hyphenate, so fall back to shorter pieces."""
    flat = re.sub(r"\s+", " ", quote).strip()
    for n in (len(flat), 100, 60, 35):
        piece = flat[:n].rstrip(" ,;")
        if len(piece) >= 20:
            hits = page.search_for(piece)
            if hits:
                return hits
    return []


def annotate_pdf(pdf: bytes, findings: Iterable[Finding]) -> tuple[bytes, dict]:
    """Highlight every quote that was used as evidence and attach the finding as a note.

    Returns the new PDF and counts. The original is not modified and nothing is stored by this function.
    """
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    done = missed = 0
    for f in findings:
        for ev in f.evidence[:1]:
            if ev.source != "paper" or not ev.quote:
                continue
            pages = [doc[ev.page - 1]] if ev.page and 1 <= ev.page <= len(doc) else list(doc)
            for page in pages:
                rects = _rects(page, ev.quote)
                if rects:
                    annot = page.add_highlight_annot(rects)
                    annot.set_colors(stroke=COLORS.get(f.kind, COLORS["flag"]))
                    annot.set_info(title=f"{f.kind} / {f.status}", content=f"[{f.module}] {f.text}")
                    annot.update()
                    done += 1
                    break
            else:
                missed += 1
    return doc.tobytes(garbage=3, deflate=True), {"highlighted": done, "not_found": missed}
