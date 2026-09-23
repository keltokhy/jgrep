# Jev, DiffusionGemma, and Laya on jgrep's benchmarks

Run September 22, 2026, from 21:13 to 21:43 America/New_York, on an Apple M3 Ultra with 96 GiB of
unified memory, on the inputs frozen for the
September 21 comparison: 2,000 of the 5,574 messages in the UCI SMS Spam Collection (268 of them
spam) and 2,000 of the 7,600 AG News test articles (500 per class), both drawn with seed 20260921;
the 20 handwritten Go changes of the [code-review experiment](../CODE_REVIEW.md) in its six record
modes; and the 150-record length diagnostic in `bench/context_eval.py`. Each local model ran through
the installed `jgrep` command with the provider named on the command line, `--no-cache`, an empty
cache directory and 4 requests in flight, one model and one benchmark at a time. Jev 1.13 was not
called again: its column is its September 21 run on the same inputs. `bench/backend_eval.py` runs
and scores it; [local-models-2026-09-22.json](local-models-2026-09-22.json) is the frozen output.

## SMS spam

One description, `an unsolicited spam, scam or marketing text message`, one call per message.

| | Jev 1.13 (`typesafe/jev-1.13`, OpenRouter) | DiffusionGemma (OpenJev, `openjev-0.1`, MLX) | Laya (`laya-421m`, MLX) |
|---|---:|---:|---:|
| Precision at 0.5, the default | 86.24% | 80.45% | 53.09% |
| Recall at 0.5 | 95.90% | 93.66% | 99.25% |
| F1 at 0.5 | 90.81% | 86.55% | 69.18% |
| False positives / false negatives at 0.5 | 41 / 11 | 61 / 17 | 235 / 2 |
| Precision at 0.9 | 99.10% | 88.76% | 78.98% |
| Recall at 0.9 | 82.46% | 85.45% | 92.54% |
| F1 at 0.9 | 90.02% | 87.07% | 85.22% |
| False positives / false negatives at 0.9 | 2 / 47 | 29 / 39 | 66 / 20 |
| Brier score (lower is better) | 0.0244 | 0.0310 | 0.0911 |
| Expected calibration error, 10 bins | 0.0736 | 0.0268 | 0.1490 |
| Wrong with p ≥ 0.9 or p ≤ 0.1 | 5 | 38 | 67 |
| Messages answered | 2,000 | 2,000 | 2,000 |
| Wall-clock | 198.4 s* | 596.2 s | 46.2 s |

*Two attempts, at 16 and then 8 requests in flight; see the caveats.

Precision / recall / F1 in percent at the seven thresholds fixed on September 21:

| Threshold | Jev | DiffusionGemma | Laya |
|---|---:|---:|---:|
| 0.1 | 36.35 / 98.88 / 53.16 | 65.74 / 96.64 / 78.25 | 28.53 / 99.63 / 44.35 |
| 0.3 | 72.45 / 98.13 / 83.36 | 76.05 / 94.78 / 84.39 | 44.50 / 99.63 / 61.52 |
| 0.5 | 86.24 / 95.90 / 90.81 | 80.45 / 93.66 / 86.55 | 53.09 / 99.25 / 69.18 |
| 0.7 | 93.66 / 93.66 / 93.66 | 83.90 / 91.42 / 87.50 | 61.61 / 97.01 / 75.36 |
| 0.9 | 99.10 / 82.46 / 90.02 | 88.76 / 85.45 / 87.07 | 78.98 / 92.54 / 85.22 |
| 0.95 | 99.49 / 72.76 / 84.05 | 94.67 / 79.48 / 86.41 | 83.92 / 89.55 / 86.64 |
| 0.99 | 100.00 / 10.07 / 18.31 | 100.00 / 58.21 / 73.58 | 89.33 / 84.33 / 86.76 |

## AG News

Four descriptions in one call per article: `news about world affairs, politics or conflict`,
`news about sports`, `news about business, markets or the economy` and `news about science or
technology`. Top-1 takes the most probable of the four as the label, ties going to the first in
that order. Macro F1 averages the four one-vs-rest F1 scores at a threshold.

