import re

from core.models import Section

_HEADING = re.compile(
    r"^(?:(\d+(?:\.\d+)*)\.?\s+)?"
    r"(abstract|introduction|related work|background|preliminaries|methods?|methodology|approach|"
    r"experiments?|experimental setup|results|discussion|conclusions?|limitations|ethics statement|"
    r"references|acknowledg(?:e)?ments|appendix)\s*[:.]?$",
    re.I,
)
_ABSTRACT_INLINE = re.compile(r"^abstract\s*[\-—–:.]+\s*(.+)$", re.I)
# Top-level numbered headings with any title ("3 Model Architecture"). Sub-sections ("3.1 ...") stay inside their parent.
_NUMBERED = re.compile(r"^(\d{1,2})\.?\s+([A-Z][A-Za-z][A-Za-z0-9 ,&\-/'()]{1,60})$")


def _generic_heading(line: str, last_number: int) -> tuple[int, str] | None:
    """A numbered heading is accepted only if it continues the numbering (1, 2, 3...), which rejects stray lines
    such as "7 Billion parameters were used" or table rows."""
    m = _NUMBERED.match(line)
    if not m or line.endswith(".") or len(line.split()) > 10:
        return None
    number, title = int(m.group(1)), m.group(2).strip()
    if number not in (last_number + 1, last_number + 2) or sum(ch.isdigit() for ch in title) > 2:
        return None
    return number, title


def _heading(line: str, last_number: int) -> tuple[int | None, str] | None:
    """(section number or None, section name) if the line is a heading."""
    if len(line) > 70:
        return None
    if len(line) <= 60 and (m := _HEADING.match(line)):
        return (int(m.group(1)) if m.group(1) and m.group(1).isdigit() else None), m.group(2).title()
    return _generic_heading(line, last_number)


def split_sections(pages: list[str]) -> list[Section]:
    """Split page text into sections by well-known heading names and by numbered top-level headings.

    PDFs often put the number and the title on separate lines ("3" / "Model Architecture"); those are joined."""
    sections: list[Section] = []
    name, start, buf = "Front matter", 1, []
    last_number = 0

    def close(end_page: int):
        text = "\n".join(buf).strip()
        if text or name != "Front matter":
            sections.append(Section(name=name, page_start=start, page_end=end_page, text=text))

    for page_no, page in enumerate(pages, start=1):
        lines = page.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            s = line.strip()
            i += 1
            inline = _ABSTRACT_INLINE.match(s)
            if inline:
                close(page_no)
                name, start, buf = "Abstract", page_no, [inline.group(1)]
                continue
            found = _heading(s, last_number)
            if found is None and s.isdigit() and len(s) <= 2 and i < len(lines):
                found = _heading(f"{s} {lines[i].strip()}", last_number)
                if found is not None:
                    i += 1  # the title line was consumed together with its number
            if found is not None and name == "References" and found[1].lower() != "appendix":
                found = None  # table captions and appendix text after the reference list are not new sections
            if found is not None:
                number, title = found
                close(page_no)
                if number is not None:
                    last_number = number
                name, start, buf = title, page_no, []
                continue
            buf.append(line)
    close(len(pages) or 1)
    return sections
