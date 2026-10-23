import re

from core.models import ParsedPaper, Ref

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_NUMBERED = re.compile(r"^\s*\[(\d+)\]\s*|^\s*(\d{1,3})\.\s+(?=[A-Z])", re.M)


def _split_entries(text: str) -> list[tuple[str, str]]:
    """Return [(key, raw_entry)]. Numbered style first, then one entry per author-start line."""
    matches = list(_NUMBERED.finditer(text))
    if len(matches) >= 3:
        entries = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            key = m.group(1) or m.group(2)
            entries.append((key, " ".join(text[m.end():end].split())))
        return entries
    entries, cur = [], []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        cur.append(line)
        joined = " ".join(cur)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        # Author-year lists: an entry ends on a line that ends with "." once a year has been seen,
        # when the next line starts a new capitalised entry. Surname-first lists also start with "Name, X."
        ended = bool(_YEAR.search(joined)) and line.endswith(".") and bool(re.match(r"^[A-Z\[]", nxt))
        if ended or re.match(r"^[A-Z][A-Za-z'\-]+,\s+[A-Z]\.", nxt) and _YEAR.search(joined) and line.endswith("."):
            entries.append(joined)
            cur = []
    if cur:
        entries.append(" ".join(cur))
    return [(f"ref{i + 1}", e) for i, e in enumerate(entries)]


def _guess_title(raw: str) -> str | None:
    quoted = re.search(r"[\"“](.{15,}?)[\"”]", raw)
    if quoted:
        return quoted.group(1).strip(" ,.")
    parts = re.split(r"(?<=[.?!])\s+", raw)
    candidates = [p for p in parts[1:4] if len(p) >= 15]
    return max(candidates, key=len).strip(" .") if candidates else None


def extract_references(paper: ParsedPaper) -> list[Ref]:
    section = paper.section("references")
    if not section:
        return []
    refs = []
    for key, raw in _split_entries(section.text):
        if len(raw) < 20:
            continue
        doi = _DOI.search(raw)
        year = _YEAR.findall(raw)
        refs.append(Ref(
            key=key,
            raw=raw,
            doi=doi.group(1).rstrip(".,;)") if doi else None,
            title=_guess_title(raw),
            year=int(year[-1]) if year else None,
        ))
    return refs
