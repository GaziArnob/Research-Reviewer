from core.models import ParsedPaper
from core.templates.autochecks import run_check

# (check name in core/templates/autochecks.py, label shown to the reader)
ITEMS = [
    ("code_available", "Code or data link"),
    ("datasets", "Datasets described"),
    ("split", "Train / validation / test split"),
    ("baselines", "Comparison with other methods"),
    ("ablation", "Ablation or component analysis"),
    ("metrics", "Evaluation metrics"),
    ("hyperparameter_search", "How hyperparameters were chosen"),
    ("final_hyperparameters", "Final hyperparameters listed"),
    ("seeds", "Random seeds"),
    ("num_runs", "Number of runs"),
    ("variance", "Variance or error bars"),
    ("statistical_tests", "Statistical tests"),
    ("compute", "Compute and hardware"),
    ("limitations", "Limitations discussed"),
    ("ethics", "Ethics or broader impact"),
]


def run_completeness(paper: ParsedPaper) -> dict:
    """Reporting-checklist completeness: which standard items the paper mentions at all.

    This is NOT a quality score. It only says whether the wording is there (keyword search with a quote); whether what
    is written is good, or whether an item even applies to this paper, is for the reviewer to judge. Unlike a model's
    rating it is transparent and can be checked item by item.
    """
    items = []
    for name, label in ITEMS:
        answer, ev, _ = run_check(name, paper, None)
        items.append({"id": name, "label": label, "answer": answer if answer in ("yes", "partial", "no") else "no",
                      "quote": ev.quote[:220] if ev else "", "page": ev.page if ev else None})
    yes = sum(i["answer"] == "yes" for i in items)
    partial = sum(i["answer"] == "partial" for i in items)
    total = len(items)
    return {"items": items, "yes": yes, "partial": partial, "missing": total - yes - partial, "total": total,
            "fraction": round((yes + 0.5 * partial) / total, 3)}
