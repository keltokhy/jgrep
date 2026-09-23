"""Run the actual scan pipeline against pre-labeled, synthetic code changes.

uv run --extra code python bench/code_review.py --api openrouter --model typesafe/jev-1.13 --output docs/benchmarks/code-review.json
uv run --extra code python bench/code_review.py --api laya --model laya-421m --jobs 4 --timeout 120 --budget 0 --output OUT.json
No answer cache. No private source code. The corpus is deliberately small and not held out.
--api is required so a local run can never fall through to a hosted provider; --budget 0 means no limit.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import datetime
import hashlib
import io
import json
import platform
import re
import tempfile
import time
from pathlib import Path

from jgrep.cli import parser, run
from jevkit_runtime import Client, resolve
from jgrep.core import PROVIDERS


class Capture(io.StringIO):
    def __init__(self):
        super().__init__()
        self.started = time.perf_counter()
        self.rows = []

    def write(self, value):
        if value.startswith("{"):
            row = json.loads(value)
            row["observed_seconds"] = time.perf_counter() - self.started
            self.rows.append(row)
        return super().write(value)


async def measure(mode, files, task, labels, backend, budget, jobs=32, timeout=30):
    flags = {"diff_hunks": ["--diff"], "diff_lines": [], "added_lines": [],
             "functions": ["--functions"], "function_lines": [], "function_line_context": ["-C", "2"]}[mode]
    args = parser().parse_args(["--json", "-p", "0", "--budget", str(budget), "--no-cache",
                                "-j", str(jobs), "--timeout", str(timeout), *flags])
    jev = Client(backend, timeout=args.timeout, concurrency=args.concurrency, store=None)
    out, err = Capture(), io.StringIO()
    code = await run(args, [task], files, jev, out, err)
    elapsed = time.perf_counter() - out.started
    if code not in (0, 1) and not out.rows:
        raise RuntimeError(err.getvalue())
    rows = [{**row, "case": Path(row["file"]).stem} for row in out.rows]
    for row in rows:
        row["file"] = Path(row["file"]).name
    hits = [row for row in rows if row["p"] >= 0.5]
    predicted = {row["case"] for row in hits}
    positive = {case for case, label in labels.items() if label}
    tp, fp, fn = len(predicted & positive), len(predicted - positive), len(positive - predicted)
    useful = [row["observed_seconds"] for row in hits if labels[row["case"]]]
    meter = {"calls": jev.meter.calls, "cached": jev.meter.cached, "retries": jev.meter.retries,
             "input_tokens": jev.meter.input_tokens, "cost": jev.meter.cost,
             "model": jev.meter.model or jev.meter.requested_model, "provenance": jev.meter.answer_provenance}
    return {"mode": mode, "task": task, "threshold": 0.5, "seconds": elapsed, "exit_code": code,
            "first_useful_seconds": min(useful) if useful else None,
            "case_precision": tp / (tp + fp) if tp + fp else 0,
            "case_recall": tp / (tp + fn) if tp + fn else 0,
            "true_positive_cases": sorted(predicted & positive),
            "false_positive_cases": sorted(predicted - positive),
            "missed_positive_cases": sorted(positive - predicted),
            "records_to_review": len(hits),
            "records_from_negative_cases": sum(not labels[row["case"]] for row in hits),
            "failed_records": failed(err.getvalue()), "meter": meter, "rows": rows, "diagnostics": err.getvalue()}


def failed(diagnostics: str) -> int:
    """Records that got no answer: jgrep names the first ten and counts the rest."""
    shown = len(re.findall(r"^jgrep: .*:\d+: ", diagnostics, re.M))
    return shown + sum(int(n.replace(",", "")) for n in re.findall(r"and ([\d,]+) more errors", diagnostics))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True, choices=list(PROVIDERS))
    ap.add_argument("--model", required=True)
    ap.add_argument("--budget", type=float, default=0.25, help="total observed spend guard across runs; 0 for none")
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--cases", help="comma-separated case ids to keep, for a quick rehearsal")
    ap.add_argument("--output", type=Path, required=True)
    options = ap.parse_args()
    fixture = Path(__file__).parent / "fixtures/code_review.json"
    corpus = json.loads(fixture.read_text())
    if options.cases:
        keep = options.cases.split(",")
        corpus["cases"] = [case for case in corpus["cases"] if case["id"] in keep]
    backend = resolve(PROVIDERS, options.api, model=options.model)
    report = {"fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "python": platform.python_version(), "platform": platform.platform(),
              "requested_model": options.model, "cache": False, "api": options.api, "url": backend.url,
              "jobs": options.jobs, "timeout": options.timeout, "cases": [case["id"] for case in corpus["cases"]],
              "limitations": ["20 handwritten examples, not a held-out production benchmark.",
                              "Labels assume SaveConfig, store.Put, AddMessage, Commit, and restoreSnapshot return errors.",
                              "Case-level precision/recall: any matching record flags the case. Individual lines are not labeled.",
                              "First useful output depends on fixture order, input-order emission, provider and concurrency.",
                              "Latency includes provider calls; no human review time was measured."], "runs": []}
    with tempfile.TemporaryDirectory(prefix="jgrep-code-review-") as folder:
        root = Path(folder)
        inputs = {name: [] for name in ("diff", "added", "functions")}
        for name in inputs:
            (root / name).mkdir()
        for case in corpus["cases"]:
            filename = case["id"] + ".go"
            before, after = case["before"], case["after"]
            patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                fromfile="a/" + filename, tofile="b/" + filename))
            added = "".join(line[1:] for line in patch.splitlines(True)
                            if line.startswith("+") and not line.startswith("+++"))
            for name, text in (("diff", patch), ("added", added), ("functions", "package main\n\n" + after)):
                path = root / name / filename
                path.write_text(text)
                inputs[name].append(str(path))
        for mode, group, task_key, label_key in (
            ("diff_lines", "diff", "diff_task", "diff_relevant"),
            ("added_lines", "added", "diff_task", "diff_relevant"),
            ("diff_hunks", "diff", "diff_task", "diff_relevant"),
            ("function_lines", "functions", "function_task", "function_relevant"),
            ("function_line_context", "functions", "function_task", "function_relevant"),
            ("functions", "functions", "function_task", "function_relevant"),
        ):
            spent = sum(r["meter"]["cost"] for r in report["runs"])
            if options.budget and spent >= options.budget:
                raise RuntimeError("benchmark budget exhausted")
            labels = {case["id"]: case[label_key] for case in corpus["cases"]}
            result = await measure(mode, inputs[group], corpus[task_key], labels, backend,
                                   options.budget - spent if options.budget else 0, options.jobs, options.timeout)
            report["runs"].append(result)
            report["total_reported_cost_usd"] = sum(r["meter"]["cost"] for r in report["runs"])
            options.output.parent.mkdir(parents=True, exist_ok=True)
            options.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: v for k, v in result.items() if k not in {"rows", "task"}}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
