# Research Reviewer: Architecture

An AI-assisted peer-review tool. It reads a paper (PDF), runs rule-based checks and LLM analysis, and produces an **evidence-linked, human-approved review report**.

Legend: 🟢 rule-based / free, 🟡 needs an LLM, 🔴 hard / always shown as "uncertain".

---

## 1. Design principles

1. **Evidence first.** Every review comment carries a quote + section/page. If the quote is not found in the paper, the comment is dropped.
2. **LLM is a plug-in.** All model calls go through one interface (`core/llm`). Ollama (free, local) today, Claude API later, no other code changes.
3. **Rule-based before LLM.** Validation, injection scan, reference checks, preflight and checklists need no LLM and run first.
4. **Human in the loop.** AI never issues a final recommendation; the reviewer approves it.
5. **Uncertainty is visible.** Every finding has a status: `verified`, `needs_verification`, `uncertain`.
6. **Cache everything.** A paper is parsed once; each LLM step is cached by (paper hash, step, prompt version, model).
7. **Privacy by default.** Local processing option, deletion controls, clear notice of where the paper goes.
8. **Free-first (current constraint).** Everything must run at $0 for now: local LLM (Ollama), local embeddings, FAISS/Chroma, free public APIs. No paid API is a hard dependency. Paid models are an optional upgrade behind `core/llm`, switched on later only if needed.

---

## 2. High-level architecture

```
                        ┌──────────────────────────────┐
                        │      UI (Streamlit / Web)    │
                        │ upload · dashboard · chat ·  │
                        │ editor · export · history    │
                        └──────────────┬───────────────┘
                                       │ HTTP
                        ┌──────────────▼───────────────┐
                        │        API (FastAPI)         │
                        │ auth · jobs · RBAC · audit   │
                        └──────────────┬───────────────┘
                                       │ enqueue
                        ┌──────────────▼───────────────┐
                        │   Pipeline Orchestrator      │
                        │ (step graph, cache, retries) │
                        └──────────────┬───────────────┘
   ┌───────────┬───────────┬───────────┼───────────┬────────────┬────────────┐
   ▼           ▼           ▼           ▼           ▼            ▼            ▼
┌────────┐ ┌────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐
│Ingest  │ │Checks  │ │Retrieval│ │Analysis │ │Review   │ │Integrity │ │Assistant│
│parse   │ │valid.  │ │S2/arXiv │ │(LLM)    │ │comments │ │similarity│ │Q&A (RAG)│
│OCR     │ │inject. │ │OpenAlex │ │claims   │ │scores   │ │images    │ │         │
│        │ │preflight│ │Crossref │ │method   │ │calibrate│ │AI-text   │ │         │
└───┬────┘ └───┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘ └────┬────┘
    └──────────┴───────────┴───────────┼───────────┴───────────┴────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │  Evidence Verifier           │
                        │ quote match · status labels  │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │ Report builder + Exporters   │
                        │ PDF · DOCX · JSON · templates│
                        └──────────────┬───────────────┘
                                       ▼
   ┌──────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
   │ LLM Gateway      │   │ Storage            │   │ Evaluation harness   │
   │ Ollama | Claude  │   │ PostgreSQL +       │   │ OpenReview benchmark │
   │ | others         │   │ files + vector idx │   │ vs human / other tool│
   └──────────────────┘   └────────────────────┘   └──────────────────────┘
```

---

## 3. Pipeline (order of steps for one paper)

