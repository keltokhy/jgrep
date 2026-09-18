"""Which wording of the yes/no question does Jev read best, and does packing change answers?

    uv run python bench/phrasing.py

Part 1 scores four ways of turning a user's description into a noul question, on bench/corpus.py.
Part 2 asks the five descriptions alone and packed into one call, and compares the probabilities.
Part 3 times calls carrying 1 to 64 questions. All live, a few cents in total, nothing cached.
"""

import asyncio
import statistics as st
import time

from corpus import DESCRIPTIONS, LINES

from jgrep.core import Jev, resolve_backend

TEMPLATES = {
    "raw": lambda d: {"type": "noul", "instructions": d},
    "fits": lambda d: {"type": "noul", "instructions": f'The text fits this description: "{d}"'},
    "structured": lambda d: {"type": "noul", "instructions": {
        "task": "Decide whether the text fits the description.", "description": d}},
    "criteria": lambda d: {"type": "noul", "instructions": f'The text fits this description: "{d}"',
                           "criteria": {"true": "The text clearly fits the description.",
                                        "false": "The text does not fit the description."}},
}


def auc(scores: list[float], labels: list[int]) -> float:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


async def ask_all(jev: Jev, build, packed: bool) -> list[list[float]]:
    """Probabilities as [line][description]."""
    sem = asyncio.Semaphore(16)

    async def one(text: str) -> list[float]:
        async with sem:
            if packed:
                a = await jev.ask(text, {f"d{i}": build(d) for i, d in enumerate(DESCRIPTIONS)})
                return [a[f"d{i}"]["noul"] for i in range(len(DESCRIPTIONS))]
            singles = await asyncio.gather(*(jev.ask(text, {"d": build(d)}) for d in DESCRIPTIONS))
            return [a["d"]["noul"] for a in singles]

    return await asyncio.gather(*(one(text) for text, *_ in LINES))


async def main() -> None:
    backend, key = resolve_backend()
    jev = Jev(key, backend, concurrency=64)
    labels = [[row[1 + j] for row in LINES] for j in range(len(DESCRIPTIONS))]

    print("PART 1  accuracy at 0.5 and AUC, by wording and description\n")
    print(f"{'wording':<12}" + "".join(f"{d[:24]:>27}" for d in DESCRIPTIONS) + f"{'ALL':>14}")
    results = {}
    for name, build in TEMPLATES.items():
        probs = results[name] = await ask_all(jev, build, packed=True)
        cells, right = [], 0
        for j in range(len(DESCRIPTIONS)):
            col = [probs[i][j] for i in range(len(LINES))]
            acc = sum((p >= 0.5) == bool(y) for p, y in zip(col, labels[j]))
            right += acc
            cells.append(f"{acc:>2}/30  auc {auc(col, labels[j]):.2f}".rjust(27))
        print(f"{name:<12}" + "".join(cells) + f"{right:>10}/150")

    print("\nmisses for the 'fits' wording:")
    for i, (text, *ys) in enumerate(LINES):
        for j, y in enumerate(ys):
            p = results["fits"][i][j]
            if (p >= 0.5) != bool(y):
                print(f"  p={p:.2f} want {y}  [{DESCRIPTIONS[j]}]  {text}")

    print("\nPART 2  same questions asked alone and packed five to a call ('fits' wording)")
    alone = await ask_all(jev, TEMPLATES["fits"], packed=False)
    diffs = [abs(a - b) for ra, rb in zip(alone, results["fits"]) for a, b in zip(ra, rb)]
    flips = sum((a >= 0.5) != (b >= 0.5) for ra, rb in zip(alone, results["fits"]) for a, b in zip(ra, rb))
    print(f"  mean |difference| {st.mean(diffs):.4f}   max {max(diffs):.3f}   decisions flipped {flips}/150")

    print("\nPART 3  latency by questions per call (8 calls each, sequential)")
    text = LINES[0][0]
    for k in (1, 2, 4, 8, 16, 32, 64):
        times, tokens = [], 0
        for rep in range(8):
            qs = {f"q{i}": TEMPLATES["fits"](f"{DESCRIPTIONS[i % 5]} (variant {rep}-{i})") for i in range(k)}
            before = jev.meter.input_tokens
            t0 = time.perf_counter()
            await jev.ask(text, qs)
            times.append(time.perf_counter() - t0)
            tokens = jev.meter.input_tokens - before
        print(f"  {k:>2} questions: median {st.median(times) * 1000:5.0f} ms   max {max(times) * 1000:5.0f} ms   "
              f"{tokens:>5} tokens billed   {tokens / k:6.1f} per question")

    print(f"\n{jev.meter.summary()}")
    await jev.close()


if __name__ == "__main__":
    asyncio.run(main())
