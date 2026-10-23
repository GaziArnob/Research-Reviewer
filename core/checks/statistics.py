import re

from core.models import Evidence, Finding, ParsedPaper


def run_statistics(paper: ParsedPaper) -> list[Finding]:
    """Keyword-level statistics hygiene checks. These are hints, not verdicts."""
    text = paper.body_text
    findings = []

    sig = re.search(r"[^.\n]*(?:statistically )?significant(?:ly)?[^.\n]*", text, re.I)
    has_test = re.search(r"t-?test|anova|wilcoxon|mann[- ]whitney|chi-?square|p\s*[<=>]\s*0?\.\d+|bootstrap|permutation test", text, re.I)
    if sig and not has_test:
        findings.append(Finding(
            module="statistics", kind="major", status="needs_verification",
            text="The paper uses 'significant' but no statistical test or p-value was detected.",
            evidence=[Evidence(quote=sig.group(0).strip()[:200])],
        ))

    has_spread = re.search(r"±|std\b|standard deviation|standard error|confidence interval|\bCI\b|variance", text, re.I)
    has_results = re.search(r"\b(?:accuracy|f1|auc|bleu|rouge|error|precision|recall)\b", text, re.I)
    if has_results and not has_spread:
        findings.append(Finding(
            module="statistics", kind="minor", status="needs_verification",
            text="Results are reported without any variance, standard deviation or confidence interval.",
        ))

    if re.search(r"\bp\s*[<=]\s*0?\.0[1-5]\b", text) and not re.search(r"bonferroni|multiple (?:comparison|testing)|false discovery|holm", text, re.I):
        n = len(re.findall(r"\bp\s*[<=]\s*0?\.\d+", text))
        if n >= 5:
            findings.append(Finding(
                module="statistics", kind="minor", status="needs_verification",
                text=f"{n} p-values reported with no mention of multiple-comparison correction.",
            ))
    return findings
