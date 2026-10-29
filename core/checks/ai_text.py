import re
import statistics

from core.models import Evidence, Finding, ParsedPaper

# Phrases that language models use far more often than scientists do. Every one of them also occurs in human writing,
# so a single hit means nothing: only a high density is reported.
MARKERS = [
    r"\bdelve[sd]?\b", r"\btapestry\b", r"\bit is (?:important|worth|crucial) to (?:note|mention|emphasi[sz]e)\b",
    r"\bin the realm of\b", r"\bunderscores?\b", r"\bpivotal\b", r"\bmultifaceted\b", r"\bplays? a (?:crucial|vital|pivotal|key) role\b",
    r"\ba testament to\b", r"\bnavigat(?:e|es|ing) the complexit", r"\bever-evolving\b", r"\bseamless(?:ly)?\b", r"\bin today'?s (?:fast-paced|digital|rapidly)",
    r"\bin conclusion,", r"\bfurthermore,", r"\bmoreover,", r"\badditionally,", r"\bholistic\b", r"\bgame-?chang", r"\bparadigm shift\b",
    r"\bit(?:'s| is) worth noting\b", r"\bgroundbreaking\b", r"\bcutting-edge\b", r"\bunlock(?:s|ing)? the (?:full )?potential\b",
    r"\bshed(?:s|ding)? light on\b", r"\bat the forefront\b", r"\bmeticulous(?:ly)?\b", r"\bintricate\b", r"\bcommendable\b",
]
_MARK = re.compile("|".join(MARKERS), re.I)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
PRED_MIN_WORDS = 150     # the language-model score needs less text than the word-list measures
MIN_WORDS = 800          # shorter texts give meaningless statistics
MARKER_DENSITY = 4.0     # marker hits per 1,000 words
MAX_UNIFORM_CV = 0.40    # coefficient of variation of sentence length below this is unusually uniform


def analyse(text: str) -> dict:
    """The raw numbers behind the finding; also used by tests."""
    flat = " ".join(text.split())
    words = len(re.findall(r"\w+", flat))
    sentences = [s for s in _SENT.split(flat) if len(s.split()) >= 4]
    lengths = [len(s.split()) for s in sentences]
    cv = statistics.pstdev(lengths) / statistics.mean(lengths) if len(lengths) >= 30 else None
    hits = [s for s in sentences if _MARK.search(s)]
    density = 1000 * len(_MARK.findall(flat)) / words if words else 0.0
    return {"words": words, "sentences": len(sentences), "cv": cv, "marker_density": density, "marker_sentences": hits}


def run_ai_text(paper: ParsedPaper) -> list[Finding]:
    """A weak, transparent style signal. It never says a text IS machine-written: detectors of this kind are unreliable,
    and they wrongly flag human writers, especially people writing in a second language."""
    a = analyse(paper.body_text)
    pred = predictability(paper.body_text) if a["words"] >= PRED_MIN_WORDS else None
    if pred and pred["level"] != "none":
        strong = pred["level"] == "strong"
        text = (f"Many sentences are very predictable to a small language model (median sentence perplexity {pred['median_ppl']:.0f}, "
                f"{pred['low_share']:.0%} of sentences below {LOW_PPL}; ordinary human papers: about 38 and 15%). "
                f"This is a {'strong' if strong else 'moderate'} style signal that the text may be machine-generated or heavily machine-edited. "
                "It is NOT proof: formal, edited or very standard human writing can look predictable too, and machine text that was "
                "paraphrased can look human. Do not base any judgement of the authors on this note; at most, read the quoted "
                "sentences with extra care.")
        quotes = [Evidence(quote=s["text"][:240]) for s in pred["predictable"][:3]]
        return [Finding(module="ai_text", kind="flag", text=text, evidence=quotes, status="uncertain", source="rule", confidence=0.4)]
    if a["words"] < MIN_WORDS:
        return []
    signals = []
    if a["marker_density"] >= MARKER_DENSITY:
        signals.append(f"phrases that language models overuse appear {a['marker_density']:.1f} times per 1,000 words (typical papers: under 2)")
    if a["cv"] is not None and a["cv"] < MAX_UNIFORM_CV:
        signals.append(f"sentence lengths are unusually uniform (variation {a['cv']:.2f}; typical papers: 0.5 to 0.7)")
    if len(signals) < 2:  # one signal alone is too often a false alarm
        return []
    quotes = [Evidence(quote=s[:240]) for s in a["marker_sentences"][:3]]
    text = ("Some style markers that are common in machine-generated text were found: " + "; ".join(signals) + ". "
            "This is NOT evidence that the text was written by AI. These markers also occur in human writing, and style-based "
            "detection often wrongly flags human authors, especially non-native English writers. Do not base any judgement of the "
            "authors on this note; at most, read the flagged sentences with extra care.")
    return [Finding(module="ai_text", kind="flag", text=text, evidence=quotes, status="uncertain", source="rule", confidence=0.3)]


# ---- predictability: the stronger signal, from a small local language model (see core/checks/perplexity.py) ----
LOW_PPL = 30             # a sentence this predictable to GPT-2 counts as "low perplexity"
STRONG = (22.0, 0.60)    # (median sentence perplexity below, share of low-perplexity sentences above)
MODERATE = (28.0, 0.50)  # chosen on 72 texts so that about 4% of human texts are flagged; see eval/ai_eval.py


def predictability(text: str, max_tokens: int = 3000) -> dict | None:
    """None when the model files are missing or the text is too short. Otherwise the measures, a level
    ('strong' | 'moderate' | 'none') and the most predictable sentences, for highlighting."""
    from core.checks import perplexity
    if not perplexity.available():
        return None
    r = perplexity.score_text(text, max_tokens=max_tokens)
    if r is None:
        return None
    ppls = sorted(s["ppl"] for s in r["sentences"])
    median = statistics.median(ppls)
    low = sum(p < LOW_PPL for p in ppls) / len(ppls)
    level = "strong" if median < STRONG[0] and low > STRONG[1] else "moderate" if median < MODERATE[0] and low > MODERATE[1] else "none"
    return {"level": level, "median_ppl": median, "low_share": low, "doc_ppl": r["doc_ppl"], "sentences": len(ppls),
            "model": perplexity.model_name(), "sentence_scores": r["sentences"],
            "predictable": [s for s in r["sentences"] if s["ppl"] < 20][:12]}


def full_report(text: str, max_tokens: int = 2500) -> dict:
    """Everything the AI-check page shows: the language-model result (when available) and the word-list measures."""
    a = analyse(text)
    pred = predictability(text, max_tokens) if a["words"] >= PRED_MIN_WORDS else None
    markers_flagged = a["words"] >= MIN_WORDS and a["marker_density"] >= MARKER_DENSITY and a["cv"] is not None and a["cv"] < MAX_UNIFORM_CV
    if pred:
        level = pred["level"]
    else:
        level = "style" if markers_flagged else ("none" if a["words"] >= MIN_WORDS else "short")
    if a["words"] < PRED_MIN_WORDS:
        level = "short"
    return dict(a, pred=pred, level=level, markers_flagged=markers_flagged, marker_enough=a["words"] >= MIN_WORDS,
                sentences_shown=a["marker_sentences"][:10], density_limit=MARKER_DENSITY, cv_limit=MAX_UNIFORM_CV,
                model_missing=not __import__("core.checks.perplexity", fromlist=["x"]).available(), min_words=PRED_MIN_WORDS)
