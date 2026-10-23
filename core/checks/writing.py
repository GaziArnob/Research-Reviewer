import re
from collections import Counter

from spellchecker import SpellChecker

from core.models import Evidence, Finding, ParsedPaper

US_UK = [("analyze", "analyse"), ("behavior", "behaviour"), ("color", "colour"), ("modeling", "modelling"),
         ("optimization", "optimisation"), ("generalization", "generalisation"), ("center", "centre"),
         ("labeled", "labelled"), ("normalize", "normalise"), ("organization", "organisation"),
         ("recognize", "recognise"), ("favor", "favour"), ("minimize", "minimise"), ("utilize", "utilise")]
_REPEAT = re.compile(r"\b([a-z]{2,})\s+\1\b")  # lowercase only: "BERT BERT" or "E_B E_B" come from math/notation
_CAPTION = re.compile(r"^\s*(?:Figure|Fig\.|Table)\s*\d+\s*[:.]\s*(.*)$", re.I | re.M)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _f(kind: str, text: str, evidence=None, status="needs_verification") -> Finding:
    return Finding(module="writing", kind=kind, text=text, evidence=evidence or [], status=status,
                   source="rule", confidence=0.6)


def flesch_reading_ease(text: str) -> float:
    words = re.findall(r"[A-Za-z']+", text)
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    if not words or not sentences:
        return 50.0
    syllables = sum(max(1, len(re.findall(r"[aeiouy]+", w.lower()))) for w in words)
    return 206.835 - 1.015 * len(words) / len(sentences) - 84.6 * syllables / len(words)


def run_writing_checks(paper: ParsedPaper) -> list[Finding]:
    """Rule-based writing hints (no LLM, no Java). They help a reviewer; they do not replace a proofread."""
    flat = " ".join(paper.body_text.split())
    findings: list[Finding] = []

    repeats = [m for m in _REPEAT.finditer(flat) if m.group(1).lower() not in {"had", "that"}][:5]
    if repeats:
        ex = [Evidence(quote=flat[max(0, m.start() - 25): m.end() + 25]) for m in repeats[:3]]
        findings.append(_f("minor", "Repeated words found: " + ", ".join(f'"{m.group(0)}"' for m in repeats) + ".", ex))

    sentences = [s.strip() for s in _SENT_SPLIT.split(flat) if len(s.split()) > 3]
    long_ones = [s for s in sentences if len(s.split()) > 60]
    if len(long_ones) >= 2:
        findings.append(_f("minor", f"{len(long_ones)} sentences are longer than 60 words and may be hard to follow.",
                           [Evidence(quote=long_ones[0][:300])]))

    ease = flesch_reading_ease(flat[:30000])
    if ease < 0:
        findings.append(_f("minor", f"Text is very hard to read (Flesch reading ease {ease:.0f}); consider shorter sentences.",
                           status="uncertain"))

    lower = flat.lower()
    mixed = [(us, uk) for us, uk in US_UK if re.search(rf"\b{us}", lower) and re.search(rf"\b{uk}", lower)]
    if mixed:
        findings.append(_f("minor", "Mixed US/UK spelling: " + ", ".join(f"{a}/{b}" for a, b in mixed[:6]) + "."))

    hyphenated = set(re.findall(r"[a-z]+(?:-[a-z]+)+", lower))
    pairs = sorted(f"{w} / {w.replace('-', '')}" for w in hyphenated
                   if re.search(rf"\b{re.escape(w.replace('-', ''))}\b", lower))[:6]
    if pairs:
        findings.append(_f("minor", "Inconsistent hyphenation: " + "; ".join(pairs) + "."))

    captions = [(m.group(0).strip(), m.group(1)) for m in _CAPTION.finditer(paper.full_text)]
    short = [c for c, rest in captions if len(rest.split()) < 4]
    if short:
        findings.append(_f("minor", f"{len(short)} caption(s) are very short and may not explain the figure/table.",
                           [Evidence(quote=short[0][:200])]))

    findings += _possible_typos(flat)
    return findings


_PREFIXES = ("pre", "non", "multi", "sub", "anti", "semi", "inter", "intra", "over", "under", "cross", "auto")
_UK_TO_US = [("isation", "ization"), ("ising", "izing"), ("ised", "ized"), ("ise", "ize"), ("yse", "yze"),
             ("our", "or"), ("centre", "center"), ("lled", "led"), ("lling", "ling")]


def _valid_variant(spell: SpellChecker, word: str) -> bool:
    """True for UK spellings and prefixed compounds (pretrained, multimodal) that the US dictionary lacks."""
    for uk, us in _UK_TO_US:
        if uk in word and not spell.unknown([word.replace(uk, us)]):
            return True
    return any(word.startswith(p) and not spell.unknown([word[len(p):]]) for p in _PREFIXES)


def _possible_typos(flat: str) -> list[Finding]:
    spell = SpellChecker()
    counts = Counter(re.findall(r"\b[a-z]{5,}\b", flat.lower()))
    capitalised = {w.lower() for w in re.findall(r"\b[A-Z][a-z]{4,}\b", flat)}
    # Technical terms repeat; real typos are usually rare. Names and acronyms are skipped.
    unknown = [w for w in spell.unknown(list(counts)) if counts[w] <= 2 and w not in capitalised]
    hints = []
    for w in sorted(unknown):
        if w in _PREFIXES or _valid_variant(spell, w):
            continue
        fix = spell.correction(w)  # slow (edit-distance search), so only run it until we have enough hints
        if fix and fix != w:
            hints.append(f"{w}→{fix}")
        if len(hints) == 12:
            break
    if not hints:
        return []
    return [_f("minor", f"Possible typos (many may be technical terms, verify): {', '.join(hints)}.", status="uncertain")]
