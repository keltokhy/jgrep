# jgrep

grep, but the pattern is a description.

```console
$ tail -f app.log | jgrep "a user is getting frustrated"
user 12: this is the third time checkout has failed, I am done with this app
user 77: WHY does it log me out every five minutes??

$ jgrep -o "announces or releases a new AI model" titles.txt | sort -rn | head -3
0.980	PrismML Launches Bonsai 2 27B, Its Most Capable Model Yet
0.970	Alibaba Releases Qwen3.8-Omni-Flash
0.940	Google announces new experimental "CC" AI agent for families
```

Each line becomes one yes/no question to [Jev](https://docs.typesafe.ai), TypeSafe's decision
model. Jev does not generate text. It returns a probability in about 200 ms for about a
thousandth of a cent, which is fast and cheap enough to sit in a pipe. jgrep reads lines as
they arrive, judges them concurrently and prints matches in input order, so it works on
`tail -f` as well as on files.

Measured on 994 Hacker News titles: 4.6 seconds and $0.012 for one description, and the same
time for three descriptions at once.

## Install

```bash
uv tool install git+https://github.com/keltokhy/jgrep     # or: pipx install git+https://...
```

jgrep needs a key for one of two APIs. With keys for both, it uses TypeSafe's.

| API | Key | Get one |
|---|---|---|
| TypeSafe | `TYPESAFE_API_KEY` | [console.typesafe.ai](https://console.typesafe.ai/settings/keys) |
| OpenRouter | `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |

Set the environment variable, or put the key in `~/.config/jev/typesafe.key` or
`~/.config/jev/openrouter.key`. Force a choice with `--api` or `JEV_API`.

## Use

```bash
jgrep "a complaint about noise" complaints.txt          # lines that fit
jgrep -v "spam" inbox.txt                               # lines that do not
jgrep -c "asks a question" *.txt                        # counts per file
jgrep -p 0.9 "mentions a specific dollar amount" f.txt  # only confident matches
jgrep -o -p 0 "the writer is losing sleep" f.txt | sort -rn   # rank every line
jgrep -e "about economics" -e "about New York" f.txt    # either; add --all for both
jgrep --para "describes an identification strategy" paper.txt
jgrep --whole "uses a bunching estimator" abstracts/*.txt     # prints matching file names
jgrep -q "a stack trace" build.log && notify "build broke"
```

| Option | Meaning |
|---|---|
| `-p P` | Match when the probability is at least P. Default 0.5. |
| `-o` | Put the probability in a first, tab-separated column. |
| `-v`, `-c`, `-n`, `-H`, `-m NUM`, `-q` | As in grep. |
| `-e DESC` | Another description. All of them go in one call per line. A line matches if any fits, or all with `--all`. |
| `--para`, `--whole` | Judge paragraphs or whole files in place of lines. |
| `--json` | One JSON object per match, with the probability. |
| `--unordered` | Print matches as answers arrive. |
| `-j N` | Calls in flight. Default 32. |
| `--budget DOLLARS` | Stop once this much is spent. Default 1.00, or `$JGREP_BUDGET`; 0 for no limit. |
| `--timeout SECONDS` | Give up on a line after this long, retries included. Default 15. |
| `--no-cache`, `--api`, `--model`, `--stats` | See `jgrep --help`. |

Exit status follows grep: 0 if anything matched, 1 if nothing did, 2 on error.

## Cost

A call bills roughly 270 tokens of fixed overhead plus the line and the description, so a
typical line costs about 300 tokens, or $0.0000126 at $0.042 per million. A million lines is
about $13. Blank lines, repeated lines and anything answered before are free: answers are
cached in `~/.cache/jev/answers.sqlite`, keyed on the exact model, line and description.
Extra `-e` descriptions add about 27 tokens each and no time.

jgrep stops at `--budget`, one dollar by default, so a stray `jgrep pattern huge.log` cannot
run up a bill. A dollar is about 80,000 lines. A stopped run loses nothing: rerun with a higher
budget and everything already judged comes from the cache. For a long-lived `tail -f` monitor,
set your own default once with `export JGREP_BUDGET=20`, or `0` for no limit. With `--stats`, or whenever stderr is a terminal, it prints what the run cost:

```
jgrep: 994 records, 33 matched; 994 calls, 0 cached; 292,839 tokens; $0.0123; 4.6s
```

## How well does it work

`bench/phrasing.py` scores 30 hand-labeled complaint lines against five descriptions of
different grammatical shapes, including a negation and a question. Jev got all 150 right, under
each of four ways of wording the question. Asking five descriptions in one call changed no
decision and moved probabilities by 0.001 on average. Latency was flat at about 210 ms from 1
to 64 questions per call.

That corpus is easy on purpose. On borderline lines the probabilities land in between, which
is what `-p` is for:

```
0.65  [a complaint about noise]  The music from the church on Sunday mornings is lovely but it does start early.
0.46  [does not mention a landlord]  The owner of the building never answers the phone.
```

Things to know:

- These are a model's judgments. Check a sample before you rely on a filter.
- Jev answers the description you wrote, not the one you meant. TypeSafe
  [documents](https://docs.typesafe.ai/model-jaggedness/jev-1.13) weak spots: counting,
  comparing numbers or dates, double negatives, and long inputs full of irrelevant detail.
- Each line is judged alone. jgrep does not show Jev the lines around it.
- Jev is close to deterministic, not exactly so. Asking 150 questions three times without the
  cache gave identical probabilities for 128; the rest moved by up to 0.03 and no decision
  flipped. The cache makes reruns exact.
- The default model ID is an alias for the latest Jev. For results that must reproduce, pin
  one with `--model` (for example `typesafe/jev-1.13` on OpenRouter).
- Text in the input can try to steer the answer. Do not use jgrep as a security boundary.

## Development

```bash
uv sync && uv run pytest        # 23 tests against a fake API; no key, no network
uv run python bench/phrasing.py # live; costs about a cent
```

`src/jgrep/core.py` is the client: two backends, retries inside a time budget, the cache,
in-flight deduplication and the cost meter. It is shared verbatim with
[jlink](https://github.com/keltokhy/jlink), which links records across datasets with the same
model.

MIT license.
