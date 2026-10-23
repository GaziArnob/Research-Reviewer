from typing import Callable, Optional

from core.analysis.literature import publication_trends, uncited_similar
from core.checks.injection import scan_injection
from core.checks.methodology import run_consistency, run_methodology
from core.checks.preflight import run_preflight
from core.checks.reproducibility import run_reproducibility
from core.checks.statistics import run_statistics
from core.checks.validation import looks_scanned, validate
from core.checks.writing import run_writing_checks
from core.config import settings
from core.ingest import ocr
from core.ingest.loader import sha256_of
from core.ingest.parser import parse_pdf
from core.llm.base import LLMClient
from core.models import Finding, ParsedPaper, PipelineResult, RelatedPaper
from core.review.run import run_llm_review
from core.references.fake_detect import verify_references
from core.references.match import match_citations
from core.retrieval.related import find_related
from core.storage.cache import Cache, audit, make_key
from core.storage.papers import register_paper

STEP_VERSION = "4"  # bump when a step's logic changes so old cached results are not reused


def _cached(cache: Cache, sha: str, step: str, compute: Callable[[], dict], log: list[str]) -> dict:
    key = make_key(sha, step, STEP_VERSION)
    hit = cache.get(key)
    if hit is not None:
        log.append(f"{step}: cache hit")
        return hit
    value = compute()
    cache.set(key, value, sha, step)
    log.append(f"{step}: computed")
    return value


def get_parsed(cache: Cache, sha: str) -> Optional[ParsedPaper]:
    """The parsed paper from the cache (OCR version first), for the Q&A assistant."""
    for step in ("parse_ocr", "parse"):
        data = cache.get(make_key(sha, step, STEP_VERSION))
        if data:
            return ParsedPaper(**data)
    return None


class _Log(list):
    """The step log; every new entry is also passed to `progress` so a UI can show live status."""

    def __init__(self, progress: Optional[Callable[[str], None]] = None):
        super().__init__()
        self._progress = progress

    def append(self, item: str) -> None:
        super().append(item)
        if self._progress:
            self._progress(item)


def run_pipeline(pdf_bytes: bytes, filename: str = "paper.pdf", *,
                 online: Optional[bool] = None, cache: Optional[Cache] = None,
                 llm: Optional[LLMClient] = None,
                 progress: Optional[Callable[[str], None]] = None) -> PipelineResult:
    """Rule-based steps always run (free). LLM steps run only when an `llm` client is passed."""
    online = settings.online_checks if online is None else online
    cache = cache or Cache()
    sha = sha256_of(pdf_bytes)
    log: list[str] = _Log(progress)
    log.append(f"cache backend: {cache.backend}")
    audit("pipeline_start", sha, {"filename": filename})

    parsed = ParsedPaper(**_cached(cache, sha, "parse",
                                   lambda: parse_pdf(pdf_bytes, filename).model_dump(), log))
    used_ocr = False
    if looks_scanned(parsed) and settings.ocr_enabled and ocr.available():
        parsed = ParsedPaper(**_cached(
            cache, sha, "parse_ocr",
            lambda: parse_pdf(pdf_bytes, filename, pages_override=ocr.ocr_pages(pdf_bytes)).model_dump(), log))
        used_ocr = True
    result = PipelineResult(sha256=sha, filename=filename, title=parsed.title)
    result.step_log = log  # same list object, so later log entries show up in the result
    if used_ocr:
        result.findings.append(Finding(
            module="ocr", kind="flag", status="uncertain",
            text="The PDF had no text layer, so the text was read with OCR. Recognition errors are possible "
                 "and can cause false findings (typos, missing references)."))

    ok, findings = validate(parsed)
    result.findings += findings
    if not ok:
        result.status = "rejected"
        register_paper(sha, filename, parsed.title, "rejected")
        audit("pipeline_rejected", sha)
        return result
    register_paper(sha, filename, parsed.title, "ok")

    def local(step: str, fn: Callable[[ParsedPaper], list[Finding]]) -> None:
        data = _cached(cache, sha, step, lambda: {"f": [f.model_dump() for f in fn(parsed)]}, log)
        result.findings += [Finding(**f) for f in data["f"]]

    local("injection", scan_injection)
    local("preflight", run_preflight)
    local("reproducibility", run_reproducibility)
    local("statistics", run_statistics)
    local("writing", run_writing_checks)
    local("methodology", run_methodology)
    local("consistency", run_consistency)
    local("citations", match_citations)

    if online:
        local("references", verify_references)
        rel = _cached(cache, sha, "related",
                      lambda: {"r": [p.model_dump() for p in find_related(parsed)]}, log)
        result.related = [RelatedPaper(**p) for p in rel["r"]]
        result.findings += uncited_similar(parsed, result.related)
        result.trends = publication_trends(parsed, result.related)
    else:
        log.append("online steps skipped (references, related work)")

    result.stats = {
        "words": parsed.word_count,
        "pages": len(parsed.pages),
        "sections": [s.name for s in parsed.sections],
        "references": len(parsed.references),
        "figures": len(parsed.figure_numbers),
        "tables": len(parsed.table_numbers),
        "hidden_spans": len(parsed.hidden_spans),
    }

    if llm is not None and llm.name != "none":
        run_llm_review(parsed, llm, cache, result, log)
    else:
        log.append("LLM review skipped (no LLM configured)")

    audit("pipeline_done", sha, {"findings": len(result.findings)})
    return result
