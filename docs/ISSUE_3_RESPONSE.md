# Response to issue #3

[Issue #3: Honest assessment of jgrep](https://github.com/keltokhy/jgrep/issues/3)
identifies a useful distinction: finding plausible matches can still leave substantial review
work. Its 448-hit example motivates judging larger units and searching changes, while its
navigation example motivates returning complete functions with source locations.

The first implementation shipped in
[0.2.0](https://github.com/keltokhy/jgrep/releases/tag/v0.2.0), before this documentation PR.
Review the code in
[commit 3a2d935](https://github.com/keltokhy/jgrep/commit/3a2d9357c5f1122f85757c9b816d3c35399dcbf2).
This response records what that release addresses, what the experiments support, and what
remains open. It does not claim to resolve every suggestion in the issue.

## What shipped

| Issue concern | Available in 0.2.0 | Boundary |
|---|---|---|
| Isolated lines omit relevant code | `--functions` judges complete Python and Go functions/methods. Go uses the optional `[code]` parser. | No caller, import, or cross-function analysis. Comments remain attached. Oversized units fail rather than truncate. |
| Review changes before scanning an entire repository | `--diff` judges complete unified hunks, including removed lines, additions, and supplied context. | It cannot retrieve context omitted from the patch. This includes deletion-only changes that added-line filtering would miss. |
| Understand cost before sending code | `--estimate` previews records, exact cache reuse, approximate calls, and cost offline. | It reads finite input to EOF. Estimates are not billing guarantees and cannot predict answer-dependent early stopping. |
| Move useful evidence into a bounded review | `--emit-records` exports exact source-linked units for tools such as jselect. | Export does not judge relevance. Selection does not verify a match or establish that it is a bug. |

Existing `-C`, `--para`, `--whole`, and `--chunks` already allow more context than one line.
The new modes add explicit code boundaries and change semantics. Paid filtering still sends
the selected source text to the configured provider; the two offline modes do not.

## Try it on a change

```bash
uv tool install 'jev-grep[code]'

# Save a finite patch with enough surrounding context for the question.
git diff --no-color -U10 > review.patch

# No authentication or model call is needed for this preview.
jgrep --diff --estimate "removes error handling for a persistent write" review.patch --json

# This sends the hunks to the configured provider and returns matching changes.
jgrep --diff "removes error handling for a persistent write" review.patch --json

# For navigation, return whole functions for inspection.
jgrep --functions "handles cancellation during retry backoff" . -r --glob '*.go' --json
```

Use `git diff --cached --no-color -U10` for staged changes. Filtering exits 0 for matches,
1 for no matches, and 2 for errors. A match is evidence to inspect, not a confirmed defect.
Full input, output, and cost contracts are in the [README](../README.md).

## What the measurements support

The [reproducible experiment](CODE_REVIEW.md) uses 20 handwritten Go change fixtures,
with labels fixed before running the model. It is not a reproduction of the issue's
22,054-line repository run or a held-out production benchmark.

- Complete hunks reduced matching records from 22 to 10 while retaining all eight positive
  cases. Two negative cases still matched. The hunk run also used a change-specific question,
  so the comparison does not isolate the effect of record size.
- Whole functions retained the same positive-case coverage as `-C 2`, with 11 matching
  records instead of 45. Both approaches still flagged a benign cleanup function.
- Whole hunks did not produce the first useful result earlier than ordinary diff lines.
  Record counts are only a review-work proxy; no human review time was measured.
- In a separate frozen-score jselect check, the default diversity setting introduced a
  false positive at a 250-token budget. That result supports keeping selection quality
  separate from the ability to produce a source-linked context within a token limit.

The report includes questions, case labels, per-record decisions, cost, timing, source text,
and commands to reproduce the runs. These results do not establish reliable logic-bug detection.

## Suggestions still open

These are unresolved proposals, not promised features or scheduled work.

| Suggestion | Current decision or evidence needed |
|---|---|
| Cluster similar code and inherit a representative's verdict | Not implemented. Similar syntax can serve different purposes, such as temporary cleanup and rollback. Grouping for display could preserve each location and judgment without assuming semantic equivalence. |
| Normalize cache keys | Exact request caching remains. Any normalization needs language-specific checks that it preserves the meaning presented to the model, including strings and significant whitespace. |
| Re-judge candidates or take three votes | Not implemented. Repeated judgments can share the same errors; agreement alone does not establish correctness or determinism. Evaluate recall, false positives, and extra cost on separately labeled cases. |
| Auto-tag cleanup, idiom, or real bug | Not implemented. A text-classification benchmark does not validate code-defect labels. Descriptive tags would need their own labeled evaluation and should not imply verification. |
| Strip comments or shortlist by signatures first | Functions retain comments and bodies. Stripping comments can remove useful intent; a signature-only first pass can discard relevant bodies. Measure these tradeoffs before making either a default. |
| Statement units and comment-versus-behavior audits | No dedicated modes yet. Full functions provide an input unit for experiments, but do not prove that a comment's behavioral promise holds. |
| Analyzer triage and SARIF | No dedicated integration yet. JSONL/CSV field filtering can consume exported findings, but does not provide analyzer execution, explanations, or a SARIF contract. |
| Numeric-fact envelopes | Not implemented. Extracted numbers alone do not establish their role or a comparison's meaning. Deterministic checks should own arithmetic where the task permits it. |

To compare against the original run, we need its repository and commit, exact commands and
descriptions, provider/model, cache conditions, raw output, and the reviewed labels. Those
details would let us compare complete-function and diff modes against the same workload and
identify which changes actually reduce review effort.