| # | Step | Type | Output |
|---|---|---|---|
| 0 | Upload, hash, dedupe | 🟢 | `Paper` record |
| 1 | Validation (empty, too short, scanned, language) | 🟢 | pass / reject reason |
| 2 | Injection scan (hidden/white/tiny text, "ignore instructions") | 🟢 | sanitized text + flags |
| 3 | Parse: sections, tables, figures, equations, references (OCR if scanned) | 🟢 | `ParsedPaper` |
| 4 | Preflight: word count, missing sections, formatting, grammar, captions, terminology | 🟢 | `Finding[]` |
| 5 | Reference verification: DOI, metadata, in-text ↔ list match, fake citation, retractions | 🟢 | `Finding[]` |
| 6 | Related-work retrieval (Semantic Scholar, arXiv, OpenAlex) | 🟢 | `RelatedPaper[]` |
| 7 | Reproducibility + statistics checklist | 🟢 | `Finding[]` |
| 8 | Understanding: summary, problem, objectives, contributions, findings | 🟡 | `Understanding` |
| 9 | Claim extraction + claim→evidence tree | 🟡 | `Claim[]` |
| 10 | Novelty and gap analysis vs related work | 🟡 | `Finding[]` |
| 11 | Methodology, math, experiments, results analysis (+ figure/table reading) | 🟡/🔴 | `Finding[]` |
| 12 | Integrity: similarity, duplicate, citation manipulation, image duplication, AI-text | 🟢/🔴 | `Finding[]` (uncertain) |
| 13 | Multi-reviewer agents (N independent reviews) | 🟡 | `DraftReview[]` |
| 14 | Meta-reviewer: merge, de-duplicate, rank | 🟡 | `Review` |
| 15 | **Evidence verification:** drop comments whose quote isn't in the paper; set status | 🟢 | verified `Review` |
| 16 | Scoring + calibration + confidence | 🟡/🔴 | `Scores` |
| 17 | Report build + **human approval gate** | 🟢 | final report |

Steps 1-7 never need an LLM, so Phase 1 works fully offline.

---

## 4. Folder structure

```
Research Reviewer/
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
├── .env.example                 # model names, keys, paths (never commit .env)
├── app/
│   ├── ui/                      # Streamlit pages
│   │   ├── upload.py
│   │   ├── dashboard.py         # scorecard, major/minor, status badges
│   │   ├── editor.py            # editable comments, approval
│   │   ├── assistant.py         # paper Q&A chat
│   │   └── history.py           # versions, comparison
│   └── api/                     # FastAPI
│       ├── main.py
│       ├── routes/              # papers, reviews, export, auth
│       └── deps.py              # auth, RBAC
├── core/
│   ├── config.py                # loads .env
│   ├── models.py                # pydantic data models (section 6)
│   ├── pipeline.py              # orchestrator + cache
│   ├── llm/
│   │   ├── base.py              # LLMClient interface
│   │   ├── ollama_client.py
│   │   ├── claude_client.py
│   │   └── router.py            # pick model per step, multi-model compare
│   ├── ingest/
│   │   ├── loader.py            # PDF/arXiv/multi-upload
│   │   ├── parser.py            # PyMuPDF / GROBID
│   │   ├── ocr.py
│   │   ├── sections.py
│   │   ├── tables_figures.py
│   │   └── equations.py
│   ├── checks/
│   │   ├── validation.py
│   │   ├── injection.py
│   │   ├── preflight.py
│   │   ├── writing.py           # grammar, readability, captions, terminology
│   │   ├── reproducibility.py   # code/data/seed/split/leakage-risk keywords
│   │   └── statistics.py
│   ├── references/
│   │   ├── extract.py
│   │   ├── crossref.py          # DOI + metadata + retractions
│   │   ├── match.py             # in-text ↔ list
│   │   └── fake_detect.py
│   ├── retrieval/
│   │   ├── semantic_scholar.py
│   │   ├── arxiv.py
│   │   ├── openalex.py
│   │   ├── similarity.py        # embeddings + overlap
│   │   └── trends.py
│   ├── analysis/                # 🟡 each file = one prompt-driven step
│   │   ├── understanding.py
│   │   ├── claims.py            # claim tree
│   │   ├── novelty.py
│   │   ├── literature.py
│   │   ├── methodology.py
│   │   ├── math_check.py
│   │   ├── experiments.py
│   │   ├── figures_vision.py
│   │   └── writing_quality.py
│   ├── integrity/
│   │   ├── text_similarity.py
│   │   ├── image_duplication.py
│   │   ├── ai_text.py           # always "uncertain"
│   │   └── red_flags.py
│   ├── review/
│   │   ├── agents.py            # N reviewer agents
│   │   ├── meta_reviewer.py
│   │   ├── comments.py          # major/minor/section-wise/questions
│   │   ├── dedupe.py
│   │   ├── scoring.py           # rubric scores + recommendation
│   │   ├── calibration.py       # map to human score distribution
│   │   └── domain_rubrics.py
│   ├── evidence/
│   │   ├── verifier.py          # quote match, status, uncertainty
│   │   └── locator.py           # page/section references
│   ├── assistant/
│   │   ├── rag.py               # chunk + embed + retrieve
│   │   └── chat.py
│   ├── report/
│   │   ├── builder.py
│   │   ├── export_pdf.py
│   │   ├── export_docx.py
│   │   └── templates/           # ICLR, NeurIPS, ACL, journal
│   ├── workflow/
│   │   ├── approval.py          # human approval gate
│   │   ├── coi.py               # conflict of interest
│   │   ├── blind.py             # double-blind masking
│   │   ├── audit.py
│   │   └── policy.py            # conference AI-use policy notice
│   └── storage/
│       ├── db.py                # SQLAlchemy
│       ├── files.py             # encrypted file store
│       ├── cache.py
│       └── deletion.py
├── prompts/                     # versioned prompt + rubric files (no prompts in code)
│   ├── understanding.md
│   ├── claims.md
│   ├── review_agent.md
│   ├── meta_reviewer.md
│   └── rubrics/{general,ml,nlp,se,medicine}.yaml
├── eval/
│   ├── fetch_openreview.py
│   ├── run_benchmark.py
│   ├── metrics.py               # Spearman, hallucination rate, quote-match rate
│   └── compare_tools.py
├── data/
│   ├── cache/
│   ├── uploads/                 # git-ignored
│   └── benchmark/
└── tests/
```

