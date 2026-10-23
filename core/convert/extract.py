import re
from collections import Counter
from typing import Optional

import pymupdf

from core.convert.model import Block, Doc, Sec
from core.models import ParsedPaper

_PAGE_NO = re.compile(r"^\s*\d{1,3}\s*$")
_ARXIV = re.compile(r"^arXiv:\S+")
_EQ_NO = re.compile(r"^\(\d{1,3}\)$")
_NUM_ONLY = re.compile(r"^\d+(?:\.\d+){1,2}\.?$")
_SUB = re.compile(r"^(\d+(?:\.\d+){1,2})\.?\s+([A-Z][A-Za-z0-9 ,:&\-/'()]{2,80})$")
_CAP = re.compile(r"^(Figure|Fig\.|Table)\s*(\d+)\s*[:.]\s*(.*)$", re.I)
_BULLET = re.compile(r"^\s*(?:[•●▪‣]|[-–])\s+(.*)$")
_KEYWORDS = re.compile(r"(?i)^\s*(?:keywords?|index terms)\s*[—:\-–]*\s*(.+)$")
_MATH = re.compile(r"[=∑√∫≤≥≈∈∂∏]")
_SKIP_SECTIONS = {"front matter", "abstract", "references"}
_NOTICE = re.compile(r"(?i)permission|copyright|©|hereby|licen[sc]e|all rights reserved")


def _tableish(line: str) -> bool:
    tokens = line.split()
    if len(tokens) < 3:
        return False
    numeric = sum(bool(re.fullmatch(r"[-+]?[\d.,]+%?|±|[-–]", t)) for t in tokens)
    return numeric / len(tokens) >= 0.6


