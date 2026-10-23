import re
from typing import Callable, Optional

from core.evidence.verifier import locate
from core.models import Evidence, ParsedPaper, PipelineResult

# name -> (answer, evidence) . Answers are keyword hints, never verdicts: the form labels them as such.
Check = Callable[[ParsedPaper, Optional[PipelineResult]], tuple[str, Optional[Evidence], str]]


def _find(paper: ParsedPaper, pattern: str) -> Optional[Evidence]:
    text = " ".join(paper.body_text.split())
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    snippet = text[max(0, m.start() - 60): m.end() + 100]
    hit = locate(paper, snippet)
    return hit[0] if hit else Evidence(quote=snippet[:240])


def _present(pattern: str, yes_note: str, no_note: str, partial: Optional[str] = None) -> Check:
    def run(paper, result):
        ev = _find(paper, pattern)
        if ev:
            return "yes", ev, yes_note
        return "no", None, no_note
    return run


def _scaled(strong: str, weak: str, strong_note: str, weak_note: str, none_note: str) -> Check:
    def run(paper, result):
        s, w = _find(paper, strong), _find(paper, weak)
        if s:
            return "yes", s, strong_note
        if w:
            return "partial", w, weak_note
        return "no", None, none_note
    return run


def _count(patterns: list[str], note: str) -> Check:
    def run(paper, result):
        found = [(p, _find(paper, p)) for p in patterns]
        hits = [ev for _, ev in found if ev]
        answer = "yes" if len(hits) >= 3 else "partial" if hits else "no"
        return answer, (hits[0] if hits else None), f"{len(hits)} of {len(patterns)} typical items mentioned. {note}"
    return run


def _references_exist(paper, result):
    if result is None:
        return "unclear", None, "Run the online reference check first."
    bad = [f for f in result.findings if f.module == "references" and "not found in Crossref" in f.text]
    missing = [f for f in result.findings if f.module == "references" and "could not be matched" in f.text]
    if bad:
        return "no", bad[0].evidence[0] if bad[0].evidence else None, f"{len(bad)} DOI(s) not found in Crossref."
    if missing:
        return "unclear", None, ("Some references could not be matched by title (preprints and books are often missing "
                                 "from Crossref). See the references findings.")
    return "yes", None, "No unknown DOIs; titles matched Crossref."


CHECKS: dict[str, Check] = {
    "code_available": _present(r"github\.com|gitlab\.com|code (?:is|will be|has been) (?:made )?(?:publicly |freely )?available|open[- ]source|release[sd]? (?:our |the )?code",
                               "A code link or release statement was found.", "No code link or release statement found."),
    "pseudocode": _present(r"\bAlgorithm\s+\d|pseudo-?code", "An algorithm box or pseudocode mention was found.", "No algorithm box or pseudocode found."),
    "theory": _present(r"\b(?:theorem|lemma|proposition|corollary)\b", "Theorem-like statements were found.", "No theorem-like statements found."),
    "assumptions": _present(r"\bassum(?:e|ption)s?\b", "Assumptions are mentioned (formality not checked).", "No assumptions mentioned."),
    "theorem_statements": _present(r"\b(?:Theorem|Lemma|Proposition)\s+\d", "Numbered theorem statements found.", "No numbered theorem statements found."),
    "proofs": _present(r"\bproof\b", "The word 'proof' appears (completeness not checked).", "No proofs found."),
    "datasets": _present(r"\bdatasets?\b|\bcorpus\b|\bbenchmark\b", "Datasets are mentioned.", "No dataset mentioned."),
    "experiments": _present(r"\bexperiment|\bevaluat", "Experiments or evaluation are described.", "No experiments found."),
    "hyperparameter_search": _scaled(r"grid search|random search|search over|tuned on|selected on the validation|chosen on the validation",
                                     r"hyper-?parameter|tuning|validation set", "A search or selection procedure is mentioned.",
                                     "Hyperparameters are mentioned but no search range or selection criterion was found.",
                                     "No hyperparameter information found."),
    "seeds": _present(r"random seed|\bseeds?\b", "Seeds are mentioned.", "No seed information found."),
    "compute": _scaled(r"(?:GPUs?|TPUs?|CPUs?)[^.]{0,80}(?:PyTorch|TensorFlow|CUDA|Python|JAX)\s*v?\d|(?:PyTorch|TensorFlow|CUDA|Python|JAX)\s*v?\d[^.]{0,80}(?:GPUs?|TPUs?|CPUs?)",
                       r"\bGPUs?\b|\bTPUs?\b|\bCPUs?\b|NVIDIA|hardware", "Hardware and software versions are mentioned.",
                       "Hardware is mentioned but software/OS versions were not found.", "No computing infrastructure found."),
    "metrics": _scaled(r"(?:metric|measure)s? (?:is|are) defined|we define", r"\b(?:accuracy|F1|BLEU|ROUGE|AUC|precision|recall|perplexity|metric)s?\b",
                       "Metrics are defined.", "Metrics are named; whether they are formally defined was not checked.", "No evaluation metric found."),
    "num_runs": _present(r"\b\d+\s+(?:runs|trials|seeds|repetitions|independent)|averaged over \d+|over \d+ (?:runs|seeds|trials)",
                         "A number of runs is stated.", "No number of runs found."),
    "variance": _present(r"±|\bstd\b|standard deviation|standard error|confidence interval|variance", "Variation or confidence information found.",
                         "No variance, standard deviation or confidence interval found."),
    "statistical_tests": _present(r"t-?test|wilcoxon|anova|p\s*[<=]\s*0?\.\d|bootstrap|permutation test|significance test",
                                  "A statistical test or p-value is mentioned.", "No statistical test found."),
    "final_hyperparameters": _count([r"learning rate", r"batch size", r"hidden (?:size|dimension|units)", r"dropout", r"optimizer|Adam|SGD", r"epochs?"],
                                    "Check the appendix for the full list."),
    "limitations": _present(r"\blimitations?\b|\bshortcomings?\b", "The paper mentions its limitations.",
                            "No limitations discussion found."),
    "references_exist": _references_exist,
}


def run_check(name: str, paper: ParsedPaper, result: Optional[PipelineResult]) -> tuple[str, Optional[Evidence], str]:
    return CHECKS[name](paper, result)