| | Jev 1.13 (`typesafe/jev-1.13`, OpenRouter) | DiffusionGemma (OpenJev, `openjev-0.1`, MLX) | Laya (`laya-421m`, MLX) |
|---|---:|---:|---:|
| Top-1 accuracy | 86.75% | 87.35% | 92.45% |
| 95% Wilson interval | 85.19 to 88.17 | 85.82 to 88.74 | 91.21 to 93.53 |
| Macro F1 at 0.5 | 83.33% | 79.98% | 87.86% |
| Macro F1 at 0.9 | 84.15% | 83.04% | 34.22% |
| World affairs, F1 at 0.5 / 0.9 | 81.57 / 84.29 | 71.78 / 79.93 | 87.16 / 12.03 |
| Sports, F1 at 0.5 / 0.9 | 97.44 / 96.59 | 96.02 / 96.56 | 94.46 / 54.34 |
| Business, F1 at 0.5 / 0.9 | 71.35 / 76.69 | 70.73 / 73.32 | 81.66 / 48.28 |
| Science and technology, F1 at 0.5 / 0.9 | 82.95 / 79.01 | 81.39 / 82.35 | 88.15 / 22.22 |
| World affairs, precision / recall at 0.5 | 76.63 / 87.20 | 58.21 / 93.60 | 90.34 / 84.20 |
| Business, precision / recall at 0.5 | 56.99 / 95.40 | 55.85 / 96.40 | 77.42 / 86.40 |
| Articles answered | 2,000 | 2,000 | 2,000 |
| Wall-clock | 76.2 s | 693.1 s | 157.5 s |

## Code review

Case precision / recall at 0.5, in percent, on the 20 Go changes, with the number of records a
reviewer would read in brackets. A case counts as flagged when any of its records matches; the
questions and labels are those of the [code-review experiment](../CODE_REVIEW.md).

| Record mode | Jev 1.13 (`typesafe/jev-1.13`, OpenRouter) | DiffusionGemma (OpenJev, `openjev-0.1`, MLX) | Laya (`laya-421m`, MLX) |
|---|---:|---:|---:|
| Lines of the unified diff | 61.54 / 100 (21) | 33.33 / 12.50 (4) | 42.11 / 100 (36) |
| Added lines alone | 66.67 / 75.00 (11) | 0 / 0 (1) | 50.00 / 50.00 (8) |
| Complete hunks (`--diff`) | 80.00 / 100 (10) | 72.73 / 100 (11) | 54.55 / 75.00 (11) |
| Individual function lines | 75.00 / 90.00 (17) | 100 / 70.00 (7) | 50.00 / 100 (71) |
| Function lines with `-C 2` | 90.91 / 100 (45) | 76.92 / 100 (46) | 55.56 / 100 (67) |
| Whole functions (`--functions`) | 90.91 / 100 (11) | 83.33 / 100 (12) | 56.25 / 90.00 (16) |
| Wall-clock, six modes summed | 27.4 s | 112.8 s | 6.5 s |

## Length and evidence position

The 30 hand-labeled complaint lines of `bench/corpus.py`, each asked its five descriptions in one
call, alone and padded with neutral text to 2,000 or 7,000 characters with the evidence first or
last. Correct decisions at 0.5, out of 150 per condition:

| Condition | Jev 1.13 (`typesafe/jev-1.13`, OpenRouter) | DiffusionGemma (OpenJev, `openjev-0.1`, MLX) | Laya (`laya-421m`, MLX) |
|---|---:|---:|---:|
| Original line | 150 | 149 | 113 |
| 2,000 characters, evidence first | 150 | 149 | 113 |
| 2,000 characters, evidence last | 150 | 150 | 105 |
| 7,000 characters, evidence first | 150 | 150 | refused* |
| 7,000 characters, evidence last | 150 | 148 | refused* |
| Correct of the decisions answered | 750 of 750 | 746 of 750 | 331 of 450 |
| Wall-clock | 13.0 s | 77.0 s | 10.1 s |

