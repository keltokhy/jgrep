"""Compare jselect packing with/without novelty on the exact frozen function scores.

uv run --extra code --with jev-select==0.1.0 python bench/code_selection.py
This makes no model calls. It isolates selection, not classifier quality.
"""

import json
from pathlib import Path

from jselect import Record, select

from jgrep.code_inputs import function_records

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "bench/fixtures/code_review.json").read_text())
benchmark = json.loads((root / "docs/benchmarks/code-review.json").read_text())
run = next(row for row in benchmark["runs"] if row["mode"] == "functions")
scores = {row["case"]: row["p"] for row in run["rows"]}
records = []
for case in fixture["cases"]:
    source = "package main\n\n" + case["after"]
    for function in function_records(source, case["id"] + ".go"):
        records.append(Record(function.text, id=case["id"], source=case["id"] + ".go", line=function.lineno))


def scorer(task, passages):
    return [scores[p.sources[0]["record_id"]] for p in passages]


labels = {case["id"]: case["function_relevant"] for case in fixture["cases"]}
report = {"model_calls": 0, "encoding": "o200k_base", "runs": [],
          "limitations": "Same small synthetic corpus and frozen scores; no human usefulness labels or production validation."}
for budget in (250, 500):
    for diversity in (0, 0.7):
        result = select(records, task=fixture["function_task"], tokens=budget, scorer=scorer,
                        scan="all", threshold=0.5, diversity=diversity)
        ids = [item.sources[0]["record_id"] for item in result.items]
        report["runs"].append({"token_budget": budget, "diversity": diversity, "selected_cases": ids,
                               "negative_cases_selected": [i for i in ids if not labels[i]],
                               "tokens": result.tokens, "context": result.context})
destination = root / "docs/benchmarks/code-selection.json"
destination.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({**report, "runs": [{k: v for k, v in r.items() if k != "context"} for r in report["runs"]]}, indent=2))
