"""The two local decision servers on the frozen September 21 samples, scored beside the Jev runs already recorded.

One (model, benchmark) at a time, from the repo root, with nothing else using either server:

    uv run --extra code python bench/backend_eval.py run --api diffusiongemma --dataset spam
    uv run --extra code python bench/backend_eval.py run --api diffusiongemma --dataset news
    uv run --extra code python bench/backend_eval.py run --api diffusiongemma --dataset code
    uv run --extra code python bench/backend_eval.py run --api diffusiongemma --dataset context
    uv run --extra code python bench/backend_eval.py run --api laya --dataset spam       # and news, code, context
    uv run python bench/backend_eval.py freeze      # -> docs/benchmarks/local-models-2026-09-22.json

A run drives the installed jgrep (code: bench/code_review.py) with the local provider named explicitly, --no-cache,
--budget 0, a new XDG_CACHE_HOME and an empty XDG_CONFIG_HOME, so no hosted key is even readable. It writes
<api>-<dataset>.json (report), .jsonl (every record, with its arrival time) and .log (stderr) to
bench/out/local-2026-09-22/, and records wall-clock, calls, records that got no answer, Laya's refusals from its
audit log, and every decision that differs from the same model's September 21 output. A second attempt needs
--attempt 2 (new files, new cache). Jev is never called: `freeze` reads its column from the September 21 bundle.
Rehearse with --rows 1-5 --output <scratch dir>; rehearsal reports are refused by `freeze`.

The samples (seed 20260921; 2,000 SMS with 268 spam, 2,000 AG News with 500 per class) were drawn by the
`prepare` step of this script's first version, on branch codex/try-laya-gemmadiffusion-jev; they are read, and
hash-checked, from that bundle rather than redrawn.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

from accuracy import NEWS, SPAM_DESCRIPTION, SPAM_REGEX

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench/out/local-2026-09-22"
RESULTS = ROOT / "docs/benchmarks/local-models-2026-09-22.json"
# The September 21 comparison's bundles (frozen samples, Jev's outputs, each local model's outputs) are kept
# outside the repository. Point these at them; the frozen results record only file names within them.
FROZEN = Path(os.environ.get("JGREP_FROZEN_BUNDLE") or ROOT / "bench/out/frozen-2026-09-21")  # samples, Jev, DG
LAYA_BUNDLE = Path(os.environ.get("JGREP_LAYA_BUNDLE") or ROOT / "bench/out/laya-2026-09-21")  # Laya's outputs
LAYA_AUDIT = Path(os.environ["LAYA_AUDIT"]) if os.environ.get("LAYA_AUDIT") else None  # the Laya server's --audit file
EASTERN = ZoneInfo("America/New_York")
THRESHOLDS = (0.1, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99)
DATASETS = ("spam", "news", "code", "context")
MODELS = {
    "diffusiongemma": {
        "model": "openjev-0.1", "timeout": 300, "health": "http://127.0.0.1:8080/health",
        "previous": FROZEN / "diffusiongemma",
        "server": {"name": "OpenJev", "commit": "e04794ab36e4f7e6040c2547baecdb2737ce2e79",
                   "weights": "mlx-community/diffusiongemma-26B-A4B-it-4bit",
                   "revision": "a7a81407613811e8ba63af92ac0d852b809e191f"}},
    "laya": {
        "model": "laya-421m", "timeout": 120, "health": "http://127.0.0.1:8081/health",
        "previous": LAYA_BUNDLE / "laya",
        "server": {"name": "jevkit-core scripts/laya_server.py", "runtime": "laya-mlx",
                   "commit": "fc1df62828a3fedf4d8229fdac1cbd85f1cdf337", "weights": "aac6fef/laya-mlx",
                   "revision": "047678560251f28113ee8f5df4be82102c7bf336", "context_tokens": 512,
                   "overflow": "HTTP 422, reported by jgrep as a failed record"}},
}
# The Jev 1.13 runs of September 21 (OpenRouter), reused as they are. Spam is the documented two-attempt recovery.
JEV = {"spam": FROZEN / "openrouter-spam-recovered.json", "news": FROZEN / "jev-retry/openrouter-news.json",
       "code": FROZEN / "openrouter-code-review.json", "context": FROZEN / "openrouter-context.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def eastern() -> str:
    return dt.datetime.now(EASTERN).isoformat(timespec="seconds")


def wilson(k: int, n: int):
    if not n:
        return None
    z = 1.959963984540054
    p, denominator = k / n, 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [center - delta, center + delta]


def binary_metrics(ps: list[float], ys: list[int]) -> dict:
    """The seven fixed thresholds, Brier score, 10-bin ECE and confident errors; as on September 21."""
    if not ps or len(ps) != len(ys):
        raise ValueError("probabilities and labels must be nonempty and aligned")
    n, thresholds, bins = len(ys), [], []
    for t in THRESHOLDS:
        tp = sum(p >= t and y == 1 for p, y in zip(ps, ys))
        fp = sum(p >= t and y == 0 for p, y in zip(ps, ys))
        fn = sum(p < t and y == 1 for p, y in zip(ps, ys))
        tn = n - tp - fp - fn
        thresholds.append({"threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                           "precision": tp / (tp + fp) if tp + fp else None,
                           "recall": tp / (tp + fn) if tp + fn else None,
                           "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
                           "accuracy": (tp + tn) / n, "accuracy_ci95": wilson(tp + tn, n),
                           "precision_ci95": wilson(tp, tp + fp), "recall_ci95": wilson(tp, tp + fn)})
    for b in range(10):
        members = [(p, y) for p, y in zip(ps, ys) if min(int(p * 10), 9) == b]
        if members:
            bins.append({"lower": b / 10, "upper": (b + 1) / 10, "n": len(members),
                         "mean_p": sum(p for p, _ in members) / len(members),
                         "positive_rate": sum(y for _, y in members) / len(members)})
    return {"n": n, "positives": sum(ys), "thresholds": thresholds,
            "brier": sum((p - y) ** 2 for p, y in zip(ps, ys)) / n,
            "ece_10_bins": sum(b["n"] * abs(b["mean_p"] - b["positive_rate"]) for b in bins) / n,
            "calibration_bins": bins,
            "confident_errors": sum((p >= .9 and not y) or (p <= .1 and y) for p, y in zip(ps, ys))}


def selection(spec: str | None, n: int) -> list[int]:
    """1-based source lines from "1-5,91", or all of them."""
    if not spec:
        return list(range(1, n + 1))
    lines = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        lines += range(int(a), int(b or a) + 1)
    if not lines or min(lines) < 1 or max(lines) > n or len(set(lines)) != len(lines):
        raise ValueError(f"--rows {spec!r} must name distinct lines from 1 to {n}")
    return sorted(lines)


def load(dataset: str) -> tuple[Path, list[str], list[str], list]:
    """The frozen input file, its descriptions, texts and labels, after checking every hash."""
    if dataset == "context":
        import context_eval
        path, records, descriptions = context_eval.fixture()
        return path, descriptions, [r["text"] for r in records], [r["labels"] for r in records]
    manifest = json.loads((FROZEN / "manifest.json").read_text())["datasets"][dataset]
    path, label_path = FROZEN / f"{dataset}.txt", FROZEN / f"{dataset}.labels"
    if sha(path) != manifest["sample_text_sha256"] or sha(label_path) != manifest["sample_labels_sha256"]:
        raise ValueError(f"the frozen {dataset} sample no longer matches its manifest")
    descriptions = [SPAM_DESCRIPTION] if dataset == "spam" else list(NEWS.values())
    if descriptions != manifest["descriptions"]:
        raise ValueError(f"bench/accuracy.py's {dataset} descriptions changed since September 21")
    return path, descriptions, path.read_text().splitlines(), list(map(int, label_path.read_text().split()))


def environment(api: str, model: str, cache: Path) -> dict:
    """Only the named local server is reachable: no JEV_* overrides, no hosted keys, no key files."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JEV_", "JGREP_"))
           and k not in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY")}
    return env | {"JEV_API": api, "JEV_MODEL": model, "XDG_CACHE_HOME": str(cache),
                  "XDG_CONFIG_HOME": str(cache / "no-config"), "PYTHONUNBUFFERED": "1"}


