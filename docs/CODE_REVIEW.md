# Code review experiment

Measured on 2026-09-19 using OpenRouter `typesafe/jev-1.13`, served as
`typesafe/jev-1.13-20260917`, with 32 concurrent requests and a 0.5 decision threshold.
There was no disk answer cache. Identical in-flight requests can still share a call; the
client reports those as `cached` in the frozen output.

This is a **20-case handwritten diagnostic**, not a held-out or production benchmark. Cases
and labels were written before the first model run. Names such as `SaveConfig`, `AddMessage`,
`Commit` and `restoreSnapshot` are assumed to return errors. The excerpts are syntax-valid Go
functions, not complete compilable applications with real implementations of those helpers.

The external [issue #3](https://github.com/keltokhy/jgrep/issues/3) supplied neither its repository
URL/commit nor raw outputs. Its reported 22,054-line run could not be reproduced. These fixtures
test related behaviors: removed error checks, rollback, comments, existing ignored errors,
temporary-file cleanup, deleted functions, and changes that preserve or improve error handling.

The [response to issue #3](ISSUE_3_RESPONSE.md) maps the suggestions to shipped features and
records the remaining proposals and evidence needed to evaluate them.

## Complete changes

Task: identify a change that starts silently ignoring a persistent-write or rollback failure
that was previously handled. Eight positive cases; twelve negative cases. A case is flagged
if any of its records scores at least 0.5. Individual lines do not have separate gold labels.

| Input unit | Case precision | Case recall | Records to review | Records from negative cases | Wall time | Reported cost |
|---|---:|---:|---:|---:|---:|---:|
| Lines of the unified diff | 57.1% | 100% | 22 | 10 | 1.69 s | $0.001920 |
| Added lines alone | 66.7% | 75% | 11 | 3 | 0.97 s | $0.000313 |
| Complete hunks (`--diff`) | 80% | 100% | 10 | 2 | 0.87 s | $0.000366 |

Whole hunks use a change-specific question, so this comparison combines unit size and question
framing; it does not isolate those effects. Added-line-only filtering missed c01 and c07.
The hunk reader found all eight positives, but falsely flagged best-effort temporary cleanup
(c12) and an ignored rendering error unrelated to persistence or rollback (c19).

## Functions versus neighboring lines

Task: find functions that silently ignore persistent-write or rollback errors. This asks about
the current code, including existing problems, so it has ten positive cases. The same question
and threshold are used for all three input modes.

| Input unit | Case precision | Case recall | Records to review | Records from negative cases | Wall time | Reported cost |
|---|---:|---:|---:|---:|---:|---:|
| Individual lines | 75% | 90% | 17 | 3 | 1.27 s | $0.000630 |
| Lines with `-C 2` | 90.9% | 100% | 45 | 4 | 1.57 s | $0.001381 |
| Whole functions | 90.9% | 100% | 11 | 1 | 0.97 s | $0.000256 |

Neighboring context already recovers the missed history-write case. Whole functions retain that
case coverage while avoiding many overlapping line matches. Both contextual approaches wrongly
classify c12, the temporary-file cleanup function.

Total provider-reported cost across all six runs: **$0.004866498**. This is input/output as reported
by the provider; it is not a promise about future pricing or a large-repository cost projection.

## First useful output and review burden

First positive-case output above threshold appeared after 0.823 s for diff lines, 0.280 s for added
lines, and 0.860 s for whole hunks. For source code it was 0.382 s for lines, 0.278 s with `-C 2`,
and 0.284 s for whole functions. Whole hunks reduced total work here but did **not** deliver the
first useful result earlier than ordinary diff lines.

The fixture order puts positive examples first. These times depend on that order, provider
variation, concurrency, and ordered output. No human review time was measured. The count of
records to review is a workload proxy, not evidence that human review became proportionally faster.
The richer records also contain more text than individual lines.

## jselect handoff: an unfavorable result is retained

The companion selection check reuses the frozen whole-function scores with a custom scorer and
`jselect==0.1.0`. It makes **zero additional model calls**. The comparison changes only jselect's
diversity penalty; both modes use the same relevance threshold, token accounting and packing rule.

At 250 tokens, both modes selected five functions. With diversity disabled, all five came from
positive cases (217 tokens). With the default diversity of 0.7, four came from positive cases and
one was the misleading cleanup case c12 (214 tokens). At 500 tokens, both selected the same eleven
eligible functions, including c12, in different orders (496 tokens).

This supports the source-linked, bounded-context handoff, but **does not establish improved
selection quality** on this workload. Novelty can promote a distinct false positive. There is no
claim that jselect verifies classifier judgments or reduces this workload's false-positive rate.

## Reproduce

```bash
uv sync --extra code
uv run --extra code python bench/code_review.py --output bench/out/code-review.json
# To recreate the frozen-score packing check (writes its report under docs/benchmarks):
uv run --extra code --with jev-select==0.1.0 python bench/code_selection.py
```

The live run uses an OpenRouter key already configured for jgrep and an observed-spend guard of
$0.25 across runs. In-flight requests and retries can make actual billing exceed that guard.

Frozen inputs: [`bench/fixtures/code_review.json`](../bench/fixtures/code_review.json).
Full per-case decisions, source excerpts, costs, latency and fixture hash:
[`benchmarks/code-review.json`](benchmarks/code-review.json).
Packing choices and exact contexts:
[`benchmarks/code-selection.json`](benchmarks/code-selection.json).
