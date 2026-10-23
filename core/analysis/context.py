from core.models import ParsedPaper

# (label, section names to try, max characters). Small budgets keep local models usable.
UNDERSTANDING_PLAN = [("Abstract", ["abstract"], 2000), ("Introduction", ["introduction"], 3500),
                      ("Conclusion", ["conclusion", "conclusions"], 2500)]
CLAIMS_PLAN = UNDERSTANDING_PLAN + [("Results", ["results", "experiments", "experiment"], 3500)]
REVIEW_PLAN = [("Abstract", ["abstract"], 1800), ("Introduction", ["introduction"], 3000),
               ("Method", ["method", "methods", "methodology", "approach", "model architecture", "model",
                           "our approach", "proposed method", "architecture"], 4000),
               ("Experiments", ["experiments", "experiment", "experimental setup", "training", "evaluation",
                                "experimental results"], 3500),
               ("Results", ["results"], 3000), ("Discussion", ["discussion", "limitations"], 2000),
               ("Conclusion", ["conclusion", "conclusions"], 2000)]


def build_context(paper: ParsedPaper, plan: list[tuple[str, list[str], int]]) -> str:
    """Wrap selected sections in <paper_text> tags. Paper text is data, so delimiter look-alikes are removed."""
    parts = [f"Title: {paper.title}"] if paper.title else []
    used = False
    for label, names, limit in plan:
        section = paper.section(*names)
        if section and section.text.strip():
            text = " ".join(section.text.split())[:limit]
            parts.append(f'<section name="{label}">\n{text}\n</section>')
            used = True
    if not used:  # no recognised headings: fall back to the beginning of the paper
        parts.append(" ".join(paper.full_text.split())[:8000])
    body = "\n".join(parts).replace("<paper_text>", "").replace("</paper_text>", "")
    return f"<paper_text>\n{body}\n</paper_text>"