*The adapter refused all 60 records of 7,000 characters with HTTP 422 because they do not fit
Laya's 512-token window; jgrep reported each as an error, 300 failed decisions, and exited with
status 2. On September 21 the adapter was set to crop such records instead, and Laya got 115 and
100 of 150 right on what it kept.

## The same models on September 21

Both local models had run these inputs the day before. Every answer they gave on September 22 is
identical to that run, probability for probability; the largest difference is 0.0.

| | DiffusionGemma | Laya |
|---|---:|---:|
| SMS records identical | 2,000 of 2,000 | 2,000 of 2,000 |
| News records identical | 2,000 of 2,000 | 2,000 of 2,000 |
| Code rows identical | 482 of 482 | 482 of 482 |
| Context records identical | 150 of 150 | 90 of 90 answered on September 22 |
| SMS wall-clock, September 21 / 22 | 548.0 s / 596.2 s | 22.9 s / 46.2 s |
| News wall-clock | 680.6 s / 693.1 s | 70.2 s / 157.5 s |
| Code wall-clock, six modes | 111.2 s / 112.8 s | 6.5 s / 6.5 s |
| Context wall-clock | 77.1 s / 77.0 s | 18.5 s / 10.1 s |

Laya's September 21 context time covers all 150 records, 60 of them cropped; September 22's covers
90 answers and 60 refusals.

## What the table says

- **DiffusionGemma gets most of the way to Jev on classification, at no API cost.** On spam its F1
  at 0.5 is 86.55% against Jev's 90.81%, from 61 false positives to Jev's 41. On news its top-1
  accuracy of 87.35% and Jev's 86.75% each lie inside the other's interval. Its calibration error
  is the lowest of the three (0.0268), but its Brier score is above Jev's (0.0310 against 0.0244)
  and it was confidently wrong 38 times to Jev's 5, which shows at `-p 0.9`: 29 false positives
  against Jev's 2. It read every 7,000-character record and got 746 of the 750 context decisions
  right.
- **On code, DiffusionGemma needs whole units.** With complete hunks or whole functions it flagged
  every positive case, at 72.73% and 83.33% case precision against Jev's 80.00% and 90.91%. Judging
  single lines of a diff it found 12.50% of the positive cases, and none from added lines alone;
  on individual function lines it was precise (100%) and missed 30%.
- **Laya is the fast local option, and good at sorting news into topics.** Its top-1 accuracy of
  92.45% has a 95% interval entirely above both other models', and its macro F1 at 0.5 is the
  highest of the three, 87.86%. It ran 2,000 messages in 46.2 s and 2,000 articles in 157.5 s,
  against 596.2 s and 693.1 s for DiffusionGemma.
- **Outside that, Laya's scores fall.** At 0.5 it flagged 501 messages as spam, 235 of them wrongly
  (precision 53.09%). Its spam F1 rises with the threshold, to 86.76% at 0.99, but that cutoff was
  not chosen in advance and would need its own validation. On news the opposite happens: its
  probabilities seldom reach 0.9, even for the right topic, so macro F1 at 0.9 is 34.22%. Its
  calibration error is 0.1490, and on code its case precision is between 42.11% and 56.25% in
  every mode. It reads at most 512 tokens including the question, so it refused the 60 records of
  7,000 characters, 300 of the 750 context decisions. The window is not the whole story: on the
  original short lines it got 113 of 150 right.
- **Speed.** At 4 requests in flight DiffusionGemma took about a third of a second per record, on a
  server that runs model work one call at a time. Jev's hosted times come from 4 to 16 requests in
  flight over the network and are not comparable with either local model's.
- **Both local models are deterministic.** Every answer matched September 21 exactly, so a rerun
  reproduces without the cache. Laya's only difference is the 60 long records, cropped then and
  refused now.

## Provenance and caveats

