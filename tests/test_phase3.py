import json
import re

from core.analysis.literature import publication_trends, rank_related, uncited_similar
from core.analysis.novelty import assess_novelty
from core.assistant.agent import MAX_SEARCHES, answer_question
from core.assistant.rag import PaperIndex
from core.ingest.parser import parse_pdf
from core.llm.base import LLMResult
from core.models import RelatedPaper
from core.retrieval.bm25 import BM25

ABSTRACT = ("We propose a neural ranking model for documents and show that it outperforms strong baselines "
            "on a public benchmark dataset with large margins.")


def test_bm25_ranks_relevant_document_first():
    docs = ["cats and dogs are pets", "a neural ranking model for documents", "stock market prices fell"]
    top = BM25(docs).top("neural ranking of documents", 2)
    assert top[0][0] == 1 and all(s > 0 for _, s in top)
    assert BM25(docs).top("zzz unknown", 3) == []


def test_chunks_carry_section_and_page(pdf_bytes):
    index = PaperIndex(parse_pdf(pdf_bytes))
    assert index.chunks and all(c.page for c in index.chunks)
    assert not any(c.section.lower() == "references" for c in index.chunks)
    hit = index.retrieve("compared against several baselines", 2)[0]
    assert "baselines" in hit.text and hit.section in {"Abstract", "Introduction"}
    assert index.get(hit.id) is hit and index.get(0) is None and index.get(10_000) is None


def test_uncited_similar_and_trends(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    missing = RelatedPaper(title="Neural ranking study of documents with a neural model", year=2021,
                           abstract=ABSTRACT, source="x")
    cited = RelatedPaper(title="A neural ranking method for search engines", year=2020, source="x")
    unrelated = RelatedPaper(title="Protein folding with graph networks", year=2019, abstract="proteins fold", source="x")
    # Best search hit but a different topic: must not be reported just because it ranks first.
    offtopic = RelatedPaper(title="Quit when you can: efficient ensembles ordering optimization", year=2018,
                            abstract="ensembles of models ordering", source="x")
    assert uncited_similar(paper, [offtopic]) == []
    found = uncited_similar(paper, [missing, unrelated])
    assert len(found) == 1 and "Neural ranking study of documents" in found[0].text and found[0].status == "uncertain"
    assert uncited_similar(paper, [cited], min_similarity=0) == []  # its title is in the reference list
    assert rank_related(paper, [unrelated, missing])[0][0] is missing
    trends = publication_trends(paper, [missing, cited, unrelated])
    assert trends["related_by_year"] == {2019: 1, 2020: 1, 2021: 1}
    assert trends["references_by_year"] == {2018: 1, 2019: 1, 2020: 1}


class NoveltyLLM:
    name, model = "fake", "t"

    def complete(self, system, user, *, json_schema=None, temperature=0.2):
        out = {"overlaps": [
            {"related": 1, "overlap": "high", "reason": "same method", "quote": "outperforms strong baselines on a public benchmark"},
            {"related": 1, "overlap": "high", "reason": "x", "quote": "This sentence is nowhere in the related abstract."},
            {"related": 9, "overlap": "high", "reason": "x", "quote": ABSTRACT},
            {"related": 1, "overlap": "low", "reason": "x", "quote": ABSTRACT}]}
        return LLMResult(text=json.dumps(out), model="t")


def test_novelty_overlap_needs_quote_from_related_abstract(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    rel = [RelatedPaper(title="Neural ranking of documents", year=2021, abstract=ABSTRACT, source="x")]
    findings, dropped, answered = assess_novelty(paper, rel, [], {"contributions": ["a ranking model"]}, NoveltyLLM())
    assert answered and dropped == 3
    assert len(findings) == 1 and findings[0].kind == "major" and findings[0].evidence[0].source == "related_work"
    assert assess_novelty(paper, [], [], None, NoveltyLLM()) == ([], 0, True)  # nothing to compare against


class QALLM:
    """Scripted model: search once, then answer citing the first passage it was shown."""
    name, model = "fake", "t"

    def __init__(self, answer="It compares against several baselines.", mode="good"):
        self.answer, self.mode, self.calls = answer, mode, 0

    def complete(self, system, user, *, json_schema=None, temperature=0.2):
        self.calls += 1
        if self.mode == "junk":
            return LLMResult(text="I do not know", model="t")
        if self.mode == "loop":
            return LLMResult(text=json.dumps({"action": "search", "query": "more"}), model="t")
        first = re.search(r"\[P(\d+)\]", user)
        if "Searches left: 3" in user and self.mode == "good":
            return LLMResult(text=json.dumps({"action": "search", "query": "baselines"}), model="t")
        if self.mode == "plain_only" and self.calls <= 2:  # ask_json retries once, so fail both attempts
            return LLMResult(text="not json", model="t")
        cited = [int(first.group(1))] if first else []
        return LLMResult(text=json.dumps({"action": "answer", "answer": self.answer, "cited": cited}), model="t")


def test_agent_searches_then_answers_with_citations(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    r = answer_question(paper, "What baselines are used?", QALLM())
    assert r.mode == "agentic" and r.citations and r.citations[0].page
    assert [s["tool"] for s in r.steps] == ["search_paper", "search_paper"] and r.warnings == []


def test_agent_flags_invented_numbers_and_missing_citations(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    r = answer_question(paper, "How many baselines?", QALLM(answer="It uses 99 baselines."))
    assert any("99" in w for w in r.warnings)

    class Uncited(QALLM):
        def complete(self, system, user, **k):
            return LLMResult(text=json.dumps({"action": "answer", "answer": "It is great.", "cited": [777]}), model="t")
    r2 = answer_question(paper, "Is it good?", Uncited())
    assert r2.citations == [] and any("do not exist" in w for w in r2.warnings) and any("unsupported" in w for w in r2.warnings)


def test_agent_fallbacks_and_step_limit(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    plain = answer_question(paper, "What baselines are used?", QALLM(mode="plain_only"))
    assert plain.mode == "plain" and plain.citations
    failed = answer_question(paper, "What baselines are used?", QALLM(mode="junk"))
    assert failed.mode == "failed" and failed.citations  # closest passages are still shown
    looping = QALLM(mode="loop")
    r = answer_question(paper, "What baselines are used?", looping)
    assert r.mode == "failed" and looping.calls <= (MAX_SEARCHES + 1) * 2 + 2  # never loops forever
    assert sum(s["tool"] == "search_paper" for s in r.steps) <= MAX_SEARCHES + 1