def health(api: str) -> dict:
    with urllib.request.urlopen(MODELS[api]["health"], timeout=10) as response:
        return json.loads(response.read())


def probe(env: dict) -> dict:
    """One untimed call through the same settings, to record which provider, URL and model answer."""
    from jevkit_runtime import Client, Settings, resolve
    from jgrep.core import PROVIDERS

    async def ask():
        backend = resolve(PROVIDERS, settings=Settings.from_env(env))
        async with Client(backend, timeout=120, concurrency=1) as client:
            answers = await client.ask("Hello there.", {"q": {"type": "noul", "instructions": "The text is a greeting."}})
        return {"provider": backend.name, "url": backend.url, "requested_model": backend.model,
                "resolved_model": answers.origins["q"].get("resolved_model"), "p": answers["q"]["noul"],
                "cost": client.meter.cost}
    return asyncio.run(ask())


def audit_count() -> int:
    return len(LAYA_AUDIT.read_text().splitlines()) if LAYA_AUDIT and LAYA_AUDIT.exists() else 0


def audit_window(before: int) -> dict:
    entries = [json.loads(line) for line in LAYA_AUDIT.read_text().splitlines()[before:]
               if line.strip()] if LAYA_AUDIT and LAYA_AUDIT.exists() else []
    questions = [q for e in entries if e.get("status") == "ok"
                 for q in (e.get("questions") or {}).values() if isinstance(q, dict)]
    return {"audit_file": LAYA_AUDIT.name if LAYA_AUDIT else None, "first_line": before + 1, "last_line": before + len(entries),
            "requests": len(entries), "status": dict(Counter(e.get("status") for e in entries)),
            "rejected": sum(e.get("status") == "context_rejected" for e in entries),
            "truncated_questions": sum((q.get("dropped_state_tokens") or 0) > 0 for q in questions),
            "max_state_tokens": max((q.get("state_tokens") or 0 for q in questions), default=0)}


