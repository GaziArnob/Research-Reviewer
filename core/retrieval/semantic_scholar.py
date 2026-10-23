from core.config import settings
from core.http import get
from core.models import RelatedPaper

URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def search(query: str, limit: int = 8) -> list[RelatedPaper]:
    headers = {"x-api-key": settings.semantic_scholar_key} if settings.semantic_scholar_key else None
    r = get(URL, params={"query": query[:300], "limit": limit,
                         "fields": "title,year,venue,url,abstract"}, headers=headers)
    if r.status != 200 or not r.data:
        return []
    return [RelatedPaper(title=p["title"], year=p.get("year"), venue=p.get("venue") or None,
                         url=p.get("url"), abstract=p.get("abstract"), source="semantic_scholar")
            for p in r.data.get("data", []) if p.get("title")]