---

## 5. Module → code map

| Module | Where |
|---|---|
| 1 Upload & Processing | `core/ingest`, `core/checks/validation.py`, `injection.py` |
| 2 Understanding | `core/analysis/understanding.py`, `claims.py` |
| 3 Novelty & Gap | `core/retrieval`, `core/analysis/novelty.py` |
| 4 Literature | `core/retrieval`, `core/analysis/literature.py` |
| 5 Methodology | `core/analysis/methodology.py`, `checks/reproducibility.py` |
| 6 Technical/Math | `core/analysis/math_check.py`, `claims.py` (claim tree) |
| 7 Experiments | `core/analysis/experiments.py`, `figures_vision.py`, `checks/statistics.py` |
| 8 Strengths/Weaknesses | `core/review/agents.py` |
| 9 Comments | `core/review/comments.py`, `dedupe.py` |
| 10 Scoring | `core/review/scoring.py`, `calibration.py` |
| 11 Writing | `core/checks/preflight.py`, `writing.py`, `analysis/writing_quality.py` |
| 12 Citations | `core/references/*` |
| 13 Integrity | `core/integrity/*` |
| 14 Assistant | `core/assistant/*`, `app/ui/assistant.py` |
| 15 Evidence | `core/evidence/*` |
| 16 Report | `core/report/*`, `app/ui/dashboard.py`, `editor.py`, `history.py` |
| 17 Security/Workflow | `core/workflow/*`, `core/storage/*`, `app/api/deps.py` |
| 18 Advanced | `core/llm/router.py`, `review/domain_rubrics.py`, `agents.py`, `retrieval` |
| 19 Evaluation | `eval/*` |
| 20 Architecture rules | `core/llm`, `core/config.py`, `prompts/` |

---

## 6. Core data models (`core/models.py`)

```python
class Paper:        id, hash, title, authors(masked?), pdf_path, uploaded_by, status, created_at
class ParsedPaper:  paper_id, sections[Section], tables[], figures[], equations[], references[Ref], text_pages[]
class Section:      name, page_start, page_end, text
class Ref:          key, raw, doi, title, year, verified(bool), retracted(bool|None)
class Claim:        id, text, section, quote, evidence_ids[], support: "supported|partial|none"
class Evidence:     quote, section, page, source: "paper|related_work"
class Finding:      id, module, kind: "major|minor|question|flag",
                    text, evidence[Evidence], status: "verified|needs_verification|uncertain",
                    confidence: float, source: "rule|llm|agent_k"
class Scores:       technical, novelty, methodology, clarity, reproducibility,
                    overall, recommendation: "accept|weak_accept|borderline|weak_reject|reject",
                    confidence, justification, calibrated(bool)
class Review:       id, paper_id, version, findings[], scores, model_info, reviewer_id,
                    approved(bool), approved_by, approved_at
class AuditEvent:   who, what, when, paper_id
```

