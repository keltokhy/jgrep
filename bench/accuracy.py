"""How accurate, fast and cheap is jgrep on public labeled text, next to the obvious alternatives?

    uv run python bench/accuracy.py prepare          # download the two datasets into bench/out/
    uv run python bench/accuracy.py spam             # jgrep vs a keyword grep on SMS spam
    uv run python bench/accuracy.py news             # four descriptions in one call on AG News
    uv run python bench/accuracy.py llm [--n 300]    # jgrep vs chat LLMs asked the same yes/no question

Everything runs the installed `jgrep` command itself, uncached, so the numbers are what a user gets.
Live; the whole file costs well under a dollar. Results land in bench/out/accuracy-*.json.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import httpx

from jevkit_runtime import resolve
from jgrep.core import PROVIDERS

OUT = Path(__file__).parent / "out"
SPAM_URL = "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip"  # UCI, CC BY 4.0
NEWS_URL = "https://raw.githubusercontent.com/mhjabreel/CharCnn_Keras/master/data/ag_news_csv/test.csv"
SPAM_DESCRIPTION = "an unsolicited spam, scam or marketing text message"
# A keyword filter of the kind people actually write. Fixed before looking at any results.
SPAM_REGEX = r"free|win|won|prize|claim|urgent|cash|txt|text .* to|call now|reply|offer|guaranteed|£|\$|www\.|http"
NEWS = {1: "news about world affairs, politics or conflict", 2: "news about sports",
        3: "news about business, markets or the economy", 4: "news about science or technology"}


def one_line(text: str) -> str:
    return " ".join(text.split())


def prepare() -> None:
    OUT.mkdir(exist_ok=True)
    raw = urllib.request.urlopen(SPAM_URL, timeout=60).read()
    rows = zipfile.ZipFile(io.BytesIO(raw)).read("SMSSpamCollection").decode("utf-8").splitlines()
    pairs = [r.split("\t", 1) for r in rows if "\t" in r]
    (OUT / "spam.txt").write_text("".join(one_line(t) + "\n" for _, t in pairs))
    (OUT / "spam.labels").write_text("".join(("1" if y == "spam" else "0") + "\n" for y, _ in pairs))
    news = list(csv.reader(io.StringIO(urllib.request.urlopen(NEWS_URL, timeout=60).read().decode("utf-8"))))
    (OUT / "news.txt").write_text("".join(one_line(f"{title}. {body}").replace("\\", " ") + "\n" for _, title, body in news))
    (OUT / "news.labels").write_text("".join(y + "\n" for y, _, _ in news))
    print(f"spam: {len(pairs):,} messages, {sum(y == 'spam' for y, _ in pairs):,} spam;  news: {len(news):,} articles")


def load(name: str) -> tuple[list[str], list[int]]:
    lines = (OUT / f"{name}.txt").read_text().splitlines()
    labels = [int(x) for x in (OUT / f"{name}.labels").read_text().split()]
    assert len(lines) == len(labels), "a text spans lines; rerun prepare"
    return lines, labels


def jgrep(descriptions: list[str], path: Path, *, jobs: int = 64) -> tuple[list[dict], dict]:
    """Every line's probabilities from the real command, uncached, plus its own stats line."""
    cmd = ["jgrep", "--json", "-p", "0", "-j", str(jobs), "--no-cache", "--stats", "--budget", "2"]
    for d in descriptions:
        cmd += ["-e", d]
    t0 = time.perf_counter()
    run = subprocess.run(cmd + [str(path)], capture_output=True, text=True)
    seconds = time.perf_counter() - t0
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    stats = re.search(r"([\d,]+) calls.*?([\d,]+) tokens; \$([\d.]+)", run.stderr)
    if run.returncode == 2 or not stats:
        sys.exit(f"jgrep failed: {run.stderr[-400:]}")
    return rows, {"seconds": round(seconds, 1), "calls": int(stats[1].replace(",", "")),
                  "tokens": int(stats[2].replace(",", "")), "dollars": float(stats[3])}


def prf(predicted: list[bool], truth: list[bool]) -> dict:
    tp = sum(p and t for p, t in zip(predicted, truth))
    fp = sum(p and not t for p, t in zip(predicted, truth))
    fn = sum(t and not p for p, t in zip(predicted, truth))
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 4),
            "accuracy": round(sum(p == t for p, t in zip(predicted, truth)) / len(truth), 4)}


