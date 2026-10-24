"""Rebuild simple tables from word positions (PyMuPDF `find_tables` cut decimals and merged cells on real papers).

Idea: a table sits right under (or over) its "Table N" caption. Take the text lines next to the caption until the
first paragraph-like line or big vertical gap, treat every line as a row, and split rows into columns at the vertical
white gaps that no row crosses. Anything that does not look like a table (fewer than 2 rows or 2 columns, ragged
rows) is not returned, so a wrong guess is rare but many tables are skipped.
"""
import re
from statistics import median

import pymupdf

_CAP = re.compile(r"^\s*Table\s+(\d+)\s*[:.]", re.I)
_COL_GAP = 7.0  # points of empty space that separate two columns


def _lines(page) -> list[dict]:
    """Text lines with their words, top to bottom."""
    out = []
    words = page.get_text("words")
    rows: dict[tuple, list] = {}
    for x0, y0, x1, y1, w, b, l, _ in words:
        rows.setdefault((b, l), []).append((x0, y0, x1, y1, w))
    for ws in rows.values():
        ws.sort()
        out.append({"y0": min(w[1] for w in ws), "y1": max(w[3] for w in ws), "x0": ws[0][0], "x1": max(w[2] for w in ws),
                    "words": ws, "text": " ".join(w[4] for w in ws)})
    return sorted(out, key=lambda l: (round(l["y0"]), l["x0"]))


def _merge_rows(lines: list[dict]) -> list[dict]:
    """A table row is often several PDF 'lines' (one per cell block) at the same height."""
    rows: list[dict] = []
    for l in lines:
        for r in rows:
            if abs((r["y0"] + r["y1"]) / 2 - (l["y0"] + l["y1"]) / 2) < 0.45 * (l["y1"] - l["y0"]):
                r["words"] += l["words"]
                r["x0"], r["x1"] = min(r["x0"], l["x0"]), max(r["x1"], l["x1"])
                r["y0"], r["y1"] = min(r["y0"], l["y0"]), max(r["y1"], l["y1"])
                break
        else:
            rows.append({**l, "words": list(l["words"])})
    for r in rows:
        r["words"].sort()
    return sorted(rows, key=lambda r: r["y0"])


def _columns(rows: list[dict]) -> list[tuple[float, float]]:
    spans = sorted((w[0], w[2]) for r in rows for w in r["words"])
    cols: list[list[float]] = []
    for a, b in spans:
        if cols and a - cols[-1][1] < _COL_GAP:
            cols[-1][1] = max(cols[-1][1], b)
        else:
            cols.append([a, b])
    return [(a, b) for a, b in cols]


def _cells(row: dict, cols: list[tuple[float, float]]) -> list[str]:
    cells = [[] for _ in cols]
    for w in row["words"]:
        mid = (w[0] + w[2]) / 2
        i = min(range(len(cols)), key=lambda k: 0 if cols[k][0] <= mid <= cols[k][1] else min(abs(mid - cols[k][0]), abs(mid - cols[k][1])))
        cells[i].append(w[4])
    return [" ".join(c) for c in cells]


def _paragraph_like(row: dict, width: float) -> bool:
    """A full-width line made of ordinary words with small gaps: body text, not a table row."""
    ws = row["words"]
    if len(ws) < 8 or (row["x1"] - row["x0"]) < 0.8 * width:
        return False
    gaps = [b[0] - a[2] for a, b in zip(ws, ws[1:])]
    return max(gaps) < _COL_GAP


def recover_tables(pdf: bytes, max_rows: int = 40) -> dict[int, list[list[str]]]:
    """Table number -> rows of cells, for tables that were reconstructed with confidence."""
    found: dict[int, list[list[str]]] = {}
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        lines = _lines(page)
        width = page.rect.width - 2 * 50
        for i, l in enumerate(lines):
            m = _CAP.match(l["text"])
            if not m or int(m.group(1)) in found:
                continue
            # the caption may wrap: skip lines until the caption sentence ends
            j, cap_end = i + 1, l
            while j < len(lines) and not cap_end["text"].rstrip().endswith((".", "?")) and j < i + 5:
                cap_end, j = lines[j], j + 1
            body, prev = [], cap_end
            for nxt in lines[j:]:
                h = nxt["y1"] - nxt["y0"]
                if nxt["y0"] - prev["y1"] > 2.2 * h and body:
                    break
                if _CAP.match(nxt["text"]) or _paragraph_like(nxt, width):
                    break
                body.append(nxt)
                prev = nxt
                if len(body) > max_rows * 3:
                    break
            rows = _merge_rows(body)
            if not 2 <= len(rows) <= max_rows:
                continue
            cols = _columns(rows)
            if not 2 <= len(cols) <= 12:
                continue
            grid = [_cells(r, cols) for r in rows]
            filled = [sum(bool(c) for c in r) for r in grid]
            if median(filled) < 2:
                continue  # mostly one-cell rows: text, not a table
            if filled[0] < 2:
                continue  # a one-cell first row is a stray sentence, not a header
            complete = sum(f >= len(cols) - 1 for f in filled) / len(filled)
            if complete < 0.8 or sum(filled) / (len(grid) * len(cols)) < 0.8:
                continue  # ragged or half-empty: side-by-side tables or wrapped text, a wrong guess is worse than a placeholder
            found[int(m.group(1))] = grid
    return found


def to_tabular(grid: list[list[str]]) -> str:
    """LaTeX tabular with plain \\hline rules (no extra packages needed)."""
    from core.convert.latex import esc
    spec = "l" + "c" * (len(grid[0]) - 1)
    rows = [" & ".join(esc(c) for c in r) + r" \\" for r in grid]
    return "\\begin{tabular}{" + spec + "}\n\\hline\n" + rows[0] + "\n\\hline\n" + "\n".join(rows[1:]) + "\n\\hline\n\\end{tabular}"