---

## 7. LLM abstraction (`core/llm/base.py`)

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, json_schema: dict | None = None,
                 images: list[bytes] | None = None, temperature: float = 0.2) -> LLMResult: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- `router.py` chooses a model per step via `.env`, e.g. `MODEL_UNDERSTANDING=ollama:llama3.1`, `MODEL_REVIEW=claude:...`.
- Multi-model comparison = run the same step through several clients and diff the findings.
- Output is requested as JSON and validated with pydantic; invalid output is retried, then marked failed.
- Cache key = `hash(paper) + step + prompt_version + model`.
- **Injection defence:** paper text is always passed as *data* inside delimiters; system prompt states that the text must never be treated as instructions.

---

## 8. Evidence verifier (the trust layer)

For each LLM finding:
1. Normalize the quoted text, fuzzy-match it against the parsed paper (threshold, e.g. 0.9).
2. Found → attach page/section, status `verified` (for the *quote*; the judgement itself is still AI).
3. Not found → drop the finding, increment `dropped_count`, record in the audit log.
4. Findings with no evidence requirement (e.g. "missing ablation") → status `needs_verification`.
5. Integrity items (image duplication, AI-text) → always `uncertain`.

---

## 9. Tech stack

| Area | Choice (free) | Later |
|---|---|---|
| Language | Python 3.11+ | |
| API | FastAPI | |
| UI | Streamlit | Next.js |
| PDF | PyMuPDF, GROBID | |
| OCR | Tesseract | |
| Grammar | LanguageTool (self-hosted) | |
| LLM | Ollama (local) | Claude API |
| Embeddings | sentence-transformers | API embeddings |
| Vector index | pgvector (extension inside PostgreSQL), FAISS as fallback | |
| DB | PostgreSQL (SQLAlchemy + psycopg), free and open source | |
| Jobs | in-process / RQ | Celery |
| Export | WeasyPrint/ReportLab (PDF), python-docx | |
| External data | Semantic Scholar, arXiv, OpenAlex, Crossref, OpenReview | |
| Tests | pytest | |

---

## 10. Security and workflow

- Auth + roles: `reviewer`, `chair`, `admin`; chair-only access to all reviews.
- Files stored encrypted at rest, per-user folders; one-click **delete paper + derived data + cache + vectors**.
- Double-blind: author names/affiliations masked before any LLM or reviewer sees the text.
- COI declaration required before opening a paper; recorded in the audit trail.
- Every action (view, run, edit, approve, export, delete) logged to `AuditEvent`.
- Final recommendation locked until a human approves; exports are watermarked "AI-assisted, human-approved" or "DRAFT".
- Policy page shows conference/journal AI-use rules; user acknowledges before use.
- External APIs only receive reference metadata and short queries, never the full paper, unless the user selected a cloud LLM (shown in the privacy notice).

---

### Database (PostgreSQL)

One database, configured by `DATABASE_URL` in `.env`. Tables (SQLAlchemy models in `core/storage/db.py`):

| Table | Purpose |
|---|---|
| `papers` | id, sha256 (unique), filename, title, status, uploaded_by, created_at |
| `analysis_cache` | key (paper hash + step + prompt version + model), paper_sha, step, payload (JSONB), created_at |
| `reviews` | id, paper_sha, version, payload (JSONB: findings, scores), approved, approved_by, approved_at |
| `audit_events` | id, actor, action, paper_sha, detail (JSONB), created_at |
| `chunks` (Phase 3) | paper_sha, section, page, text, embedding (pgvector) for RAG |

JSONB keeps findings flexible while the pipeline is still changing. Running Postgres locally is free (installer or `docker compose up -d db`).

