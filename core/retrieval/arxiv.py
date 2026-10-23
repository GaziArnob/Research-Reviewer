import xml.etree.ElementTree as ET

from core.http import get
from core.models import RelatedPaper

URL = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def search(query: str, limit: int = 8) -> list[RelatedPaper]:
    r = get(URL, params={"search_query": f"all:{query[:200]}", "max_results": limit}, json=False)
    if r.status != 200 or not r.text:
        return []
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("a:entry", NS):
        title = " ".join((e.findtext("a:title", "", NS) or "").split())
        published = e.findtext("a:published", "", NS) or ""
        if title:
            out.append(RelatedPaper(
                title=title, year=int(published[:4]) if published[:4].isdigit() else None,
                venue="arXiv", url=e.findtext("a:id", None, NS),
                abstract=" ".join((e.findtext("a:summary", "", NS) or "").split()) or None,
                source="arxiv"))
    return out
