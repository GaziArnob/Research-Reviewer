import io
import json

import pytest

from core.ingest.parser import parse_pdf
from core.models import Claim, Evidence, Finding, PipelineResult
from core.templates.autochecks import CHECKS, run_check
from core.templates.loader import TEMPLATE_DIR, list_templates, load_template
from core.templates.render import build_form, to_docx, to_markdown


def _result(**kw) -> PipelineResult:
    base = dict(sha256="x", filename="f.pdf", title="A Neural Ranking Study", understanding={
        "summary": "A ranking study.", "contributions": ["a model"], "problem": "", "objectives": [], "key_findings": [], "conclusion": ""},
        claims=[Claim(text="works well", quote="The approach is compared against several baselines", page=1)],
        findings=[
            Finding(module="review", kind="strength", text="Clear task.", status="verified", evidence=[Evidence(quote="We study a method for ranking documents", page=1)]),
            Finding(module="review", kind="major", text="One dataset only.", status="verified"),
            Finding(module="writing", kind="minor", text="Repeated words."),
            Finding(module="review", kind="question", text="Why this dataset?"),
        ])
    base.update(kw)
    return PipelineResult(**base)


def test_every_template_is_valid_and_honest_about_what_was_verified():
    templates = list_templates()
    assert {t.id for t in templates} == {"neurips2025", "icml2025", "icml2025_position", "arr_acl", "iclr2025", "cvpr2025",
                                         "aaai_checklist", "ieee_journals", "ieee_cpmt_transactions"}
    for t in templates:
        assert t.provenance.level in ("verified", "partial") and t.provenance.official_urls and t.provenance.checked
        keys = [s.key for s in t.sections]
        assert len(keys) == len(set(keys))
        for s in t.sections:
            assert s.source in ("official", "secondary", "unverified")
            if s.type == "scale":
                assert s.scale and s.scale["min"] < s.scale["max"]
        for item in t.checklist:
            if item.auto:
                assert item.auto in CHECKS
        assert t.policy.llm_review_text in ("forbidden", "allowed_assist", "not_stated") and t.policy.notes
        if t.provenance.level == "partial":  # a partly verified template must say what is missing
            assert "not" in t.provenance.notes.lower() and "NOT" in t.provenance.notes or "no " in t.provenance.notes.lower()


def test_known_scales_match_the_official_pages():
    n = load_template("neurips2025")
    overall = next(s for s in n.sections if s.key == "overall")
    assert overall.scale["max"] == 6 and overall.scale["labels"]["6"] == "Strong Accept"
    assert [s.label for s in n.sections][:2] == ["Summary", "Strengths and Weaknesses"]
    icml = load_template("icml2025")
    assert next(s for s in icml.sections if s.key == "overall_recommendation").scale["max"] == 5
    assert not any(s.key == "confidence" for s in icml.sections)  # not stated for the Main Track form
    arr = load_template("arr_acl")
    assert next(s for s in arr.sections if s.key == "overall_assessment").scale["labels"]["3"] == "Findings"
    iclr = load_template("iclr2025")
    assert all(s.source == "unverified" for s in iclr.sections if s.key in ("rating", "confidence", "soundness"))
    assert all(not s.scale["labels"] for s in iclr.sections if s.type == "scale")  # no invented label values