def spam() -> None:
    lines, labels = load("spam")
    truth = [y == 1 for y in labels]
    rows, stats = jgrep([SPAM_DESCRIPTION], OUT / "spam.txt")
    p = {r["line"]: r["p"] for r in rows}
    ps = [p.get(i + 1, 0.0) for i in range(len(lines))]
    t0 = time.perf_counter()
    keyword = [bool(re.search(SPAM_REGEX, line, re.I)) for line in lines]
    result = {
        "dataset": "UCI SMS Spam Collection", "lines": len(lines), "positives": sum(truth),
        "description": SPAM_DESCRIPTION, "jgrep_at_0.5": prf([x >= 0.5 for x in ps], truth),
        "jgrep_at_0.9": prf([x >= 0.9 for x in ps], truth), "jgrep_run": stats,
        "keyword_grep": prf(keyword, truth) | {"regex": SPAM_REGEX, "seconds": round(time.perf_counter() - t0, 3)},
    }
    (OUT / "accuracy-spam.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


def news() -> None:
    lines, labels = load("news")
    rows, stats = jgrep(list(NEWS.values()), OUT / "news.txt")
    by_line = {r["line"]: r.get("ps", [r["p"]]) for r in rows}
    ps = [by_line.get(i + 1, [0.0] * 4) for i in range(len(lines))]
    per_class = {NEWS[c]: prf([x[c - 1] >= 0.5 for x in ps], [y == c for y in labels]) for c in NEWS}
    top1 = [max(range(4), key=lambda j: x[j]) + 1 for x in ps]
    result = {
        "dataset": "AG News test set", "lines": len(lines), "descriptions": list(NEWS.values()),
        "note": "four descriptions packed into one call per line; each scored one-vs-rest at 0.5",
        "per_description": per_class, "macro_f1": round(sum(v["f1"] for v in per_class.values()) / 4, 4),
        "top1_accuracy": round(sum(a == b for a, b in zip(top1, labels)) / len(labels), 4), "jgrep_run": stats,
    }
    (OUT / "accuracy-news.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


async def chat(model: str, lines: list[str], key: str, jobs: int) -> tuple[list[bool | None], dict]:
    """The do-it-yourself alternative: ask a chat model the same question, one line per request."""
    sem, latencies, usage = asyncio.Semaphore(jobs), [], {"cost": 0.0, "prompt": 0, "completion": 0}
    prompt = f'Does the text fit this description: "{SPAM_DESCRIPTION}"? Answer with one word, yes or no.'

    async def one(client: httpx.AsyncClient, text: str) -> bool | None:
        # Hybrid models that think by default (Qwen) spend the whole allowance thinking and never answer,
        # so their thinking is switched off; the GPT models take the lowest effort they offer.
        reasoning = {"enabled": False} if model.startswith("qwen/") else {"effort": "low", "exclude": True}
        body = {"model": model, "temperature": 0, "max_tokens": 200, "usage": {"include": True},
                "reasoning": reasoning,
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]}
        async with sem:
            for attempt in range(4):
                t0 = time.perf_counter()
                r = await client.post("https://openrouter.ai/api/v1/chat/completions", json=body)
                if r.status_code == 200 and "choices" in r.json():
                    break
                await asyncio.sleep(0.5 * 2 ** attempt)
            else:
                return None
        latencies.append(time.perf_counter() - t0)
        data = r.json()
        u = data.get("usage") or {}
        usage["cost"] += u.get("cost") or 0.0
        usage["prompt"] += u.get("prompt_tokens") or 0
        usage["completion"] += u.get("completion_tokens") or 0
        answer = (data["choices"][0]["message"].get("content") or "").strip().lower()
        return True if answer.startswith("yes") else False if answer.startswith("no") else None

    t0 = time.perf_counter()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=90) as client:
        answers = await asyncio.gather(*(one(client, t) for t in lines))
    latencies.sort()
    return answers, {"seconds": round(time.perf_counter() - t0, 1), "dollars": round(usage["cost"], 5),
                     "median_latency_ms": round(latencies[len(latencies) // 2] * 1000) if latencies else None,
                     "prompt_tokens": usage["prompt"], "completion_tokens": usage["completion"],
                     "unparseable": sum(a is None for a in answers)}


def llm(n: int, models: list[str], jobs: int) -> None:
    lines, labels = load("spam")
    step = len(lines) // n
    idx = list(range(0, step * n, step))  # an even spread through the file, fixed
    sub_lines, truth = [lines[i] for i in idx], [labels[i] == 1 for i in idx]
    sub = OUT / "spam-subset.txt"
    sub.write_text("".join(t + "\n" for t in sub_lines))
    rows, stats = jgrep([SPAM_DESCRIPTION], sub, jobs=jobs)
    p = {r["line"]: r["p"] for r in rows}
    result = {"dataset": f"UCI SMS Spam Collection, every {step}th message", "lines": n, "positives": sum(truth),
              "concurrency": jobs, "jgrep": prf([p.get(i + 1, 0.0) >= 0.5 for i in range(n)], truth) | stats, "chat_models": {}}
    key = resolve(PROVIDERS, "openrouter").key
    for model in models:
        answers, run = asyncio.run(chat(model, sub_lines, key, jobs))
        result["chat_models"][model] = prf([a is True for a in answers], truth) | run
        print(model, result["chat_models"][model], flush=True)
    (OUT / "accuracy-llm.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["prepare", "spam", "news", "llm"])
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument("--models", nargs="+", default=["~openai/gpt-luna-latest", "qwen/qwen3.7-flash", "~openai/gpt-terra-latest"])
    a = ap.parse_args()
    {"prepare": prepare, "spam": spam, "news": news, "llm": lambda: llm(a.n, a.models, a.jobs)}[a.what]()
