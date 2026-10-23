import io
import re
import zipfile
from collections import Counter

import pymupdf
import pytest

from core.convert.bundle import ConversionError, convert_pdf
from core.convert.extract import build_document, join_lines, to_blocks
from core.convert.latex import esc, render_latex, text_with_cites
from core.convert.model import Block, Sec
from core.convert.templates import list_paper_templates, load_paper_template
from core.ingest.parser import parse_pdf
from tests.conftest import make_pdf


def test_every_paper_template_is_valid_and_states_what_is_unverified():
    ts = list_paper_templates()
    assert {t.id for t in ts} == {"ieee_conference", "ieee_journal", "acm_sigconf", "neurips2025", "icml2025", "iclr2025",
                                  "acl2025", "cvpr2025", "aaai26"}
    assert {t.kind for t in ts} == {"conference", "journal"}  # both kinds, not only journals
    for t in ts:
        assert t.provenance.official_urls and t.provenance.checked and t.facts
        assert t.latex.documentclass and t.layout.columns in (1, 2) and t.layout.paper in ("letter", "a4")
        if t.provenance.level == "partial":
            assert "NOT" in t.provenance.notes or "not" in t.provenance.notes.lower()
        assert any(f.status == "unverified" for f in t.facts) or t.provenance.level == "verified"


def test_verified_venue_rules_match_the_official_pages():
    assert load_paper_template("neurips2025").limits.main_pages == 9
    assert load_paper_template("icml2025").limits.main_pages == 8
    assert load_paper_template("cvpr2025").limits.main_pages == 8
    assert load_paper_template("aaai26").limits.main_pages == 7
    acl = load_paper_template("acl2025")
    assert (acl.layout.paper, acl.layout.columns, acl.layout.font_pt, acl.layout.margins_cm, acl.layout.layout_status) == ("a4", 2, 11, 2.5, "official")
    ieee_c, ieee_j = load_paper_template("ieee_conference"), load_paper_template("ieee_journal")
    assert ieee_c.kind == "conference" and ieee_j.kind == "journal" and ieee_c.latex.documentclass == "IEEEtran"
    assert ieee_c.latex.options == ["conference"] and not ieee_c.anonymous_default
    assert all(load_paper_template(i).anonymous_default for i in ("neurips2025", "icml2025", "iclr2025", "acl2025", "cvpr2025", "aaai26"))


def test_latex_escaping_and_citations():
    assert esc("50% of A&B_c #1 {x} $5") == r"50\% of A\&B\_c \#1 \{x\} \$5"
    assert esc("x ≤ y ± 1 “q” ﬁ") == "x $\\leq$ y $\\pm$ 1 ``q'' fi"
    tex, n = text_with_cites("As shown in [3], [5, 7] and [9-11].", {3, 5, 7, 9, 10, 11})
    assert n == 3 and "\\cite{r3}" in tex and "\\cite{r5,r7}" in tex and "\\cite{r9,r10,r11}" in tex
    tex, n = text_with_cites("Unknown [99] and array [i, j].", {1, 2})
    assert n == 0 and "[99]" in tex  # a bracket that is not a known reference is left alone


def test_join_lines_dehyphenates_and_blocks_find_structure():
    assert join_lines(["a trans-", "former model", "works"]) == "a transformer model works"
    lines = ["We study ranking.", "A second paragraph starts here and goes on for a while until it wraps", "to a new line.",
             "3.1 Encoder", "Details follow.", "• first point", "• second point",
             "Figure 2: The model", "overview.", "Table 1: Results.", "0.91 0.88 0.77 0.65", "(4)", "12"]
    stats = Counter()
    out = to_blocks(lines, stats)
    kinds = [type(b).__name__ + ":" + (getattr(b, "kind", getattr(b, "heading", ""))) for b in out]
    assert "Sec:Encoder" in kinds and kinds.count("Block:bullets") == 1
    fig = next(b for b in out if isinstance(b, Block) and b.kind == "figure")
    assert fig.num == 2 and fig.caption == "The model overview."
    assert stats["table_rows_dropped"] == 1 and stats["equation_numbers_dropped"] == 1
    out = to_blocks(["3.2", "Position encoding", "Text follows."], Counter())
    assert isinstance(out[0], Sec) and out[0].heading == "Position encoding"  # number and title on separate lines
    assert not any(isinstance(b, Sec) for b in to_blocks(["6.1 Transformer (4 layers)"], Counter()))  # table row, not a heading