def stats(stderr: str) -> dict:
    """jgrep's own --stats line: calls, in-flight duplicates it shared ("cached"), tokens and dollars."""
    line = next((x for x in reversed(stderr.splitlines()) if re.match(r"jgrep: [\d,]+ records", x)), "")
    number = lambda pattern: int(m[1].replace(",", "")) if (m := re.search(pattern, line)) else 0
    dollars = re.search(r"\$([\d.]+)", line)
    return {"line": line, "records": number(r"([\d,]+) records"), "calls": number(r"([\d,]+) calls"),
            "shared": number(r"([\d,]+) cached"), "retries": number(r"([\d,]+) retries"),
            "tokens": number(r"([\d,]+) tokens"), "dollars": float(dollars[1]) if dollars else 0.0}


def cli(command: list[str], env: dict, raw: Path, log: Path, total: int, label: str) -> tuple[list[dict], float, int]:
    start, rows = time.perf_counter(), []
    with raw.open("x") as out, log.open("x") as err:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=err, text=True, env=env, cwd=ROOT)
        try:
            for line in process.stdout:
                row = json.loads(line)
                row["observed_seconds"] = time.perf_counter() - start
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                out.flush()
                if len(rows) % 200 == 0:
                    print(json.dumps({"run": label, "records": len(rows), "of": total,
                                      "seconds": round(time.perf_counter() - start, 1)}), flush=True)
            code = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
    return rows, time.perf_counter() - start, code


def answered(rows: list[dict], texts: list[str], lines: list[int], width: int) -> dict[int, list[float]]:
    """Probabilities by source line. Every row must be one of ours, in order, with the right text; gaps are failures."""
    got, previous = {}, 0
    for row in rows:
        if not 1 <= row["line"] <= len(lines) or row["line"] <= previous:
            raise ValueError(f"record {row['line']} out of range or out of order")
        previous = row["line"]
        source = lines[row["line"] - 1]
        ps = row.get("ps", [row["p"]])
        if row.get("text") != texts[source - 1] or len(ps) != width:
            raise ValueError(f"record {row['line']} changed identity or probability count")
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in ps):
            raise ValueError(f"record {row['line']} has an invalid probability")
        got[source] = ps
    return got