- **Jev column.** Jev 1.13 (`typesafe/jev-1.13` through OpenRouter, served as
  `typesafe/jev-1.13-20260917`) made no calls for this document. Its numbers are summarized, with
  the same code as the local runs, from four September 21 files in the `large-test-2026-09-21`
  bundle: `openrouter-spam-recovered.json` (16 then 8 requests in flight, deadlines 30 s then
  120 s), `jev-retry/openrouter-news.json` (8, 120 s), `openrouter-code-review.json` (8, 120 s) and
  `openrouter-context.json` (4, 120 s). The JSON records each file's path and SHA-256. SMS needed
  two attempts, a deadline and then an HTTP 520 losing one record each; it is scored from the
  recovered file, and its 198.4 s covers both attempts.
- **Other Jev numbers.** The README's full-dataset results (5,574 messages and 7,600 articles, run
  2026-09-18, `bench/out/accuracy-spam.json` and `accuracy-news.json`) are a different run on
  different inputs; the JSON keeps them under `jev_full_datasets`, marked not like for like. The Jev
  code column is the September 21 run, which differs from the September 19 run in
  [CODE_REVIEW.md](../CODE_REVIEW.md) only in the diff-lines mode.
- **Local servers.** DiffusionGemma: OpenJev at commit `e04794a`, serving `openjev-0.1` from
  `mlx-community/diffusiongemma-26B-A4B-it-4bit` revision `a7a8140`, which runs model work one call
  at a time on the GPU. Laya: laya-mlx at commit `fc1df62` with `aac6fef/laya-mlx` revision
  `0476785`, served as `laya-421m` through jevkit-core's `scripts/laya_server.py`, which refuses
  with HTTP 422 any request whose state would be cropped to fit its 512-token window, question
  included; jgrep reports a refusal as an error, not a non-match. Both on an Apple M3 Ultra with 96
  GiB of unified memory; during each run the other server was loaded but idle.
- **Concurrency and deadlines.** 4 requests in flight for both servers; deadline 300 s for
  DiffusionGemma (120 s on September 21) and 120 s for Laya. There were no deadline errors, so
  nothing was lowered, and each benchmark ran once. Every run had `--no-cache` and its own empty
  `XDG_CACHE_HOME`. Local API fees were $0; hardware and electricity are not priced.
- **Samples.** SMS and news are subsamples, 2,000 of 5,574 messages and 2,000 of 7,600 articles,
  drawn with seed 20260921 and frozen on September 21 with their source line numbers and hashes;
  the SMS sample has 1,925 unique texts. Code and context are the complete fixtures. These are old
  public corpora that any of the models may have seen in training. The code and context fixtures
  are small and synthetic, and the context test measures length and position, not 150 independent
  examples.
- **Nothing was tuned.** Descriptions, thresholds and scoring are those of September 21, and the
  runner refuses to start if a description in `bench/accuracy.py` has changed since. The seven
  thresholds were fixed before any model was scored.
- **Timing.** Each wall-clock figure is one warm run, including CLI start-up; code is the sum of
  the six modes' scan times, and each model's 441 code calls cover 482 rows because identical lines
  share one request. DiffusionGemma's times were within 9% of September 21. Laya's SMS and news
  took 2.0 and 2.2 times as long as on September 21, with unchanged answers. Its audit log puts the
  median request at the same server time as then (10.5 ms against 10.2 ms for SMS, 28.3 ms against
  28.9 ms for news); the difference is a tail of stalled requests, 83 over 0.1 s for SMS, up to
  1.3 s, and 138 for news, up to 2.6 s, against 2 and 30 on September 21. The cause was not
  verified; both servers run at nice 5 on a desktop that was in use.
- **Laya refusals** are counted from the adapter's audit log, which other tools share; each run's
  window held only its own requests. There were none for SMS, news or code, whose longest states
  were 243, 226 and 92 tokens, and 60 for context, all of them the 7,000-character records.
- **Reproduce.** `uv run --extra code python bench/backend_eval.py run --api diffusiongemma
  --dataset spam`, then `news`, `code` and `context`, then the same with `--api laya`, and
  `uv run python bench/backend_eval.py freeze`. The runner reads the frozen inputs, and the
  September 21 outputs it compares against, from the local bundles named in the JSON. Raw answers
  and logs are in `bench/out/local-2026-09-22/`, which is not committed; the JSON records each Laya
  run's line range in the adapter's audit log.