def test_scores_are_never_prefilled_and_evidence_is_prepared(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    form = build_form(load_template("neurips2025"), _result(), paper)
    by = {f.key: f for f in form.fields}
    for key in ("overall", "confidence", "quality", "clarity"):
        assert by[key].human_fill and by[key].type == "scale"
    assert any("One dataset only." in l for l in by["strengths_weaknesses"].prepared)
    assert any("Clear task." in l for l in by["strengths_weaknesses"].prepared)
    assert by["questions"].prepared == ["- Why this dataset? [verify]"]
    assert any("Repeated words" in l for l in by["clarity"].prepared)
    md = to_markdown(form)
    assert "To be filled in by the human reviewer" in md and "deliberately left blank" in md
    assert "VERIFIED" in md and "neurips.cc" in md


def test_icml_claims_evidence_and_unverified_banner(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    aaai = build_form(load_template("aaai_checklist"), _result(), paper)  # AAAI page is silent on AI tools: content shown
    ev = next(f for f in aaai.fields if f.key == "evaluation")
    assert any("works well" in l for l in ev.prepared) and any("p.1" in l for l in ev.prepared)
    assert not aaai.llm_hidden
    md = to_markdown(build_form(load_template("iclr2025"), _result(), paper))
    assert "PARTIAL" in md and "unverified" in md and "check the live form" in md


def test_aaai_checklist_gates_and_hints(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    rows = {r.id: r for r in build_form(load_template("aaai_checklist"), _result(), paper).checklist}
    assert rows["t0"].answer == "no" and rows["t3"].answer == "NA"  # no theorems: dependent items are not applicable
    assert rows["d0"].answer == "yes" and rows["d1"].answer == ""  # gate yes, manual item stays unanswered
    assert rows["c0"].answer == "yes" and rows["c9"].auto and rows["g2"].auto is False
    assert rows["c4"].answer in ("yes", "no", "partial")
    ans, ev, note = run_check("variance", paper, None)
    assert ans == "no" and ev is None
    ans, ev, _ = run_check("datasets", paper, None)
    assert ans == "yes" and ev and "dataset" in ev.quote.lower()


def test_arr_checklist_uses_reference_findings():
    bad = _result(findings=[Finding(module="references", kind="major", text="DOI 10.1/x was not found in Crossref (reference 3).",
                                    evidence=[Evidence(quote="ref raw")])])
    ans, ev, note = run_check("references_exist", None, bad)
    assert ans == "no" and "1 DOI" in note
    assert run_check("references_exist", None, None)[0] == "unclear"


def test_exports_render(pdf_bytes):
    from docx import Document
    paper = parse_pdf(pdf_bytes)
    form = build_form(load_template("arr_acl"), _result(), paper)
    md = to_markdown(form)
    assert "Overall Assessment" in md and "Findings" in md and "| Item | Answer | Note |" in md
    doc = Document(io.BytesIO(to_docx(form)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "ACL Rolling Review" in text and "To be filled in by the human reviewer" in text
    assert len(doc.tables) == 1 and len(doc.tables[0].rows) == 7  # header + 6 checklist items


def test_template_files_are_plain_json():
    for p in TEMPLATE_DIR.glob("*.json"):
        assert json.loads(p.read_text(encoding="utf-8"))["id"] == p.stem


def test_venues_that_forbid_llm_text_get_no_llm_content(pdf_bytes):
    paper = parse_pdf(pdf_bytes)
    llm_finding = Finding(module="review", kind="major", text="LLM weakness.", status="verified", source="llm")
    rule_finding = Finding(module="statistics", kind="minor", text="No variance reported.", source="rule")
    res = _result(findings=[llm_finding, rule_finding])
    for tid in ("icml2025", "icml2025_position", "arr_acl", "cvpr2025", "ieee_journals", "ieee_cpmt_transactions"):
        form = build_form(load_template(tid), res, paper)
        text = to_markdown(form)
        assert form.llm_hidden and form.llm_policy == "forbidden", tid
        assert "LLM weakness." not in text and "A ranking study." not in text and "works well" not in text, tid
        assert "venue forbids LLM-written review text" in text or tid == "ieee_cpmt_transactions" or "Venue policy" in text
        assert "Venue policy on AI tools (forbidden)" in text
    shown = build_form(load_template("neurips2025"), res, paper)
    assert not shown.llm_hidden and "LLM weakness." in to_markdown(shown)
    assert build_form(load_template("iclr2025"), res, paper).llm_policy == "allowed_assist"
    for tid in ("arr_acl", "ieee_journals", "ieee_cpmt_transactions"):
        assert build_form(load_template(tid), res, paper).third_party == "discouraged"


def test_ieee_templates_match_the_pages_they_came_from():
    j = load_template("ieee_journals")
    rec = next(s for s in j.sections if s.key == "recommendation")
    assert rec.choices == ["Accept", "Revise (Major)", "Revise (Minor)", "Reject"] and j.provenance.level == "partial"
    assert not any(s.type == "scale" for s in j.sections)  # no numeric scale is stated by IEEE, so none is invented
    c = load_template("ieee_cpmt_transactions")
    assert next(s for s in c.sections if s.key == "quality").choices == ["Poor", "Marginal", "Acceptable", "Good", "Prize paper"]
    assert [s.label for s in c.sections][:6] == ["Originality", "Significance", "Completeness", "References", "Organization", "Clarity"]