def join_lines(lines: list[str]) -> str:
    """Re-flow wrapped lines and re-join words hyphenated across lines."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and line[:1].islower() and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + line
        else:
            out = f"{out} {line}".strip()
    return re.sub(r"\s+", " ", out)


def _new_paragraph(prev: str, new: str, longest: int) -> bool:
    return prev.rstrip().endswith((".", "?", "!", ":")) and len(prev) < 0.8 * longest and new[:1].isupper()


def to_blocks(lines: list[str], stats: Counter) -> list:
    """Turn raw PDF text lines into blocks; sub-headings come out as Sec markers."""
    out: list = []
    para: list[str] = []
    longest = max((len(l.strip()) for l in lines), default=80)

    def flush():
        if para:
            text = join_lines(para)
            if len(text) > 25 and _MATH.search(text) and len(text) < 100:
                stats["equation_like"] += 1
            out.append(Block("para", text=text))
            para.clear()

    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        i += 1
        if not raw or _PAGE_NO.match(raw) or _ARXIV.match(raw):
            continue
        if _EQ_NO.match(raw):
            stats["equation_numbers_dropped"] += 1
            continue
        if _NUM_ONLY.match(raw) and i < len(lines) and _SUB.match(f"{raw} {lines[i].strip()}") and not lines[i].strip().endswith("."):
            raw = f"{raw} {lines[i].strip()}"
            i += 1
        if (m := _SUB.match(raw)) and not any(ch.isdigit() for ch in m.group(2)):  # "6.1 Transformer (4 layers)" is a table row
            flush()
            out.append(Sec(heading=m.group(2).strip(), level=min(3, m.group(1).count(".") + 1)))
            continue
        if (c := _CAP.match(raw)):
            flush()
            cap = [c.group(3)]
            while i < len(lines) and len(cap) < 6 and not cap[-1].rstrip().endswith((".", "?")):
                cap.append(lines[i].strip())
                i += 1
            kind = "table" if c.group(1).lower() == "table" else "figure"
            out.append(Block(kind, num=int(c.group(2)), caption=join_lines(cap)))
            continue
        if _tableish(raw):
            stats["table_rows_dropped"] += 1
            continue
        if (b := _BULLET.match(raw)):
            flush()
            item = [b.group(1)]
            while i < len(lines) and lines[i].strip() and not _BULLET.match(lines[i]) and not item[-1].rstrip().endswith("."):
                nxt = lines[i].strip()
                if _CAP.match(nxt) or _SUB.match(nxt) or (nxt[:1].isupper() and len(item[-1]) < 0.8 * longest):
                    break  # a heading, a caption or a new sentence after a short line ends the bullet
                item.append(nxt)
                i += 1
            out.append(Block("bullets", items=[join_lines(item)]))
            continue
        if para and _new_paragraph(para[-1], raw, longest):
            flush()
        para.append(raw)
    flush()

    merged: list = []  # consecutive bullet blocks become one list
    for b in out:
        if isinstance(b, Block) and b.kind == "bullets" and merged and isinstance(merged[-1], Block) and merged[-1].kind == "bullets":
            merged[-1].items += b.items
        else:
            merged.append(b)
    return merged


def extract_images(pdf: bytes, min_px: int = 120) -> list[tuple[int, float, float, str, bytes]]:
    """Raster images only (page, y, x, extension, bytes). Vector figures cannot be recovered from a PDF this way."""
    found, seen = [], set()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for pno, page in enumerate(doc, start=1):
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in seen:
                continue
            try:
                img = doc.extract_image(xref)
                if not img or img["width"] < min_px or img["height"] < min_px:
                    continue
                rects = page.get_image_rects(xref)
                y, x = (rects[0].y0, rects[0].x0) if rects else (0.0, 0.0)
                if img["ext"] in ("png", "jpg", "jpeg"):
                    ext, data = img["ext"], img["image"]
                else:
                    pix = pymupdf.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:
                        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                    ext, data = "png", pix.tobytes("png")
                seen.add(xref)
                found.append((pno, y, x, ext, data))
            except Exception:  # noqa: BLE001 - a damaged image must not stop the conversion
                continue
    return found


def _page_of(parsed: ParsedPaper, text: str) -> Optional[int]:
    probe = re.sub(r"\s+", " ", text[:40]).strip().lower()
    if len(probe) < 15:
        return None
    for n, page in enumerate(parsed.pages, start=1):
        if probe in re.sub(r"\s+", " ", page).lower():
            return n
    return None


def build_document(parsed: ParsedPaper, pdf: Optional[bytes] = None) -> Doc:
    doc, stats = Doc(title=re.sub(r"\s+", " ", parsed.title).strip()), Counter()

    front = parsed.section("front matter")
    if front:
        lines = [l.strip() for l in front.text.splitlines() if l.strip() and not _ARXIV.match(l.strip())]
        title_words = set(doc.title.lower().split())
        cand = [l for l in lines if not set(l.lower().split()) <= title_words]
        keep, notice = [], False
        for l in cand:  # a permission/copyright notice runs from its first keyword line to the sentence end
            if notice or _NOTICE.search(l):
                notice = not l.rstrip().endswith(".")
                doc.warnings.append("A permission or copyright notice in the title block was left out of the author list.") \
                    if "permission or copyright" not in " ".join(doc.warnings) else None
                continue
            keep.append(l)
        doc.authors_raw = [l for l in keep if len(l) <= 90][:12]

    abstract = parsed.section("abstract")
    if abstract:
        keep = []
        for line in abstract.text.splitlines():
            if (k := _KEYWORDS.match(line)):
                doc.keywords = [w.strip(" .;") for w in re.split(r"[,;·]", k.group(1)) if w.strip()]
            else:
                keep.append(line)
        cut = next((n for n, l in enumerate(keep) if n > 2 and re.match(r"^\s*[∗*†‡§¶]\s*\w", l)), None)
        if cut is not None:  # author footnotes ("*Equal contribution ...") sit in the same text block as the abstract
            doc.footnotes = join_lines(keep[cut:])
            keep = keep[:cut]
            doc.warnings.append("Author footnotes were found next to the abstract and moved into a LaTeX comment: put them where the venue wants them.")
        doc.abstract = join_lines(keep)
    else:
        doc.warnings.append("No abstract section was found: add one by hand.")

    pending: list = []
    for s in parsed.sections:
        name = s.name
        if name.lower() in _SKIP_SECTIONS:
            continue
        items = to_blocks(s.text.splitlines(), stats)
        if name.lower().startswith("acknowledg"):
            doc.acknowledgements = [b for b in items if isinstance(b, Block)]
            continue
        current = Sec(heading=name, level=1)
        doc.sections.append(current)
        for it in items:
            if isinstance(it, Sec):
                current = it
                doc.sections.append(current)
            else:
                current.blocks.append(it)
        pending.append(name)

    doc.references = []
    for r in parsed.references:
        raw = r.raw
        if len(raw) > 450:
            raw = raw[:450].rsplit(" ", 1)[0] + " ..."
            doc.warnings.append(f"Reference {r.key} was very long (probably appendix text after the list) and was cut: check it.")
        doc.references.append((r.key, raw))
    if not doc.references:
        doc.warnings.append("No reference list was recognised: add the bibliography by hand.")

    # Match raster images to figure captions when a page has as many captions as images.
    if pdf:
        imgs = extract_images(pdf)
        figures = [b for s in doc.sections for b in s.blocks if b.kind == "figure"]
        by_page: dict[int, list] = {}
        for f in figures:
            p = _page_of(parsed, f.caption)
            if p:
                by_page.setdefault(p, []).append(f)
        used = 0
        for p, caps in by_page.items():
            page_imgs = sorted([i for i in imgs if i[0] == p], key=lambda t: (t[1], t[2]))
            if len(page_imgs) == len(caps) and caps:
                for cap, (pno, _, _, ext, data) in zip(caps, page_imgs):
                    name = f"fig{cap.num}_page{pno}.{ext}"
                    doc.images[name] = data
                    cap.image = name
                    used += 1
        for pno, y, x, ext, data in imgs:
            name = f"extra_page{pno}_{int(y)}_{int(x)}.{ext}"
            if not any(data is v for v in doc.images.values()):
                doc.images[name] = data
        stats["images_total"], stats["images_matched"] = len(imgs), used
        if figures and used < len(figures):
            doc.warnings.append(f"{len(figures) - used} of {len(figures)} figures have no matched image and are placeholders. "
                                "Figures drawn as vectors cannot be recovered from a PDF: export them from your original files.")
    elif any(b.kind == "figure" for s in doc.sections for b in s.blocks):
        doc.warnings.append("Figures are placeholders (no PDF image data was available).")

    got_f = {b.num for s in doc.sections for b in s.blocks if b.kind == "figure"}
    got_t = {b.num for s in doc.sections for b in s.blocks if b.kind == "table"}
    lost_f, lost_t = sorted(set(parsed.figure_numbers) - got_f), sorted(set(parsed.table_numbers) - got_t)
    if lost_f or lost_t:
        doc.warnings.append("The PDF has captions for " + ", ".join([f"Figure {n}" for n in lost_f] + [f"Table {n}" for n in lost_t]) +
                            " that are not in the converted text. They are probably in an appendix after the reference list, which is NOT converted: copy that part by hand.")
    n_tables = sum(1 for s in doc.sections for b in s.blocks if b.kind == "table")
    if n_tables:
        doc.warnings.append(f"{n_tables} table(s) are placeholders with their captions only: table contents are not recovered. "
                            f"{stats['table_rows_dropped']} number-heavy line(s) were removed from the text.")
    if stats["equation_like"]:
        doc.warnings.append(f"About {stats['equation_like']} equation-like line(s) were kept as plain text. Retype equations in LaTeX.")
    doc.stats = dict(stats)
    return doc