def compare(fresh: dict, previous: dict) -> dict:
    """Decision-level determinism check against the same model's September 21 output."""
    common = sorted(fresh.keys() & previous.keys())
    changed = [k for k in common if fresh[k] != previous[k]]
    flips = {t: sum(any((a >= t) != (b >= t) for a, b in zip(fresh[k], previous[k])) for k in common) for t in (.5, .9)}
    return {"compared_records": len(common), "identical_records": len(common) - len(changed),
            "changed_records": len(changed), "flipped_at_0.5": flips[.5], "flipped_at_0.9": flips[.9],
            "max_abs_difference": max((abs(a - b) for k in changed for a, b in zip(fresh[k], previous[k])), default=0.0),
            "answered_before_not_now": sorted(previous.keys() - fresh.keys()),
            "answered_now_not_before": sorted(fresh.keys() - previous.keys()),
            "changes": [{"record": k, "before": previous[k], "after": fresh[k]} for k in changed[:500]]}


def previous_lines(api: str, dataset: str, keep: list[int]) -> dict[int, list[float]]:
    path = Path(f"{MODELS[api]['previous']}-{dataset}.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    wanted = set(keep)
    return {r["line"]: r.get("ps", [r["p"]]) for r in rows if r["line"] in wanted}


def code_rows(report: dict, cases: set | None = None) -> dict[str, list[float]]:
    return {f"{run['mode']}:{row['file']}:{row['line']}": row.get("ps", [row["p"]])
            for run in report["runs"] for row in run["rows"] if cases is None or row["case"] in cases}


def score(dataset: str, report: dict, got: dict, texts: list[str], labels: list, lines: list[int]) -> None:
    kept = [i for i in lines if i in got]
    ps, ys = [got[i] for i in kept], [labels[i - 1] for i in kept]
    if dataset == "spam":
        report["metrics"] = binary_metrics([p[0] for p in ps], ys)
        keyword = [float(bool(re.search(SPAM_REGEX, texts[i - 1], re.I))) for i in kept]
        report["keyword_baseline"] = binary_metrics(keyword, ys)
    elif dataset == "news":
        report["per_class"] = {d: binary_metrics([p[k] for p in ps], [int(y == k + 1) for y in ys])
                               for k, d in enumerate(NEWS.values())}
        top1 = [max(range(4), key=lambda k: p[k]) + 1 for p in ps]
        correct = sum(a == b for a, b in zip(top1, ys))
        report["top1_accuracy"], report["top1_ci95"] = correct / len(ys), wilson(correct, len(ys))
        report["top1_ties"] = sum(p.count(max(p)) > 1 for p in ps)
        report["confusion_true_rows_predicted_columns"] = [[sum(y == a and t == b for y, t in zip(ys, top1))
                                                            for b in range(1, 5)] for a in range(1, 5)]
        for t, k in ((0.5, 2), (0.9, 4)):
            report[f"macro_f1_at_{t}"] = sum(m["thresholds"][k]["f1"] for m in report["per_class"].values()) / 4
    else:
        import context_eval
        _, records, _ = context_eval.fixture()
        report["groups"] = context_eval.score(got, records, binary_metrics, lines)


def run(args) -> None:
    api, dataset, output = args.api, args.dataset, args.output
    model, jobs = args.model or MODELS[api]["model"], args.jobs
    timeout = args.timeout or MODELS[api]["timeout"]
    name = f"{api}-{dataset}" + (f"-attempt{args.attempt}" if args.attempt > 1 else "")
    cache = output / f"cache-{name}"
    paths = {k: output / f"{name}{k}" for k in (".json", ".jsonl", ".log", "-review.json")}
    if cache.exists() or any(p.exists() for p in paths.values()):
        raise FileExistsError(f"{name} already has outputs or a cache in {output}; keep them and pass a new --attempt")
    (cache / "no-config").mkdir(parents=True)
    env = environment(api, model, cache)
    server = health(api)
    if api == "laya" and (server.get("model") != model or server.get("allow_truncation")):
        raise RuntimeError(f"the Laya server is not the strict laya-421m adapter this benchmark expects: {server}")
    checked = probe(env)
    if checked["provider"] != api or checked["cost"]:
        raise RuntimeError(f"the probe did not reach the local {api} server at $0: {checked}")
    report = {"complete": False, "rehearsal": bool(args.rows or args.cases), "api": api, "model": model,
              "dataset": dataset, "attempt": args.attempt, "jobs": jobs, "timeout": timeout, "budget": 0,
              "cache": False, "xdg_cache_home": str(cache), "health": server, "probe": checked,
              "server": MODELS[api]["server"], "started": eastern(), "hardware": "Apple M3 Ultra, 96 GiB unified memory",
              "jgrep_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    before = audit_count() if api == "laya" else 0
    if dataset == "code":
        command = [sys.executable, str(ROOT / "bench/code_review.py"), "--api", api, "--model", model,
                   "--jobs", str(jobs), "--timeout", str(timeout), "--budget", "0", "--output", str(paths["-review.json"])]
        command += ["--cases", args.cases] if args.cases else []
        report["command"] = command
        write_json(paths[".json"], report)
        start = time.perf_counter()
        with paths[".log"].open("x") as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT).returncode
        report.update(seconds=time.perf_counter() - start, exit_code=code)
        review = json.loads(paths["-review.json"].read_text()) if paths["-review.json"].exists() else {"runs": []}
        report["runs"] = [{k: v for k, v in r.items() if k not in ("rows", "task")} for r in review["runs"]]
        report["mode_seconds_total"] = sum(r["seconds"] for r in review["runs"])
        report["calls"] = sum(r["meter"]["calls"] for r in review["runs"])
        report["failed_records"] = sum(r.get("failed_records", 0) for r in review["runs"])
        report["dollars"] = sum(r["meter"]["cost"] for r in review["runs"])
        report["review_sha256"] = sha(paths["-review.json"]) if review["runs"] else None
        cases = set(args.cases.split(",")) if args.cases else None
        before_rows = code_rows(json.loads(Path(f"{MODELS[api]['previous']}-code-review.json").read_text()), cases)
        report["vs_2026_09_21"] = compare(code_rows(review), before_rows)
        complete = code == 0 and len(review["runs"]) == 6
    else:
        path, descriptions, texts, labels = load(dataset)
        lines = selection(args.rows, len(texts))
        if len(lines) != len(texts):
            path = output / f"{name}-input.txt"
            path.write_text("".join(texts[i - 1] + "\n" for i in lines))
        command = [sys.executable, "-m", "jgrep", "--api", api, "--model", model, "--json", "-p", "0", "--no-cache",
                   "--stats", "-j", str(jobs), "--timeout", str(timeout), "--budget", "0", "--max-chars", "8000"]
        command += [part for d in descriptions for part in ("-e", d)] + [str(path)]
        report.update(command=command, input=str(path), input_sha256=sha(path), source_lines=None if path.parent == FROZEN else lines)
        write_json(paths[".json"], report)
        rows, seconds, code = cli(command, env, paths[".jsonl"], paths[".log"], len(lines), name)
        stderr = paths[".log"].read_text()
        got = answered(rows, texts, lines, len(descriptions))
        report.update(seconds=seconds, exit_code=code, stderr=stderr, stats=stats(stderr), records=len(lines),
                      answered_records=len(got), failed_records=len(lines) - len(got),
                      failed_lines=[i for i in lines if i not in got], calls=stats(stderr)["calls"],
                      dollars=stats(stderr)["dollars"], predictions_sha256=sha(paths[".jsonl"]),
                      decisions=len(lines) * len(descriptions), records_per_second=len(lines) / seconds)
        if got:
            score(dataset, report, got, texts, labels, lines)
        report["vs_2026_09_21"] = compare(got, previous_lines(api, dataset, lines))
        complete = bool(got) and (code == 0 or (api == "laya" and dataset == "context"))
    report["finished"] = eastern()
    if api == "laya":
        report["laya_audit"] = audit_window(before)
        # Our requests are our calls plus our refusals; anything else in the window was someone else's traffic.
        report["laya_audit"]["only_ours"] = report["laya_audit"]["requests"] == report["calls"] + report["failed_records"]
    leftovers = [str(p.relative_to(cache)) for p in cache.rglob("*") if p != cache / "no-config"]
    report["cache_files_after"] = leftovers
    report["complete"] = complete and not leftovers and not report["dollars"]
    write_json(paths[".json"], report)
    change = report["vs_2026_09_21"]
    print(json.dumps({"finished": str(paths[".json"]), "complete": report["complete"], "seconds": round(report["seconds"], 1),
                      "calls": report["calls"], "failed_records": report["failed_records"], "dollars": report["dollars"],
                      "laya_rejected": report.get("laya_audit", {}).get("rejected"),
                      "changed_vs_sept21": change["changed_records"], "compared": change["compared_records"]}), flush=True)
    if not report["complete"]:
        sys.exit(f"{name} is not complete; see {paths['.json']} and {paths['.log']}")


def summary(dataset: str, report: dict) -> dict:
    """The headline numbers of one report; the same shape for Jev, September 21 and fresh local runs."""
    if dataset == "spam":
        m = report["metrics"]
        at = {t["threshold"]: t for t in m["thresholds"]}
        pick = lambda t: {k: at[t][k] for k in ("precision", "recall", "f1", "tp", "fp", "fn", "tn")}
        return {"seconds": report["seconds"], "n": m["n"], "at_0.5": pick(0.5), "at_0.9": pick(0.9),
                "sweep": [pick(t) | {"threshold": t} for t in THRESHOLDS], "brier": m["brier"],
                "ece_10_bins": m["ece_10_bins"], "confident_errors": m["confident_errors"]}
    if dataset == "news":
        per = report["per_class"]
        return {"seconds": report["seconds"], "top1_accuracy": report["top1_accuracy"], "top1_ci95": report["top1_ci95"],
                "macro_f1_at_0.5": sum(c["thresholds"][2]["f1"] for c in per.values()) / 4,
                "macro_f1_at_0.9": sum(c["thresholds"][4]["f1"] for c in per.values()) / 4,
                "per_class": {d: {"f1_at_0.5": c["thresholds"][2]["f1"], "f1_at_0.9": c["thresholds"][4]["f1"],
                                  "precision_at_0.5": c["thresholds"][2]["precision"],
                                  "recall_at_0.5": c["thresholds"][2]["recall"]} for d, c in per.items()}}
    if dataset == "context":
        return {"seconds": report["seconds"],
                "groups": {g: {"correct_at_0.5": v["correct_at_0.5"], "decisions": v["decisions"],
                               "failed_decisions": v.get("failed_decisions", 0)} for g, v in report["groups"].items()}}
    return {"seconds": sum(r["seconds"] for r in report["runs"]),
            "modes": {r["mode"]: {"case_precision": r["case_precision"], "case_recall": r["case_recall"],
                                  "records_to_review": r["records_to_review"], "seconds": r["seconds"],
                                  "failed_records": r.get("failed_records", 0)} for r in report["runs"]}}


def latest(output: Path, api: str, dataset: str) -> tuple[Path, list[str]]:
    found = sorted(output.glob(f"{api}-{dataset}.json")) + sorted(
        output.glob(f"{api}-{dataset}-attempt*.json"), key=lambda p: int(p.stem.rsplit("attempt", 1)[1]))
    found = [p for p in found if not p.name.endswith("-review.json")]
    if not found:
        raise FileNotFoundError(f"no {api}-{dataset} report in {output}")
    return found[-1], [str(p) for p in found[:-1]]


def freeze(args) -> None:
    samples = json.loads((FROZEN / "manifest.json").read_text())
    result = {
        "title": "jgrep on two local decision servers, beside Jev 1.13, on the frozen September 21 samples",
        "date": "2026-09-22", "frozen_at": eastern(), "timezone": "America/New_York", "hardware": "Apple M3 Ultra, 96 GiB unified memory",
        "samples": {"bundle": "the September 21 comparison's bundle, kept outside the repository (JGREP_FROZEN_BUNDLE)",
                    "seed": samples["seed"], "manifest_sha256": sha(FROZEN / "manifest.json"),
                    "datasets": {k: {x: v[x] for x in ("n", "population_n", "sample_labels", "unique_texts",
                                                       "sample_text_sha256", "descriptions")}
                                 for k, v in samples["datasets"].items()},
                    "code": "bench/fixtures/code_review.json, 20 handwritten Go cases, six record modes",
                    "context": "bench/context_eval.py: 30 labeled lines x 5 paddings x 5 descriptions"},
        "models": {"jev": {"api": "openrouter", "requested_model": "typesafe/jev-1.13",
                           "resolved_model": "typesafe/jev-1.13-20260917", "recorded": "2026-09-21",
                           "reused": "not re-run; read from the September 21 bundle",
                           "jobs": {"spam": "16 then 8", "news": 8, "code": 8, "context": 4},
                           "timeout": {"spam": "30 then 120", "news": 120, "code": 120, "context": 120},
                           "notes": ["SMS needed two attempts (a deadline, then an HTTP 520, one record each); "
                                     "scored from the recovered file; its 198.4 s includes both attempts."],
                           "provenance": {d: {"file": str(JEV[d].relative_to(FROZEN)), "sha256": sha(JEV[d]), "recorded": "2026-09-21",
                                              "calls_made_today": 0} for d in DATASETS}}},
        "benchmarks": {d: {"jev": summary(d, json.loads(JEV[d].read_text()))} for d in DATASETS},
        "runs": {}, "vs_2026_09_21": {}, "sept21_local": {},
    }
    for api in MODELS:
        result["models"][api] = {"requested_model": MODELS[api]["model"], "server": MODELS[api]["server"]}
        result["runs"][api], result["vs_2026_09_21"][api], result["sept21_local"][api] = {}, {}, {}
        for dataset in DATASETS:
            path, superseded = latest(args.output, api, dataset)
            report = json.loads(path.read_text())
            if report["rehearsal"] or not report["complete"]:
                raise ValueError(f"{path} is a rehearsal or incomplete")
            result["benchmarks"][dataset][api] = summary(dataset, report)
            stem = path.with_suffix("")
            raw = {k: str(Path(f"{stem}{k}").relative_to(ROOT)) for k in (".jsonl", ".log", "-review.json")
                   if Path(f"{stem}{k}").exists()}
            console = path.parent / f"console-{stem.name}.log"
            raw["console"] = str(console.relative_to(ROOT)) if console.exists() else None
            per_record = report["decisions"] / report["records"] if report.get("records") else 1
            result["runs"][api][dataset] = {
                "report": str(path.relative_to(ROOT)), "raw_outputs": raw, "superseded_attempts": superseded,
                "failed_decisions": int(report["failed_records"] * per_record),
                "laya_rejected": report.get("laya_audit", {}).get("rejected") if api == "laya" else None,
                "exit_code": report.get("exit_code"), "stats_line": report.get("stats", {}).get("line"),
                "started": report["started"],
                "finished": report["finished"], "seconds": report["seconds"], "jobs": report["jobs"],
                "timeout": report["timeout"], "calls": report["calls"], "failed_records": report["failed_records"],
                "dollars": report["dollars"],
                "laya_audit": report.get("laya_audit") and {**report["laya_audit"],
                                                            "audit_file": Path(report["laya_audit"]["audit_file"]).name
                                                            if report["laya_audit"].get("audit_file") else None},
                "probe": report["probe"],
                "mode_seconds_total": report.get("mode_seconds_total")}
            result["vs_2026_09_21"][api][dataset] = {k: v for k, v in report["vs_2026_09_21"].items() if k != "changes"}
            previous = Path(f"{MODELS[api]['previous']}-{dataset}" + ("-review" if dataset == "code" else "") + ".json")
            result["sept21_local"][api][dataset] = summary(dataset, json.loads(previous.read_text()))
    full = {k: json.loads((ROOT / f"bench/out/accuracy-{k}.json").read_text()) for k in ("spam", "news")}
    result["jev_full_datasets"] = {
        "note": "Jev 1.13 via OpenRouter on 2026-09-18 over the whole datasets (bench/accuracy.py); not like for like "
                "with the 2,000-record samples above, and no local model was run on them.",
        "spam": {"lines": full["spam"]["lines"], "at_0.5": full["spam"]["jgrep_at_0.5"], "at_0.9": full["spam"]["jgrep_at_0.9"]},
        "news": {"lines": full["news"]["lines"], "top1_accuracy": full["news"]["top1_accuracy"], "macro_f1": full["news"]["macro_f1"]}}
    result["notes"] = [
        "Local runs: real jgrep CLI, answer cache off, empty XDG_CACHE_HOME, one model and one benchmark at a time.",
        "Seconds are one warm wall-clock run including CLI start-up; code is the sum of the six modes' scan times.",
        "A Laya refusal (HTTP 422, state over its 512-token window) is a failed decision, not a negative answer.",
        "On September 21 the Laya server allowed audited truncation; today's server refuses instead, so context "
        "records that were cropped then are failures now.",
        "Local API fees are $0; hardware and electricity are not priced.",
        "Concurrency 4 requests in flight for both servers; deadline 300 s for DiffusionGemma (120 s on September 21) "
        "and 120 s for Laya. No deadline errors, so nothing was lowered. During each run the other server was loaded "
        "but idle.",
        "Wall-clock is not like for like against Jev: hosted Jev ran at jobs 8-16 over the network.",
        "Every local decision today is bit-identical to the same model's September 21 output (see vs_2026_09_21); "
        "the only difference is Laya context lines 91-150, refused today (HTTP 422) and truncated then.",
        "Laya spam and news took 2.0x and 2.2x their September 21 time. Its audit log shows the median per-request "
        "server time unchanged (spam 10.5 ms vs 10.2 ms, news 28.3 ms vs 28.9 ms); the excess is a tail of stalled "
        "requests (spam 83 over 0.1 s, up to 1.3 s; news 138, up to 2.6 s; September 21: 2 and 30). Cause not "
        "verified; both servers run at nice 5 on a desktop in use. See bench/out/local-2026-09-22/RESULTS-NOTES.md."]
    args.results.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.results, result)
    print(json.dumps({"frozen": str(args.results)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("operation", choices=["run", "freeze"])
    ap.add_argument("--api", choices=list(MODELS), help="a local server; hosted providers are not offered here")
    ap.add_argument("--model", help="default: openjev-0.1 for diffusiongemma, laya-421m for laya")
    ap.add_argument("--dataset", choices=DATASETS)
    ap.add_argument("--jobs", type=int, default=4, help="requests in flight (default 4)")
    ap.add_argument("--timeout", type=float, help="per-record deadline in seconds (default 300 DiffusionGemma, 120 Laya)")
    ap.add_argument("--attempt", type=int, default=1, help="2, 3, ... for a repeat: new files and a new cache")
    ap.add_argument("--output", type=Path, default=OUT, help=f"default {OUT.relative_to(ROOT)}")
    ap.add_argument("--results", type=Path, default=RESULTS, help="where freeze writes")
    ap.add_argument("--rows", help="rehearsal only: source lines such as 1-5,91")
    ap.add_argument("--cases", help="rehearsal only, code: case ids such as c01,c09")
    a = ap.parse_args()
    if a.operation == "run" and not (a.api and a.dataset):
        ap.error("run needs --api and --dataset")
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    run(a) if a.operation == "run" else freeze(a)
