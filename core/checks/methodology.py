import re

from core.evidence.verifier import find_pattern, locate
from core.models import Evidence, Finding, ParsedPaper

# (id, severity, regex that signals the problem, text). All are keyword hints for a human reviewer, never verdicts.
SIGNALS = [
    ("test_tuning", "major",
     r"(?:tuned|selected|chosen|picked|optimi[sz]ed|early[- ]stopp\w+)\b[^.]{0,60}\b(?:on|using|with|against)\s+the\s+test\s+(?:set|data|split|accuracy|performance)"
     r"|report(?:ed|ing)?\s+the\s+best\s+(?:test|result)s?",
     "The text suggests that hyper-parameters, checkpoints or results may have been chosen on the test set. Check whether a separate validation set was used."),
    ("copied_baselines", "minor",
     r"(?:results|numbers|scores|performance)\s+(?:of|for|from)\s+(?:the\s+)?(?:baselines?|prior|previous|other)\s+(?:methods?|models?|work)?\s*(?:are|were)?\s*(?:taken|copied|reported|borrowed)\s+from"
     r"|baselines?\s+(?:results|numbers|scores|performance)\s+(?:are|were)\s+(?:taken|copied|borrowed)\s+from"
     r"|(?:numbers|results|scores)\s+(?:are|were)\s+(?:taken|copied|borrowed)\s+from\s+(?:the\s+)?(?:original|previous|prior|respective|their)",
     "Baseline numbers appear to be copied from other papers. Their data splits, preprocessing and tuning effort may differ from this paper's."),
    ("cherry_picking", "minor",
     r"(?:selected|chosen|representative|hand-?picked|cherry-?picked|randomly\s+selected)\s+(?:examples?|samples?|cases|instances)",
     "Qualitative examples were selected by the authors. Check that they are representative and that failure cases are shown too."),
    ("no_validation", "minor",
     r"\bno\s+(?:separate\s+|held-?out\s+)?validation\s+(?:set|data|split)|without\s+(?:a\s+)?(?:separate\s+)?validation\s+(?:set|data|split)",
     "The paper says no separate validation set was used. Then model selection must have used the training or test data."),
    ("eval_on_train", "minor",
     r"(?:evaluat|test)\w*\b[^.]{0,40}\bon\b[^.]{0,25}\b(?:training|train)\s+(?:set|data|split)\b",
     "Evaluation on training data is mentioned. Check that reported results come from held-out data."),
]
HUMAN = re.compile(r"\b(?:participants?|annotators?|crowd-?workers?|human\s+evaluation|user\s+study|volunteers?|respondents?)\b", re.I)
ETHICS = re.compile(r"\b(?:IRB|ethic\w*|informed\s+consent|consent|institutional\s+review|approved\s+by)\b", re.I)
PRETRAINED = re.compile(r"\b(?:GPT-?\d?|LLaMA|BERT|pre-?train(?:ed|ing)|foundation\s+model|large\s+language\s+model)\b", re.I)
CONTAMINATION = re.compile(r"contaminat\w*|data\s+leak\w*|leakage|overlap\s+between\s+(?:the\s+)?train", re.I)


def run_methodology(paper: ParsedPaper) -> list[Finding]:
    findings: list[Finding] = []
    for _id, severity, pattern, text in SIGNALS:
        ev = find_pattern(paper, pattern)
        if ev:
            findings.append(Finding(module="methodology", kind=severity, status="needs_verification", source="rule",
                                    confidence=0.4, text=text, evidence=[ev]))
    body = paper.body_text
    if (ev := find_pattern(paper, HUMAN.pattern)) and not ETHICS.search(body):
        findings.append(Finding(module="methodology", kind="minor", status="needs_verification", source="rule", confidence=0.4,
                                text="Human participants or annotators are mentioned, but no ethics approval or consent is. Check the ethics statement.",
                                evidence=[ev]))
    if (ev := find_pattern(paper, PRETRAINED.pattern)) and not CONTAMINATION.search(body):
        findings.append(Finding(module="methodology", kind="minor", status="uncertain", source="rule", confidence=0.3,
                                text="Pretrained models are used, but train/test contamination or leakage is never discussed.", evidence=[ev]))
    return findings


_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{3,})(\s?%)?(?![\w.])")


def _numbers(text: str) -> set[str]:
    return {m.group(1).replace(",", "") for m in _NUM.finditer(text)}


def run_consistency(paper: ParsedPaper) -> list[Finding]:
    """Numbers quoted in the abstract that never occur again in the paper. Rounding and tables rendered as images
    cause false alarms, so this is only a hint to look at the results section."""
    abstract = paper.section("abstract")
    if not abstract:
        return []
    rest = " ".join(s.text for s in paper.sections if s.name.lower() not in ("abstract", "front matter", "references"))
    elsewhere = _numbers(rest)
    flat = " ".join(abstract.text.split())
    findings, seen = [], set()
    for m in _NUM.finditer(flat):
        num = m.group(1).replace(",", "")
        if num in elsewhere or num in seen or re.fullmatch(r"(19|20)\d\d", num):
            continue
        seen.add(num)
        hit = locate(paper, flat[max(0, m.start() - 50): m.end() + 40])
        ev = hit[0] if hit else Evidence(quote=flat[max(0, m.start() - 50): m.end() + 40], section="Abstract")
        findings.append(Finding(module="consistency", kind="minor", status="needs_verification", source="rule", confidence=0.4,
                                text=f"The number {m.group(1)} in the abstract does not appear anywhere else in the paper's text. "
                                     "Check it against the results (it may be rounded, derived, or inside a table image).", evidence=[ev]))
    return findings[:5]
