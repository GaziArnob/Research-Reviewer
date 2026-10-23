import re

import pymupdf

from core.ingest.loader import sha256_of
from core.ingest.sections import split_sections
from core.models import HiddenSpan, ParsedPaper
from core.references.extract import extract_references

_FIG = re.compile(r"^(?:Figure|Fig\.)\s*(\d+)", re.I | re.M)
_TAB = re.compile(r"^Table\s+(\d+)", re.I | re.M)


def _hidden_reason(span: dict, page_rect) -> str | None:
    if span["color"] == 0xFFFFFF:
        return "white text"
    if span["size"] < 3:
        return "tiny font"
    x0, y0, x1, y1 = span["bbox"]
    if x1 < page_rect.x0 or x0 > page_rect.x1 or y1 < page_rect.y0 or y0 > page_rect.y1:
        return "outside page"
    return None


def parse_pdf(pdf_bytes: bytes, filename: str = "paper.pdf",
              pages_override: list[str] | None = None) -> ParsedPaper:
    """Extract visible text per page (hidden text is separated out), title, sections, references.

    `pages_override` replaces the text layer with OCR text for scanned PDFs."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages: list[str] = []
    hidden: list[HiddenSpan] = []
    title_spans: list[tuple[float, str]] = []
    images = 0

    for page_no, page in enumerate(doc, start=1):
        lines_out: list[str] = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 1:
                images += 1
                continue
            for line in block.get("lines", []):
                visible = []
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        visible.append(text)
                        continue
                    reason = _hidden_reason(span, page.rect)
                    if reason:
                        hidden.append(HiddenSpan(page=page_no, text=text.strip(), reason=reason))
                        continue
                    visible.append(text)
                    if page_no == 1 and abs(line["dir"][1]) < 0.1:  # skip rotated margin stamps (arXiv id)
                        title_spans.append((span["size"], text.strip()))
                joined = "".join(visible).strip()
                if joined:
                    lines_out.append(joined)
        pages.append("\n".join(lines_out))

    title = ""
    if pages_override is not None:
        pages, hidden, title_spans = pages_override, [], []
        title = next((l.strip() for p in pages for l in p.splitlines() if len(l.strip()) > 8), "")[:200]
    if title_spans:
        top = max(size for size, _ in title_spans)
        title = " ".join(t for size, t in title_spans if abs(size - top) < 0.5)[:200]

    full = "\n".join(pages)
    paper = ParsedPaper(
        sha256=sha256_of(pdf_bytes),
        filename=filename,
        title=title,
        pages=pages,
        sections=split_sections(pages),
        hidden_spans=hidden,
        figure_numbers=sorted({int(n) for n in _FIG.findall(full)}),
        table_numbers=sorted({int(n) for n in _TAB.findall(full)}),
        image_count=images,
    )
    paper.references = extract_references(paper)
    return paper
