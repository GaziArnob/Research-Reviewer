import re

from core.models import Finding, ParsedPaper

# (label, regex that means "present"). Absence becomes a finding that a human should verify.
CHECKS = [
    ("code availability", r"github\.com|gitlab\.com|code (?:is|will be) (?:publicly )?available|source code|open[- ]source"),
    ("dataset description", r"\bdatasets?\b|\bcorpus\b|\bbenchmark\b"),
    ("train/validation/test split", r"train(?:ing)?[/ ,-]+(?:val(?:idation)?|dev)|test split|held[- ]out|cross[- ]validation"),
    ("random seed or number of runs", r"random seed|\bseeds?\b|\d+ (?:runs|trials|repetitions)|averaged over"),
    ("hyperparameters", r"hyper-?parameters?|learning rate|batch size|\bepochs?\b"),
    ("compute/hardware", r"\bGPUs?\b|\bTPUs?\b|\bCPUs?\b|hardware|compute"),
    ("baselines", r"\bbaselines?\b|compared (?:with|to|against)|state[- ]of[- ]the[- ]art"),
    ("ablation study", r"\bablations?\b"),
    ("limitations discussion", r"\blimitations?\b"),
    ("ethics/broader impact", r"\bethic|broader impact|societal impact"),
]


def run_reproducibility(paper: ParsedPaper) -> list[Finding]:
    text = paper.body_text
    findings = []
    for label, pattern in CHECKS:
        if not re.search(pattern, text, re.I):
            findings.append(Finding(
                module="reproducibility", kind="minor", status="needs_verification",
                text=f"No mention of {label} was found by keyword search. Verify manually.",
            ))
    return findings
