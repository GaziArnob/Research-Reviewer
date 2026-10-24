import io

import pymupdf
import pytest

from core.annotate import annotate_pdf
from core.checks.methodology import run_consistency, run_methodology
from core.http import Response
from core.models import Evidence, Finding, ParsedPaper, Ref, Section
from core.references import crossref, fake_detect
from core.retrieval import openalex
from core.review.consensus import merge_runs
from eval.overlap import evaluate


def _paper(body: str, abstract: str = "", refs=()) -> ParsedPaper:
    secs = [Section(name="Abstract", page_start=1, page_end=1, text=abstract),
            Section(name="Introduction", page_start=1, page_end=1, text=body)]
    return ParsedPaper(sha256="x", filename="f", pages=[abstract + "\n" + body], sections=secs, references=list(refs))


def test_methodology_signals_and_quiet_text():
    bad = _paper("We tuned the learning rate on the test set and report the best test results. "
                 "Baseline numbers were taken from the original papers. We used 40 participants for the user study. "
                 "We fine-tune a pretrained BERT model on selected examples of the data.")
    texts = " ".join(f.text for f in run_methodology(bad))
    assert "test set" in texts and "copied" in texts and "ethics approval" in texts and "contamination" in texts
    assert [f.kind for f in run_methodology(bad) if "test set" in f.text] == ["major"]
    ok = _paper("We tune hyper-parameters on a held-out validation set. Participants gave informed consent under IRB approval.")
    assert run_methodology(ok) == []


def test_abstract_numbers_must_appear_in_the_body():
    p = _paper("Our model reaches 28.4 BLEU on the benchmark and trains in 3.5 days.",
               abstract="We reach 28.4 BLEU, improve by 2.0 points and train in 3.5 days on 8 GPUs in 2017.")
    findings = run_consistency(p)
    assert len(findings) == 1 and "2.0" in findings[0].text  # 28.4 and 3.5 are repeated, the year and 8 are ignored
    assert run_consistency(_paper("text", abstract="")) == []


def _f(text, quote="", kind="minor", status="verified"):
    return Finding(module="review", kind=kind, text=text, status=status, evidence=[Evidence(quote=quote)] if quote else [])


def test_consensus_keeps_only_points_repeated_across_runs():
    runs = [[_f("Only one dataset is used for evaluation", "evaluated on WMT 2014 only"), _f("The title is boring")],
            [_f("Evaluation uses a single dataset only", "evaluated on WMT 2014 only"), _f("Typos in section 3")],
            [_f("Just one dataset is used in the evaluation", "evaluated on WMT 2014 only"), _f("Typos in section 3 and 4")]]
    kept = merge_runs(runs, min_votes=2)
    texts = [f.text for f in kept]
    assert any("dataset" in t for t in texts) and any("Typos" in t for t in texts) and not any("title" in t for t in texts)
    dataset = next(f for f in kept if "dataset" in f.text)
    assert dataset.confidence == 1.0 and dataset.status == "verified"
    typos = next(f for f in kept if "Typos" in f.text)
    assert typos.confidence == pytest.approx(0.67, abs=0.01) and typos.status == "needs_verification"
    assert merge_runs([[_f("a unique remark about notation")]], min_votes=2) == []


def test_annotated_pdf_highlights_quotes_and_counts_misses():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 100), "We evaluate the method on a single public dataset with strong baselines.", fontsize=11)
    pdf = doc.tobytes()
    findings = [Finding(module="review", kind="major", text="Only one dataset.", evidence=[
                    Evidence(quote="evaluate the method on a single public dataset", page=1)]),
                Finding(module="review", kind="minor", text="Ghost", evidence=[Evidence(quote="this text is not in the paper at all", page=1)]),
                Finding(module="x", kind="flag", text="from a related paper", evidence=[Evidence(quote="evaluate the method", source="related_work")])]
    out, stats = annotate_pdf(pdf, findings)
    assert stats == {"highlighted": 1, "not_found": 1}
    reopened = pymupdf.open(stream=out, filetype="pdf")  # keep the document alive while reading its annotations
    infos = [a.info for a in reopened[0].annots()]
    assert len(infos) == 1 and "Only one dataset." in infos[0]["content"] and infos[0]["title"].startswith("major")


