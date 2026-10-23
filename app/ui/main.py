import json
from pathlib import Path

import pandas as pd
import streamlit as st

from core.assistant.agent import answer_question
from core.ingest.loader import fetch_arxiv
from core.llm.router import get_llm
from core.pipeline import get_parsed, run_pipeline
from core.storage.cache import Cache
from core.storage.papers import delete_paper_data
from core.templates.loader import list_templates, load_template
from core.templates.render import _scale_text, build_form, to_docx, to_markdown
from core.workflow.approval import approve, save_draft

def load_benchmark_summary():
    try:
        return json.loads((Path(__file__).resolve().parent.parent.parent / "data" / "benchmark" / "summary.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


st.set_page_config(page_title="Research Reviewer", layout="wide")
st.title("Research Reviewer")

cache = Cache()
llm = get_llm()
llm_ready = llm.name != "none"
st.caption("Rule-based checks always run (free). LLM review is optional. Every finding is a hint for a human reviewer.")
st.sidebar.write(f"Cache backend: **{cache.backend}**")
st.sidebar.write(f"LLM: **{llm.name}**" + (f" ({getattr(llm, 'model', '')})" if llm_ready else " (set LLM_PROVIDER=ollama in .env)"))
online = st.sidebar.checkbox("Online checks (Crossref, Semantic Scholar, arXiv, OpenAlex)", value=True)
use_llm = st.sidebar.checkbox("Run LLM review (summary, claims, comments, draft scores)", value=llm_ready, disabled=not llm_ready)
st.sidebar.info("Privacy: with Ollama the paper stays on this computer. Online checks send only the title, "
                "abstract sentence and reference titles/DOIs to public APIs. The PDF itself is never stored; "
                "use 'Delete this paper's data' to remove everything else.")

results: dict = st.session_state.setdefault("results", {})
review_ids: dict = st.session_state.setdefault("review_ids", {})
inputs: list[tuple[bytes, str]] = []

source = st.radio("Paper source", ["Upload PDFs", "arXiv links or ids"], horizontal=True)
if source == "Upload PDFs":
    for up in st.file_uploader("PDF files (you can select several)", type="pdf", accept_multiple_files=True) or []:
        inputs.append((up.getvalue(), up.name))
else:
    refs = st.text_area("arXiv links or ids, one per line (for example 1706.03762)")
    if refs.strip() and st.button("Download"):
        downloaded, errors = [], []
        for ref in [r.strip() for r in refs.replace(",", "\n").splitlines() if r.strip()]:
            try:
                downloaded.append(fetch_arxiv(ref))
            except ValueError as e:
                errors.append(f"{ref}: {e}")
        st.session_state["arxiv"] = downloaded
        for e in errors:
            st.error(e)
    for pdf, name in st.session_state.get("arxiv", []):
        inputs.append((pdf, name))
    if inputs:
        st.success("Ready: " + ", ".join(n for _, n in inputs))

if inputs and st.button(f"Run review on {len(inputs)} paper(s)", type="primary"):
    bar = st.progress(0.0)
    for i, (pdf, name) in enumerate(inputs):
        with st.spinner(f"{name} ({i + 1}/{len(inputs)}). LLM steps can take minutes on a local model..."):
            res = run_pipeline(pdf, name, online=online, cache=cache, llm=llm if use_llm else None)
        results[res.sha256] = res
        review_ids[res.sha256] = save_draft(res)
        st.session_state["selected"] = res.sha256
        bar.progress((i + 1) / len(inputs))

if results:
    rows = []
    for sha, r in results.items():
        rows.append({"File": r.filename, "Title": (r.title or "")[:60], "Status": r.status,
                     "Major": sum(f.kind == "major" for f in r.findings),
                     "Minor": sum(f.kind == "minor" for f in r.findings),
                     "Strengths": sum(f.kind == "strength" for f in r.findings),
                     "Questions": sum(f.kind == "question" for f in r.findings)})
    st.subheader("Papers")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    shas = list(results)
    if st.session_state.get("selected") not in shas:
        st.session_state["selected"] = shas[0]
    sha = st.selectbox("Show details for", shas, key="selected",
                       format_func=lambda s: results[s].filename)
    result = results[sha]

    st.subheader(result.title or result.filename)
    if result.status == "rejected":
        st.error("Paper rejected before review.")
    cols = st.columns(len(result.stats) or 1)
    for col, (k, v) in zip(cols, result.stats.items()):
        col.metric(k, len(v) if isinstance(v, list) else v)

    if result.understanding:
        st.markdown("### Summary")
        u = result.understanding
        st.write(u["summary"])
        c1, c2 = st.columns(2)
        c1.markdown("**Problem**\n\n" + (u["problem"] or "n/a"))
        c2.markdown("**Conclusion**\n\n" + (u["conclusion"] or "n/a"))
        for label, key in (("Objectives", "objectives"), ("Contributions", "contributions"), ("Key findings", "key_findings")):
            if u[key]:
                st.markdown(f"**{label}**\n" + "\n".join(f"- {x}" for x in u[key]))

    if result.claims:
        st.markdown("### Claims (each quote verified in the paper)")
        for cl in result.claims:
            where = f"page {cl.page}" if cl.page else (cl.section or "")
            st.markdown(f"- {cl.text}  \n  > {cl.quote} *({where})*")

    badge = {"verified": "quote verified", "needs_verification": "needs verification", "uncertain": "uncertain"}
    for kind, title in (("major", "Major"), ("minor", "Minor"), ("strength", "Strengths"),
                        ("question", "Questions for authors"), ("flag", "Flags")):
        items = [f for f in result.findings if f.kind == kind]
        if not items:
            continue
        st.markdown(f"### {title} ({len(items)})")
        for f in items:
            with st.expander(f"[{f.module}] {f.text[:110]}"):
                st.write(f.text)
                st.caption(f"Status: {badge[f.status]} · source: {f.source}")
                for e in f.evidence:
                    where = f"page {e.page}" if e.page else (e.section or "")
                    st.code(e.quote, language=None)
                    if where:
                        st.caption(where + (f" · section {e.section}" if e.page and e.section else ""))

    if result.scores:
        sc = result.scores
        with st.expander("Draft scores (optional, not reliable, hidden by default)", expanded=False):
            bench = load_benchmark_summary()
            if bench and bench.get("llm_model") == result.llm_model and (bench["spearman_raw"] is None or bench["spearman_raw"] < 0.3):
                rho = "undefined" if bench["spearman_raw"] is None else f"{bench['spearman_raw']:.2f}"
                st.warning(f"Do not rely on these numbers. On {bench['n']} {bench['dataset']} papers, this model's scores did "
                           f"not track human reviewers (Spearman {rho}, accepted-vs-rejected AUC {bench['auc_raw']:.2f}). "
                           "Use the evidence-linked comments instead of the score.")
            cols = st.columns(6)
            for col, (label, val) in zip(cols, [("Technical", sc.technical), ("Novelty", sc.novelty),
                                               ("Methodology", sc.methodology), ("Clarity", sc.clarity),
                                               ("Reproducibility", sc.reproducibility), ("Overall", sc.overall)]):
                col.metric(label, val)
            st.write(f"Draft recommendation: **{sc.recommendation}** (model confidence {sc.confidence:.0%})")
            if sc.raw_overall is not None:
                st.caption(f"Model's raw overall: {sc.raw_overall:g} → adjusted {sc.overall:g}. {sc.adjustment_note}")
            st.write(sc.justification)

    if result.related:
        st.markdown("### Related papers (for novelty checking)")
        for p in result.related:
            line = f"- {p.title} ({p.year or 'n/a'}, {p.source})"
            st.markdown(f"{line} [link]({p.url})" if p.url else line)

    if result.trends and (result.trends.get("related_by_year") or result.trends.get("references_by_year")):
        st.markdown("### Research trend (publication years)")
        t1, t2 = st.columns(2)
        for col, key, label in ((t1, "related_by_year", "Related papers found"), (t2, "references_by_year", "This paper's references")):
            data = result.trends.get(key) or {}
            if data:
                col.caption(label)
                col.bar_chart(pd.Series({int(y): n for y, n in data.items()}))

    st.markdown("### Ask the paper")
    parsed = get_parsed(cache, sha)
    if not llm_ready:
        st.caption("Set LLM_PROVIDER=ollama in .env to ask questions.")
    elif parsed is None:
        st.caption("The parsed paper is not in the cache (run the review again).")
    else:
        history = st.session_state.setdefault("qa_history", {}).setdefault(sha, [])
        question = st.text_input("Question about this paper", key=f"q_{sha}",
                                 placeholder="For example: Which datasets are used? What is the main result?")
        if st.button("Ask", key=f"ask_{sha}", disabled=not question.strip()):
            with st.spinner("Searching the paper and answering..."):
                history.insert(0, answer_question(parsed, question.strip(), llm))
        for qa in history:
            st.markdown(f"**Q: {qa.question}**")
            st.write(qa.answer)
            for w in qa.warnings:
                st.warning(w)
            for c in qa.citations:
                with st.expander(f"Passage P{c.chunk_id}: {c.section}, page {c.page}"):
                    st.write(c.text)
            with st.expander(f"Search steps ({qa.mode})"):
                st.write(qa.steps)

    with st.expander("Pipeline log"):
        st.write(result.step_log)

    st.markdown("### Conference review form")
    templates = list_templates()
    tpl_id = st.selectbox("Venue template", [t.id for t in templates], key=f"tpl_{sha}",
                          format_func=lambda i: next(t.name for t in templates if t.id == i))
    form = build_form(load_template(tpl_id), result, parsed if parsed is not None else get_parsed(cache, sha))
    if form.provenance_level == "verified":
        st.success("Fields and scales verified against the official page on " + load_template(tpl_id).provenance.checked + ".")
    else:
        st.warning("Only partly verified against official pages. Fields marked unverified must be checked against the live form.")
    st.caption(form.provenance_notes)
    if form.llm_policy == "forbidden":
        st.error("This venue forbids AI-written review text. LLM content is hidden in this form; do not use the "
                 "LLM review to draft your comments. " + " ".join(form.policy_notes[:1]))
    elif form.llm_policy == "allowed_assist":
        st.info(" ".join(form.policy_notes))
    elif form.policy_notes:
        st.info(" ".join(form.policy_notes))
    if form.third_party == "discouraged":
        st.warning("This venue's rules discourage sending confidential manuscript content to third-party services. "
                   "Untick 'Online checks' in the sidebar and run the paper again (the online checks send the title, "
                   "one abstract sentence and reference titles to public APIs).")
    for url in form.official_urls:
        st.markdown(f"[{url}]({url})")
    if form.criteria:
        with st.expander("Official evaluation criteria"):
            st.markdown("\n".join(f"- {c}" for c in form.criteria))
    for fld in form.fields:
        tag = "" if fld.source == "official" else f" ({fld.source})"
        with st.expander(f"{fld.label}{tag}" + ("  [human fills in]" if fld.human_fill else "")):
            if fld.guidance:
                st.caption(fld.guidance)
            if fld.type == "scale":
                st.write("Scale: " + (_scale_text(fld.scale) or "see live form"))
            if fld.type == "choice":
                st.write("Options: " + " / ".join(fld.choices))
            if fld.prepared:
                st.markdown("\n".join(fld.prepared))
    if form.checklist:
        st.markdown("**Checklist** (answers marked auto are keyword hints, verify each)")
        st.dataframe(pd.DataFrame([{"Group": r.group, "Item": r.text, "Answer": (r.answer or "manual") + (" (auto)" if r.auto else ""),
                                    "Note": r.note} for r in form.checklist]), use_container_width=True, hide_index=True)
    d1, d2 = st.columns(2)
    d1.download_button("Download form (Markdown)", to_markdown(form), file_name=f"{tpl_id}_{sha[:8]}.md", mime="text/markdown", key=f"md_{sha}")
    d2.download_button("Download form (DOCX)", to_docx(form), file_name=f"{tpl_id}_{sha[:8]}.docx", key=f"docx_{sha}",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    st.markdown("### Human approval")
    st.info("AI output is a draft. Nothing is final until a named human reviewer approves it. "
            "The recommendation is not shown by default; the reviewer decides from the evidence-linked comments.")
    review_id = review_ids.get(sha)
    name = st.text_input("Reviewer name", key=f"name_{sha}")
    if st.button("Approve this review", key=f"approve_{sha}", disabled=review_id is None or result.status == "rejected"):
        if approve(review_id, name):
            st.success(f"Approved by {name.strip()} (review #{review_id}).")
        else:
            st.error("Enter your name to approve.")
    if review_id is None:
        st.caption("Approval needs the PostgreSQL database (scripts\\pg_start.ps1).")

    st.download_button("Download JSON", result.model_dump_json(indent=2),
                       file_name=f"{result.sha256[:8]}_report.json", mime="application/json", key=f"dl_{sha}")

    with st.expander("Delete this paper's data"):
        st.write("Removes cached results, review drafts and the paper record from the database and from this "
                 "session. The PDF is not stored by the tool. A hash-only audit entry is kept.")
        sure = st.checkbox("I understand this cannot be undone", key=f"sure_{sha}")
        if st.button("Delete", key=f"del_{sha}", disabled=not sure):
            counts = delete_paper_data(sha, cache)
            results.pop(sha, None)
            review_ids.pop(sha, None)
            st.session_state.pop("selected", None)
            st.success(f"Deleted. {counts if counts is not None else 'No database; memory cache cleared.'}")
            st.rerun()