def test_document_from_pdf_and_latex_structure(pdf_bytes):
    parsed = parse_pdf(pdf_bytes, "t.pdf")
    doc = build_document(parsed, pdf_bytes)
    assert doc.title.startswith("A Neural Ranking Study") and doc.abstract and len(doc.references) == 3
    tex, stat = render_latex(doc, load_paper_template("ieee_conference"), anonymous=False)
    assert "\\documentclass[conference]{IEEEtran}" in tex and "\\begin{IEEEkeywords}" not in tex
    assert tex.count("\\begin{") == tex.count("\\end{") and stat["citations"] >= 1
    assert "\\bibitem{r1}" in tex and "\\cite{r1" in tex


def test_every_template_renders_balanced_latex_and_anonymizes(pdf_bytes):
    for t in list_paper_templates():
        c = convert_pdf(pdf_bytes, "t.pdf", t.id)
        tex = c.files["main.tex"].decode()
        assert tex.count("\\begin{") == tex.count("\\end{"), t.id
        depth = 0
        for ch in tex.replace("\\{", "").replace("\\}", ""):
            depth += (ch == "{") - (ch == "}")
            assert depth >= 0, t.id
        assert depth == 0, t.id
        assert f"\\documentclass" in tex and "\\end{document}" in tex and "main.docx" in c.files and "CONVERSION_REPORT.md" in c.files
        if t.anonymous_default:
            assert "Anonymous" in tex and "Smith" not in tex.split("\\begin{document}")[1].split("\\begin{abstract}")[0], t.id
        assert "NOT guaranteed to compile" in tex


def test_zip_docx_and_report_contents(pdf_bytes):
    from docx import Document
    c = convert_pdf(pdf_bytes, "t.pdf", "acl2025")
    z = zipfile.ZipFile(io.BytesIO(c.zip_bytes()))
    assert {"main.tex", "main.docx", "CONVERSION_REPORT.md"} <= set(z.namelist())
    docx = Document(io.BytesIO(z.read("main.docx")))
    assert docx.sections[0].page_width.cm == pytest.approx(21.0, abs=0.05) and docx.sections[0].left_margin.cm == pytest.approx(2.5, abs=0.05)
    assert any(p.text.startswith("Abstract") for p in docx.paragraphs)
    report = z.read("CONVERSION_REPORT.md").decode()
    assert "NOT the venue's official Word template" in report and "not compiled" in report.lower() or "NOT compiled" in report
    assert "[official page]" in report and "[UNVERIFIED]" in report and "do not reformat a paper that was sent to you" in report
    only_tex = convert_pdf(pdf_bytes, "t.pdf", "neurips2025", with_docx=False)
    assert "main.docx" not in only_tex.files


def test_length_and_anonymity_warnings():
    pdf = make_pdf(body_repeats=14)
    c = convert_pdf(pdf, "t.pdf", "ieee_conference", anonymous=False)
    assert c.stats["estimate"].startswith("about") and not c.stats["anonymous"]
    src = pymupdf.open(stream=pdf, filetype="pdf")
    page = src[1]  # an early page: its text is part of the converted body
    page.insert_text((50, 20), "Contact me at jane.doe@example.org or see https://github.com/janedoe/code", fontsize=8)
    page.insert_text((50, 32), "In our previous work we showed this.", fontsize=8)
    anon = convert_pdf(src.tobytes(), "t.pdf", "neurips2025")
    joined = " ".join(anon.warnings)
    assert "jane.doe@example.org" in joined or "github" in joined or "previous work" in joined


def test_blank_or_tiny_pdf_is_refused():
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Only a few words here.")
    with pytest.raises(ConversionError):
        convert_pdf(doc.tobytes(), "tiny.pdf", "neurips2025")


TECTONIC = __import__("pathlib").Path(__file__).resolve().parent.parent / "tools" / "tectonic" / "tectonic.exe"


@pytest.mark.skipif(not TECTONIC.exists(), reason="tectonic is not installed (tools/tectonic); it also needs internet the first time")
@pytest.mark.parametrize("template", ["ieee_conference", "ieee_journal", "acm_sigconf"])
def test_generated_latex_really_compiles(template, tmp_path):
    """The only templates whose class files ship with TeX distributions can be checked for real."""
    import subprocess
    c = convert_pdf(make_pdf(body_repeats=3), "t.pdf", template)
    z = zipfile.ZipFile(io.BytesIO(c.zip_bytes()))
    z.extractall(tmp_path)
    run = subprocess.run([str(TECTONIC), "-X", "compile", "main.tex"], cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, (run.stdout + run.stderr)[-1500:]
    assert len(pymupdf.open(tmp_path / "main.pdf")) >= 1