---

## 11. Evaluation (`eval/`)

1. Download OpenReview papers + human reviews (ICLR/NeurIPS).
2. Run the pipeline, per paper.
3. Metrics: Spearman correlation (AI vs human overall score), score-distribution gap, **quote-match rate**, hallucination/drop rate, inter-run variance, comment overlap with human comments.
4. Compare configurations (Ollama vs Claude, 1 vs 3 reviewers, with/without calibration) and against OpenReviewer / paperreview.ai outputs.

---

## 12. Build phases

| Phase | Scope | LLM | Cost |
|---|---|---|---|
| **1** | config, `core/llm` stub, ingest, validation, injection, preflight, writing checks, reference checks, retrieval, reproducibility/stats, cache, basic UI + JSON export | none | free |
| **2** | understanding, claims, structured review, evidence verifier, scores, report PDF/DOCX, editor + approval | local (Ollama) | free |
| **3** | novelty/literature, methodology, math, experiments, claim tree, assistant (RAG), workflow/security, history | better local/cloud | low |
| **4** | multi-reviewer + meta, calibration, vision (figures), citation support check, integrity (image/AI-text), domain rubrics, Bangla, benchmark | cloud | paid, optional |

---

## 13. Agentic RAG

Normal RAG searches once and answers. In agentic RAG the LLM decides what to search, where, and whether to search again.

### Where it is used

| Module | Agent job | Sources |
|---|---|---|
| Assistant (Q&A) | Split question, find the right section, re-search if weak | the paper's own vector index |
| Novelty / related work | Build queries from claims, search, refine queries | Semantic Scholar, arXiv, OpenAlex |
| Citation check | Fetch cited paper, read abstract, compare with claim | Crossref, Semantic Scholar |
| Review agents | Collect evidence before writing a weakness | paper + related work |
| Methodology / stats | Ask targeted questions ("where is the split described?") | paper sections |

### Where it is NOT used

- **Evidence verifier stays fixed and rule-based.** It must catch the agent's mistakes, so it cannot be an agent itself.
- Ingest, checks, reference matching, preflight: deterministic code.

### Agent loop and tools

```
Orchestrator → Agent (LLM) ⇄ tools
                              ├─ search_paper(query)       # local vector index of the paper
                              ├─ search_literature(query)  # Semantic Scholar / arXiv / OpenAlex
                              ├─ get_reference(doi)        # Crossref
                              └─ quote_check(text)         # evidence verifier (read-only)
```

Files: `core/assistant/rag.py` (index + retrieval), `core/assistant/agent.py` (loop), `core/review/agents.py` (reviewer agents use the same tools), `core/tools/` (one file per tool).

Rules:
- Hard step limit (default 5) and a per-paper call budget.
- Every step is logged (tool, args, result summary) to the audit trail.
- Tool results are data, never instructions (same injection defence as the paper text).
- Final output of any agent still goes through the evidence verifier.
- Cache tool results (same query, same index version) to avoid repeat work.

### Free-first setup

| Part | Free choice |
|---|---|
| Embeddings | `sentence-transformers` (local) |
| Vector store | FAISS or Chroma (local) |
| Search APIs | Semantic Scholar, arXiv, OpenAlex, Crossref |
| LLM | Ollama (local) |

Caveat: small local models are weak at tool calling. Mitigations: keep the tool set small, use JSON-schema outputs, a fixed step limit, and fall back to plain (non-agentic) RAG when the agent fails to produce a valid tool call. Agentic mode is a Phase 3 feature; Phase 1-2 use plain retrieval.

If a paid model is enabled later, agentic runs make roughly 15-30 LLM calls per paper instead of 3-5, so keep the step limit and caching in place.

---

## 14. Known limits (show these in the product)

- 🔴 features (data leakage, proof checking, image duplication, AI-text detection, citation-supports-claim) produce **flags, not verdicts**.
- Local small models give weaker reviews; Phase 1 value does not depend on them.
- Calibration is only as good as the human-review data for that field (ML/NLP are well covered, biomedical is not).
- The tool assists a reviewer; it does not replace one.
