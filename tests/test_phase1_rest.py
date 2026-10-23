import pymupdf
import pytest

from core.checks.writing import flesch_reading_ease, run_writing_checks
from core.models import ParsedPaper, Section
from core.pipeline import run_pipeline
from core.storage.cache import Cache
from core.storage.db import get_engine, init_db
from core.storage.papers import delete_paper_data, register_paper
from tests.conftest import make_pdf


def _paper(text: str) -> ParsedPaper:
    return ParsedPaper(sha256="x", filename="f", pages=[text],
                       sections=[Section(name="Introduction", page_start=1, page_end=1, text=text)])


def test_writing_checks_find_common_problems():
    text = ("We analyze the behavior and analyse the results of the the model. We use a pre-trained network and a pretrained baseline. "
            "Figure 1: Plot\nFigure 2: Plot\n" + "This is a sentence about methods. " * 3 +
            "Our teh approch uses a recieve step. ")
    findings = run_writing_checks(_paper(text))
    joined = " ".join(f.text for f in findings)
    assert "Repeated words" in joined and "the the" in joined
    assert "Mixed US/UK spelling" in joined
    assert "pre-trained / pretrained" in joined
    assert "caption" in joined
    assert "Possible typos" in joined and "recieve" in joined
    assert "behaviour" not in joined.split("Possible typos")[1]  # UK spelling is not a typo
    assert "pretrained" not in joined.split("Possible typos")[1]  # prefixed compound is fine


def test_writing_checks_quiet_on_clean_text():
    clean = "The method is simple. It works well on the benchmark. We describe it in detail below. " * 5
    assert run_writing_checks(_paper(clean)) == []
    assert flesch_reading_ease(clean) > 50


def test_scanned_pdf_is_read_with_ocr(monkeypatch):
    import dataclasses

    from core.config import settings
    from core.ingest import ocr
    if not ocr.available():
        pytest.skip("rapidocr not installed")
    # OCR costs seconds per page, so use the short 3-page fixture and a lower word minimum
    monkeypatch.setattr("core.checks.validation.settings", dataclasses.replace(settings, min_words=150))
    src = pymupdf.open(stream=make_pdf(), filetype="pdf")
    scanned = pymupdf.open()
    for page in src:  # rebuild the PDF from page images only, like a scan
        pix = page.get_pixmap(dpi=150)
        new = scanned.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    pdf = scanned.tobytes()
    assert not pymupdf.open(stream=pdf, filetype="pdf")[0].get_text().strip()  # really no text layer

    result = run_pipeline(pdf, "scan.pdf", online=False, cache=Cache(use_db=False))
    assert result.status == "ok", [f.text for f in result.findings]
    assert any(f.module == "ocr" for f in result.findings)
    assert result.stats["words"] > 150


@pytest.mark.skipif(get_engine() is None, reason="PostgreSQL not reachable (set DATABASE_URL)")
def test_delete_paper_removes_everything():
    init_db()
    cache = Cache()
    sha = "d" * 64
    register_paper(sha, "x.pdf", "T", "ok")
    cache.set(f"{sha}:step:v1:none", {"a": 1}, sha, "step")
    counts = delete_paper_data(sha, cache)
    assert counts == {"cache_rows": 1, "reviews": 0, "papers": 1}
    assert cache.get(f"{sha}:step:v1:none") is None
