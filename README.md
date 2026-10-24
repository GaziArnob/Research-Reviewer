# Research Reviewer

AI-assisted paper review tool. Phase 1 is fully rule-based and free (no LLM). See [ARCHITECTURE.md](ARCHITECTURE.md).

## Web app (multi-page, multi-user)

```powershell
powershell -File scripts\pg_start.ps1          # the project's PostgreSQL (port 5434)
.\.venv\Scripts\python.exe scripts\dev_web.py  # http://localhost:8000   (add --sqlite to try it on a throw-away database)
```

Pages: landing, Trust & privacy, Plans, Terms/Privacy drafts, sign up / sign in, dashboard, New review (multi-file + arXiv), Papers list with search and status,
a paper with tabs (Overview, Findings with filters, Claims & evidence, Literature, Ask the paper, Review form per venue with Markdown/DOCX export, Activity),
Settings (profile, password, data export, account deletion) and Admin (users, plans, system health, failures).

Design decisions: papers run in a background worker with live progress; every paper route checks ownership (404 for other users' ids); CSRF tokens on every POST; scrypt password hashing,
login throttling, strict CSP (no inline scripts or styles), session cookie `HttpOnly` + `SameSite=Lax`; the PDF is deleted after processing; the first account becomes admin; ratings are never prefilled;
venue AI rules hide LLM text where the venue forbids it. Plans (`webapp/plans.py`) enforce monthly paper quotas; there are no prices or payments yet.
Tests: `tests/test_webapp.py` (access control, CSRF, upload to result, exports, quota, account deletion).

Not built yet, needed before charging real users: HTTPS (then `COOKIE_SECURE=true`), email verification and password reset, billing, a job queue shared by several servers (the worker is in-process),
a rate limiter shared by several servers (login throttling is in memory), backups, monitoring, a lawyer's review of the Terms/Privacy drafts, and a security review.
The Streamlit app (`app/ui/main.py`) still works as an internal console.

## Convert a manuscript to a venue template (authors)

Web app, "Convert format": upload your own PDF, pick a venue, get a ZIP with `main.tex` (venue preamble, abstract, sections, `\cite` for numbered citations, reference list),
`images/` (embedded bitmaps matched to figure captions when a page has as many images as captions), `main.docx` and `CONVERSION_REPORT.md`.
Templates in `paper_templates/*.json`: IEEE conference and IEEE journal (IEEEtran), ACM (acmart sigconf), NeurIPS 2025, ICML 2025, ICLR 2025, ACL/ARR, CVPR 2025, AAAI-26.
Each file says which rules were read from the official page (page limits, double-blind, ACL's A4/2-column/11 pt layout, ...) and which are unverified (usually the exact class options and style-file names).
Honest limits: a PDF has no structure, so equations, table contents and vector figures are not recovered (placeholders and plain text); text after the reference list is not converted;
the tool does not compile for you; the venue's style file must be downloaded from the venue; page counts are rough estimates. Compile check: the IEEE conference, IEEE journal and ACM outputs were compiled for real with Tectonic 0.17.0 (`tools/tectonic/`, ignored by git; two real papers, both compiled and rendered in two columns); the other venues' style files are not part of TeX distributions, so their output is untested.
The PDF is converted in memory and never stored. Code: `core/convert/`; tests: `tests/test_convert.py`.
There is no IEEE-conference *review* form: IEEE has no single reviewer form (each conference or journal sets its own), so only the paper templates cover IEEE conferences.

## Making the results stronger (what exists and how well it worked)

- **Speed:** reference lookups and related-paper search run in parallel (reference check about 80 s -> about 10 s on a 40-reference paper).
- **More checks without a model:** `core/checks/methodology.py` (tuning on the test set, copied baselines, selected examples, human participants without ethics wording, pretrained models without contamination discussion) and an abstract-number consistency check. They are keyword hints with a quote.
- **Related work from OpenAlex's citation graph** (`related_by_graph`), with abstracts, ahead of keyword search.
- **Self-consistency:** `LLM_CONSENSUS_RUNS=3` runs the reviewer three times and keeps only points that two runs agree on (`core/review/consensus.py`). Test on 6 papers: it kept 18 weakness/question comments instead of 39 and their overlap with human reviews did not improve (5.6% vs 17.9%, too few papers to be sure), so it stays OFF by default and is not recommended with a 3B model.
- **Annotated PDF:** on the Findings tab, upload the same PDF again and get it back with every evidence quote highlighted and the finding as a note. The hash must match the reviewed file; nothing is stored.
- **Reviewer votes:** Useful / Not useful on every finding, summarised per check in the Admin page. This is how the checks can be judged on real use.
- **Overlap with human reviews** (`python -m eval.fetch_reviews`, `python -m eval.overlap_report`): how much of what the official reviewers wrote is also said by the tool, compared with the chance level. `python -m eval.compare_models --models a b` compares local models the same way.
- **Reporting checklist instead of a score:** the overview shows 15 standard items (code, datasets, split, baselines, ablation, seeds, runs, variance, tests, compute, limitations, ethics ...) as Found / Partly / Not found with the quote (`core/checks/completeness.py`). It is keyword presence, not quality: the model's numeric score had no link to human ratings (Spearman -0.19), so it is no longer shown in the overview.
- **Critique-only overlap** (`python -m eval.overlap_report --critique-only`): counts only human sentences that criticise or ask, not summaries. Result on 20 papers: LLM review comments match 11.5% vs 0.5% chance (the earlier 26-30% was partly shared topic words); rule checks stay at chance (0.3% vs 0.2%).
- Tried and rejected: PyMuPDF's table finder cuts decimals and merges cells on real LaTeX papers, so tables are not converted automatically.

Not done because they need a download or a service that is not here: a sentence-embedding or NLI model (PyTorch, about 2 GB), GROBID (needs Java), equation OCR (Nougat), other Ollama chat models for comparison.

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then edit CONTACT_EMAIL and DATABASE_URL
```

## PostgreSQL (free)

Option 0, project-local Postgres (what this repo is set up with). It uses the installed PostgreSQL 18 binaries but its own data folder `data\pgdata` and port 5434, so it never touches other Postgres databases on the PC:

```powershell
powershell -File scripts\pg_start.ps1    # start
powershell -File scripts\pg_stop.ps1     # stop
```

Credentials live only in `.env` (`DATABASE_URL`). Start it after every reboot before running the app.

Option A, Docker (start Docker Desktop first):

```powershell
docker compose up -d db       # listens on localhost:5433, user/password/db = reviewer
```

Option B, your own PostgreSQL install: create a database and user, then put the URL in `.env`:

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/DBNAME
```

Create tables:

```powershell
.\.venv\Scripts\python.exe -m core.storage.db init
```

Without a reachable database the pipeline still works and caches in memory.

## Run

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/ui/main.py
.\.venv\Scripts\python.exe -m pytest -q
```

## LLM review (Phase 2, free with Ollama)

1. Install Ollama (https://ollama.com) and pull a model that fits your PC, for example `ollama pull qwen2.5:3b` (about 2 GB, suits 8 GB RAM / 4 GB VRAM).
2. In `.env` set `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=qwen2.5:3b`, `OLLAMA_NUM_CTX=8192`.
3. In the UI, "Run LLM review" becomes available: summary, claims, strengths/weaknesses/questions, draft scores.

Every strength/weakness/claim must carry a quote that exists in the paper; the evidence verifier (`core/evidence/verifier.py`) deletes the rest and reports how many were dropped. Scores are an uncalibrated draft, and nothing is final until a named reviewer clicks "Approve". Small local models give weaker reviews than large ones; the checks above do not depend on them.

## Benchmark and calibration (Phase 4)

```powershell
python -m eval.fetch_peerread                 # 78 ICLR 2017 papers + official reviewer ratings (PeerRead, ~170 MB)
python -m eval.run_benchmark                  # rule-based checks on all papers (minutes)
python -m eval.run_benchmark --limit 20 --llm # LLM review on 20 papers (about 1 hour with qwen2.5:3b)
python -m eval.report --dir results_llm --fit # metrics + calibration fit (saved only if it generalises)
```

OpenReview's own API refuses automated requests (bot challenge), so PeerRead, which republishes the ICLR 2017 OpenReview reviews, is used instead.
Result with `qwen2.5:3b` on 20 papers: score vs human mean Spearman -0.19 (95% CI -0.65 to 0.32), accepted-vs-rejected AUC 0.39,
only 4 distinct scores. The model's numeric score carries no usable signal, a leave-one-out calibration fit did not beat predicting
the mean (MAE 1.24 vs 1.17), so it is not used and the UI warns about the score. The evidence verifier dropped 67 of 283 LLM comments.
Word count alone correlates with human rating (Spearman 0.38 on 78 papers). Details: `data/benchmark/report_*.md`.

## Conference templates

`templates/*.json`: NeurIPS 2025, ICML 2025 (main and position), ACL Rolling Review, ICLR 2025, CVPR, AAAI (reproducibility checklist plus reviewer guidelines), IEEE journals (general criteria) and the IEEE CPMT Transactions form.
Each template also has a `policy` block taken from the venue's own reviewer page: ICML, ARR, CVPR and IEEE forbid LLM-written review text, so the export hides everything an LLM wrote for them (rule-based checks and quote-verified evidence stay); ICLR allows LLMs as a general assist tool; NeurIPS and AAAI pages are silent. ARR and IEEE also discourage sending confidential content to third-party services, so turn the online checks off for those papers.
Each file records what was checked against official pages and when (`provenance`), and every field says whether it is `official`, `secondary` or `unverified`.
Verified: NeurIPS, ICML, ARR forms and scales; the AAAI-26 checklist items. Only partly verified: ICLR (the official guide does not list the form fields or scales), CVPR (criteria only), AAAI (review form). Nothing unverified is given scale values.
The UI builds the form for the chosen venue, prepares the evidence-linked comments per field, answers checklist items by keyword hints, and exports Markdown or DOCX. Scores, ratings, confidence and the recommendation are always left for the human reviewer.
ICML and ARR/ACL forbid writing reviews with LLMs: treat the export as evidence, not as the review text.
To add a venue, copy a template JSON, fill the fields from the official reviewer page, and set `provenance.level` honestly.

## Phase 1 extras

- Several PDFs or arXiv ids at once; a summary table lists all papers.
- Scanned PDFs are read with OCR (RapidOCR, pure pip, offline). It is slow, roughly 10-15 s per page on a laptop CPU, and `OCR_ENABLED=false` turns it off.
- Writing hints without Java or LanguageTool: repeated words, very long sentences, readability, mixed US/UK spelling, inconsistent hyphenation, short captions, possible typos.
- "Delete this paper's data" removes cached results, review drafts and the paper row; a hash-only audit entry remains. The PDF itself is never stored.
- Restart the Streamlit server after changing code under `core/` (it keeps old modules in memory).

## What Phase 1 does

Parse PDF (hidden text removed) → validation → injection scan → preflight → reproducibility and statistics hints → citation matching → Crossref reference check → related-paper search (Semantic Scholar, arXiv, OpenAlex) → findings with status labels and JSON export. Results are cached in PostgreSQL.