def test_references_are_checked_in_parallel_and_keep_their_order(monkeypatch):
    refs = [Ref(key=str(i), raw=f"Author {i}. A reasonably long title number {i}. 2020.", title=f"A reasonably long title number {i}") for i in range(1, 13)]
    refs[4] = Ref(key="5", raw="Doe. Something. doi:10.1/zzz", title=None, doi="10.1/zzz")
    seen = []

    def fake_search(title):
        seen.append(title)
        n = int(title.rsplit(" ", 1)[1])
        items = [] if n % 4 == 0 else [{"title": [title]}]
        return Response(200, {"message": {"items": items}})

    monkeypatch.setattr(crossref, "search_title", fake_search)
    monkeypatch.setattr(crossref, "get_work", lambda doi: Response(404, None))
    findings = fake_detect.verify_references(_paper("x", refs=refs))
    texts = " ".join(f.text for f in findings)
    assert "10.1/zzz was not found" in texts and "(4, 8, 12)" in texts  # unmatched keys listed in the original order
    assert len(seen) == 11


def test_openalex_abstract_and_graph(monkeypatch):
    assert openalex.abstract_text({"We": [0], "study": [1], "attention": [2, 4], "and": [3], "more": [5]}) == "We study attention and attention more"
    assert openalex.abstract_text(None) is None
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        if "filter" in params:
            return Response(200, {"results": [{"title": "BERT", "publication_year": 2019, "id": "https://openalex.org/W2",
                                               "abstract_inverted_index": {"Bidirectional": [0], "encoders": [1]}}]})
        return Response(200, {"results": [{"title": "Attention Is All You Need", "related_works": ["https://openalex.org/W1", "https://openalex.org/W2"]}]})

    monkeypatch.setattr(openalex, "get", fake_get)
    out = openalex.related_by_graph("Attention Is All You Need", 5)
    assert [p.title for p in out] == ["BERT"] and out[0].abstract == "Bidirectional encoders" and out[0].source == "openalex-graph"
    assert "W1|W2" in calls[1]["filter"]
    monkeypatch.setattr(openalex, "get", lambda *a, **k: Response(200, {"results": [{"title": "A completely different title", "related_works": ["x/W9"]}]}))
    assert openalex.related_by_graph("Attention Is All You Need") == []  # the title must match closely


def test_overlap_metric_beats_chance_only_for_real_matches():
    human = {"a": ["The authors evaluate only on a single dataset which limits generality of the claims."],
             "b": ["The proofs in the appendix are incomplete and the theorem statement seems wrong."],
             "c": ["Training details such as learning rate and batch size are missing from the paper."]}
    good = {"a": [("review", "Evaluation uses a single dataset, which limits how general the claims are")],
            "b": [("review", "The theorem proofs are incomplete and the theorem statement is wrong")],
            "c": [("review", "Missing training details: learning rate and batch size are not reported")]}
    res = evaluate(good, human, chance_rounds=10)
    assert res["recall"][0.25] == 1.0 and res["precision"][0.25] == 1.0
    assert res["recall_chance"][0.25] < 0.4 and res["by_module"]["review"]["match_rate"] == 1.0
    generic = {k: [("writing", "The paper has long sentences and some typos here")] for k in human}
    res2 = evaluate(generic, human, chance_rounds=10)
    assert res2["recall"][0.25] == 0.0 and res2["precision"][0.25] == 0.0


def test_completeness_checklist_counts_items_and_is_not_a_quality_score():
    from core.checks.completeness import run_completeness
    full = _paper("Code is available at github.com/x/y. We use the CIFAR dataset with a train/validation split. "
                  "We compare against strong baselines and run an ablation. Accuracy is the metric. "
                  "Limitations are discussed. Results are averaged over 5 runs with standard deviation.")
    r = run_completeness(full)
    by_id = {i["id"]: i["answer"] for i in r["items"]}
    assert by_id["code_available"] == "yes" and by_id["ablation"] == "yes" and by_id["seeds"] == "no"
    assert r["total"] == len(r["items"]) == 15 and r["yes"] + r["partial"] + r["missing"] == 15
    empty = run_completeness(_paper("We study a problem."))
    assert empty["yes"] == 0 and empty["missing"] == 15


def test_overlap_critique_filter_drops_summary_sentences(tmp_path):
    import json
    from eval.overlap import human_sentences, is_critique
    assert not is_critique("This paper proposes a new method for training deep networks on small data.")
    assert is_critique("However, the experiments only use one small dataset and lack any ablation study.")
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "p.json").write_text(json.dumps({"paper_id": "p", "comments": [
        "This paper proposes a new method for training deep networks on small data. "
        "However, the experiments only use one small dataset and lack any ablation study."]}))
    assert len(human_sentences("p", tmp_path)) == 2
    assert len(human_sentences("p", tmp_path, critique_only=True)) == 1
