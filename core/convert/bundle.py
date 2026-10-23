import io
import re
import zipfile
from dataclasses import dataclass, field

from core.checks.validation import looks_scanned
from core.convert.docx_out import render_docx
from core.convert.extract import build_document
from core.convert.latex import render_latex
from core.convert.templates import PaperTemplate, load_paper_template
from core.ingest import ocr
from core.ingest.parser import parse_pdf

_URL = re.compile(r"https?://\S+|www\.\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SELF = re.compile(r"\b(?:our (?:previous|earlier|prior) (?:work|paper|study)|we (?:previously|earlier) (?:showed|proposed|introduced|reported)|in our (?:previous|earlier) work)\b", re.I)


class ConversionError(ValueError):
    pass


@dataclass
class Conversion:
    template: PaperTemplate
    files: dict[str, bytes]
    report: str
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.files.items():
                z.writestr(name, data)
        return buf.getvalue()


def _words_per_page(tpl: PaperTemplate) -> int:
    if tpl.layout.columns == 2:
        return 800 if tpl.layout.font_pt >= 11 else 950
    return 600


def _anonymity_warnings(doc) -> list[str]:
    text = " ".join([doc.abstract] + [b.text + " ".join(b.items) for s in doc.sections for b in s.blocks])
    found = []
    if (emails := _EMAIL.findall(text)):
        found.append(f"E-mail addresses in the text: {', '.join(emails[:3])}.")
    if (urls := [u for u in _URL.findall(text) if "github" in u.lower() or "gitlab" in u.lower() or "~" in u]):
        found.append(f"Links that may identify you (code or home pages): {', '.join(urls[:3])}.")
    if _SELF.search(text):
        found.append("Phrases like 'our previous work' can reveal the authors: cite your own work in the third person.")
    if doc.acknowledgements:
        found.append("The acknowledgements were left out of the anonymous version.")
    return found


def convert_pdf(pdf: bytes, filename: str, template_id: str, anonymous: bool | None = None, with_docx: bool = True) -> Conversion:
    tpl = load_paper_template(template_id)
    anonymous = tpl.anonymous_default if anonymous is None else anonymous
    parsed = parse_pdf(pdf, filename)
    if looks_scanned(parsed):
        if not ocr.available():
            raise ConversionError("This PDF has no text layer and OCR is not installed.")
        parsed = parse_pdf(pdf, filename, pages_override=ocr.ocr_pages(pdf))
    if parsed.word_count < 200:
        raise ConversionError("Too little text could be read from this PDF to convert it.")

    doc = build_document(parsed, pdf)
    tex, stat = render_latex(doc, tpl, anonymous)
    warnings = list(doc.warnings)
    if anonymous:
        warnings += _anonymity_warnings(doc)
    if parsed.hidden_spans:
        warnings.append(f"{len(parsed.hidden_spans)} hidden text fragment(s) in the PDF were left out.")
    if stat["non_ascii_left"]:
        warnings.append(f"{stat['non_ascii_left']} non-ASCII character(s) remain in the LaTeX: check the symbols after compiling.")
    if doc.references and stat["citations"] == 0:
        warnings.append("No numeric citations such as [3] were found, so citations stay as plain text (author-year style?). Convert them to \\cite by hand.")

    pages = doc.body_words / _words_per_page(tpl)
    est = f"about {pages:.1f} page(s) of text before figures, tables and references (rough, +/-30%)"
    if tpl.limits.main_pages and pages > tpl.limits.main_pages * 0.9:
        warnings.append(f"The text alone is {est}; the venue's main-text limit is {tpl.limits.main_pages} pages. Figures and tables add more.")

    files = {"main.tex": tex.encode("utf-8")}
    for name, data in doc.images.items():
        files[f"images/{name}"] = data
    if with_docx:
        files["main.docx"] = render_docx(doc, tpl, anonymous)
    stats = {"words": doc.body_words, "sections": len(doc.sections), "references": len(doc.references),
             "citations_converted": stat["citations"], "figures": sum(b.kind == "figure" for s in doc.sections for b in s.blocks),
             "figures_with_image": stat["figures_with_image"], "tables": sum(b.kind == "table" for s in doc.sections for b in s.blocks),
             "images_in_zip": len(doc.images), "estimate": est, "anonymous": anonymous}
    report = make_report(tpl, filename, stats, warnings, with_docx)
    files["CONVERSION_REPORT.md"] = report.encode("utf-8")
    return Conversion(tpl, files, report, warnings, stats)


def make_report(tpl: PaperTemplate, filename: str, stats: dict, warnings: list[str], with_docx: bool) -> str:
    L = [f"# Conversion report: {filename} -> {tpl.name}", "",
         "This tool re-types the text of a PDF into the venue's layout. It is a starting point, not a finished submission. "
         "Use it for your own manuscript; do not reformat a paper that was sent to you to review.", "",
         "## What was converted", "",
         f"- Words read: {stats['words']}; sections: {stats['sections']}; references: {stats['references']}",
         f"- Numeric citations turned into \\cite: {stats['citations_converted']}",
         f"- Figures: {stats['figures']} ({stats['figures_with_image']} matched to an image), tables: {stats['tables']} (captions only)",
         f"- Length: {stats['estimate']}",
         f"- Anonymized: {'yes' if stats['anonymous'] else 'no'}", ""]
    L += ["## Please check", ""] + ([f"- {w}" for w in warnings] or ["- Nothing flagged. Still read the result."]) + [""]
    L += ["## Template facts", "", f"Template check: **{tpl.provenance.level.upper()}** (last checked {tpl.provenance.checked}).", "", tpl.provenance.notes, ""]
    L += [f"- [{'official page' if f.status == 'official' else 'UNVERIFIED'}] {f.text}" for f in tpl.facts]
    if tpl.limits.text:
        L += ["", f"Length rule: {tpl.limits.text}"]
    L += ["", f"Anonymity: {tpl.anonymity.text}", "", "Sources:"] + [f"- {u}" for u in tpl.provenance.official_urls] + [""]
    cc = tpl.compile_check
    L += ["## Compile check", "", (f"Compiled by us with {cc['tool']} on {cc['date']}. {cc['result']}" if cc else
          "Not compiled by us: this venue's style file is not part of the TeX distributions, so the generated LaTeX is untested against it."), ""]
    L += ["## How to compile", "",
          f"1. Install a TeX distribution (TeX Live or MiKTeX), or use Overleaf.",
          f"2. Class/style file: {tpl.latex.class_file}. Download: {tpl.latex.download}",
          "3. Put it next to main.tex and run pdflatex twice. This tool does not compile it for you, so expect to fix small errors.",
          "4. Compare with the venue's own example file: it is the reference, this output is not.", ""]
    if with_docx:
        L += ["## Word version", "", "main.docx uses the venue's page size, columns and font where known. It is NOT the venue's official Word template: "
              "copy the text into the official template if the venue requires it.", ""]
    return "\n".join(L)
