"""The length and evidence-position diagnostic: 30 labeled complaint lines, five descriptions, five paddings.

    uv run python bench/context_eval.py check              # the frozen fixture still rebuilds from bench/corpus.py
    uv run python bench/context_eval.py prepare DIR        # write a fresh copy of the fixture to DIR
    uv run --extra code python bench/backend_eval.py run --api laya --dataset context    # score a model on it

Neutral text ("Routine inspection completed.") is added before or after each line, so a record is 30, 2,000
or 7,000 characters with its evidence first or last; every record stays under jgrep's 8,000-character limit.
It tests length and position, not 150 independent examples. The frozen copy, made on 2026-09-21 before any
model saw it, lives in the September 21 bundle; the runner reads it from there and checks it here first.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from corpus import DESCRIPTIONS, LINES

import os

# The September 21 bundle with context.txt and context-manifest.json is kept outside the repository.
FROZEN = Path(os.environ.get("JGREP_FROZEN_BUNDLE") or Path(__file__).resolve().parents[1] / "bench/out/frozen-2026-09-21")
GROUPS = [("original", 0, "start"), ("2000_start", 2000, "start"), ("2000_end", 2000, "end"),
          ("7000_start", 7000, "start"), ("7000_end", 7000, "end")]


def build() -> list[dict]:
    records = []
    for group, padding_chars, position in GROUPS:
        padding = ("Routine inspection completed. " * (padding_chars // 30 + 1))[:padding_chars].strip()
        for i, row in enumerate(LINES):
            parts = [row[0], padding] if position == "start" else [padding, row[0]]
            records.append({"group": group, "source_row": i + 1, "labels": list(row[1:]),
                            "text": " ".join(p for p in parts if p)})
    return records


def fixture(folder: Path = FROZEN) -> tuple[Path, list[dict], list[str]]:
    """The frozen input file, its labeled records and descriptions, after checking nothing drifted."""
    path, manifest = folder / "context.txt", json.loads((folder / "context-manifest.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["input_sha256"]:
        raise ValueError(f"{path} no longer matches its manifest")
    if manifest["descriptions"] != DESCRIPTIONS or [r["labels"] for r in manifest["records"]] != [r["labels"] for r in build()]:
        raise ValueError("bench/corpus.py no longer matches the frozen context fixture")
    if path.read_text() != "".join(r["text"] + "\n" for r in build()):
        raise ValueError("the frozen context text no longer rebuilds from bench/corpus.py")
    return path, manifest["records"], manifest["descriptions"]


def score(scores: dict[int, list[float]], records: list[dict], metrics, lines: list[int] | None = None) -> dict:
    """Correct decisions at 0.5 per condition; a record the model did not answer counts as failed, not wrong.

    `scores` is keyed by 1-based record number; `lines` limits scoring to the records that were asked.
    """
    asked = set(lines or range(1, len(records) + 1))
    groups = {}
    for group, _, _ in GROUPS:
        members = [(i, r) for i, r in enumerate(records, 1) if r["group"] == group and i in asked]
        answered = [(scores[i], r["labels"]) for i, r in members if i in scores]
        groups[group] = {
            "records": len(members), "decisions": len(members) * len(DESCRIPTIONS),
            "answered_decisions": len(answered) * len(DESCRIPTIONS),
            "failed_decisions": (len(members) - len(answered)) * len(DESCRIPTIONS),
            "correct_at_0.5": sum((p >= .5) == bool(y) for ps, ys in answered for p, y in zip(ps, ys)),
            "metrics": {d: metrics([ps[k] for ps, _ in answered], [ys[k] for _, ys in answered])
                        for k, d in enumerate(DESCRIPTIONS)} if answered else {}}
    return groups


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        path, records, descriptions = fixture()
        print(json.dumps({"fixture": str(path), "records": len(records), "descriptions": len(descriptions), "ok": True}))
    elif sys.argv[1:2] == ["prepare"] and len(sys.argv) == 3:
        out = Path(sys.argv[2])
        out.mkdir(parents=True, exist_ok=False)
        records = build()
        (out / "context.txt").write_text("".join(r["text"] + "\n" for r in records))
        (out / "context-manifest.json").write_text(json.dumps({
            "input_sha256": hashlib.sha256((out / "context.txt").read_bytes()).hexdigest(),
            "descriptions": DESCRIPTIONS, "records": records}, indent=2) + "\n")
        print(json.dumps({"records": len(records), "decisions": len(records) * len(DESCRIPTIONS)}))
    else:
        sys.exit(__doc__)
