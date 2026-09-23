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
uv tool install jev-grep        # the command it installs is jgrep
uv tool upgrade jev-grep        # upgrade an existing installation
```

For Go and C function parsing, install the optional syntax parsers: `uv tool install 'jev-grep[code]'`.
Python function parsing and unified diffs work with the base package.

jgrep needs a key for one of two APIs, or for a gateway of your own; a server on your own machine
needs none (both below). With keys for several, it uses TypeSafe's.

| API | Key | Get one |
|---|---|---|
| TypeSafe | `TYPESAFE_API_KEY` | [console.typesafe.ai](https://console.typesafe.ai/settings/keys) |
| OpenRouter | `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |

Set the environment variable, or put the key in `~/.config/jev/typesafe.key` or
`~/.config/jev/openrouter.key`. Force a choice with `--api` or `JEV_API`.

Behind an LLM gateway that serves System One (LiteLLM, Ramp Router, a corporate proxy), point
jgrep at it with `--api gateway`. The URL is the full endpoint and the key is the gateway's own:

```bash
export JEV_GATEWAY_URL=https://gateway.example.com/v1/systemone
export JEV_GATEWAY_API_KEY=...       # or ~/.config/jev/gateway.key and gateway.url
jgrep "a stack trace" build.log      # picked automatically when it is the only key set
```

Requests are sent as they would be to TypeSafe, so the gateway sees the same `{model, state,
questions}` body. Ask for a model the gateway knows with `--model`; the default is `jev-latest`.
`--stats` prices gateway calls at TypeSafe's list price, which may not be what the gateway bills.

### Local servers (experimental)

`--api diffusiongemma` and `--api laya` send the same requests to a System One server on your own
machine, an [OpenJev](https://github.com/razorback16/openjev) or
[laya-mlx](https://github.com/mizorewww/laya-mlx) process that you run separately. They are never
chosen automatically, need no key, and count as $0 in `--stats` and `--estimate` unless
`JEV_PRICE_PER_MTOK` is set. The runtime's [DiffusionGemma](https://github.com/keltokhy/jevkit-core/blob/main/docs/diffusiongemma.md)
and [Laya](https://github.com/keltokhy/jevkit-core/blob/main/docs/laya.md) guides explain the setup;
start with `-j 1` and a long `--timeout` while a local model warms up.

[How well does it work](#how-well-does-it-work) compares both with Jev, and the
[local-model comparison](https://github.com/keltokhy/jgrep/blob/main/docs/benchmarks/local-models-2026-09-22.md)
has the full results.

## Use

```bash
jgrep "a complaint about noise" complaints.txt          # lines that fit
jgrep -v "spam" inbox.txt                               # lines that do not
jgrep -c "asks a question" *.txt                        # counts per file
jgrep -p 0.9 "mentions a specific dollar amount" f.txt  # only confident matches
jgrep -o -p 0 "the writer is losing sleep" f.txt | sort -rn   # rank every line
jgrep -e "about economics" -e "about New York" f.txt    # either; add --all for both
jgrep -C 2 "a line in the middle of a stack trace" app.log   # judged with its neighbours
jgrep --para "describes an identification strategy" paper.txt
jgrep --whole "uses a bunching estimator" abstracts/*.txt     # prints matching file names
jgrep -q "a stack trace" build.log && notify "build broke"
jgrep --jsonl --field message "a payment failed" events.jsonl
jgrep --csv --field abstract "uses a natural experiment" papers.csv
jgrep -rl --glob '*.txt' "mentions a rent increase" notes/
jgrep --chunks 8000 --json "describes an identification strategy" paper.txt
```

| Option | Meaning |
|---|---|
| `-p P` | Match when the probability is at least P. Default 0.5. |
| `-o` | Put the probability in a first, tab-separated column. |
| `-v`, `-c`, `-n`, `-H`, `-q` | As in grep. |
| `-m NUM` | Stop each input file after NUM matches; `-m 0` reads no input and makes no API calls. |
| `-l` | Print a file name at its first matching record, then continue to the next file. |
| `-r` | Search directories recursively; defaults to the current directory if no paths are given. |
| `--glob PATTERN`, `--exclude PATTERN` | Include or exclude files; both can be repeated. |
| `--no-ignore` | During recursive search, disregard `.gitignore` and `.ignore`. |
| `-e DESC` | Another description. All of them go in one call per line. A line matches if any fits, or all with `--all`. |
| `--para`, `--whole` | Judge paragraphs or whole files in place of lines. |
| `-C N` | Show Jev the N lines either side of each line. Still one decision per line, and still only the matching line is printed. |
| `--json` | One JSON object per match, with the probability. |
| `--jsonl --field NAME`, `--csv --field NAME` | Judge one field and return the complete original record. |
| `--chunks N`, `--overlap N` | Search full text files in overlapping passages, with source locations. |
| `--diff` | Judge each complete unified diff hunk, including removed lines and unchanged context. Reads plain patches and `git log -p` or `git format-patch` streams. |
| `-W`, `--function-context`, `--repo DIR` | With `--diff`, also show Jev the Python/Go/C function that encloses each hunk, read at the hunk's commit or from the working tree. Still one decision per hunk, and still only the hunk is printed. |
| `--functions`, `--lang python\|go\|c` | Judge complete functions/methods with adjacent comments; infer language from the extension, or specify it for stdin. |
| `--estimate` | Read to EOF and preview calls and approximate cost without authentication or API calls. Add `--json` for a single report. |
| `--emit-records` | Export source-linked JSONL without judging; omit DESCRIPTION. Useful for inspection and other tools. |
| `--max-chars N` | Maximum characters judged per ordinary record; default 8000. Truncation produces a warning. |
| `--unordered` | Print matches as answers arrive. |
| `-j N` | Calls in flight. Default 32. |
| `--budget DOLLARS` | Stop once this much is spent. Default 1.00, or `$JGREP_BUDGET`; 0 for no limit. |
| `--timeout SECONDS` | Give up on a line after this long, retries included. Default 15. |
| `--no-cache`, `--api`, `--model`, `--stats` | See `jgrep --help`. |

Exit status follows grep: 0 if anything matched, 1 if nothing did, 2 on error.
Offline estimation and record export exit 0 on success, including empty input, and 2 on errors.

With ordered output, `-j N` bounds the total of active requests and completed results waiting
for earlier records. A slow first record therefore cannot let the rest of the input run ahead.
`--unordered` releases each slot as soon as its result arrives.

## Changes and complete functions

```bash
# Review both sides of each change, including deletion-only hunks
git diff --no-color | jgrep --diff "removes error handling for a persistent write"
jgrep --diff "weakens cancellation handling" review.patch --json

# Judge each hunk together with the function around it; print the hunk alone
git diff --no-color | jgrep --diff -W "removes a length check before a copy"
git log -p --no-color | jgrep --diff -W -p 0 --json "changes who frees a buffer" > scored-hunks.jsonl

# Read the entire function; comments and decorators stay attached
jgrep --functions "ignores a failed rollback" plugin/installer.go --json
jgrep --functions "releases connections on every exit path" src/ -r --glob '*.py'

# Preview exactly the units the filter would judge; no key or paid call needed
git diff --no-color | jgrep --diff --estimate "removes error handling" --json

# Export functions for jselect without making any jgrep model calls
jgrep --functions --emit-records src/ -r > functions.jsonl
jselect "How does cancellation work?" functions.jsonl --tokens 2000
```

`--diff` accepts ordinary unified patches, including Git diffs, from files or stdin. One decision
covers a complete hunk: both removed and added lines, plus the context supplied in the patch.
Use `git diff -U10` when you need more surrounding lines. Each emitted hunk repeats its file headers.
Unless `-W` is given, it does not read the working tree or retrieve omitted context. Binary changes,
metadata-only changes (such as mode-only edits), combined merge diffs, and malformed hunks produce
errors rather than silently reporting no match. An empty diff contains no records. Patch lines are
counted at line feeds only, so a form feed or a lone carriage return inside a source line does not
shift locations.

`--diff` also reads a history: `git log -p`, `git show` and `git format-patch` output. Each commit's
header, message and diffstat are skipped, and its hunks carry the commit id as `unit.commit`, which
is null for a plain patch. Such a stream is read one commit at a time, so memory follows the
largest commit, not the length of the history. Commit ids are recognized in Git's default header
(`commit <id>`, full or abbreviated down to four hex digits) and in mbox separators. In a
`git format-patch` mail the patch begins after the last `---` separator, so a message that itself
contains `diff --git` is not mistaken for the patch, and the trailing `-- ` signature is not read as
diff content. With `--oneline` or a custom `--format` no id is recognized: hunks carry none, `-W`
reads the working tree and reports `source_mismatch` if the hunk's new-side lines differ at their
given position. Unindented message text can be rejected as content outside a hunk.

A commit without a patch, such as a merge, has no records. A commit whose patch cannot be read,
such as a combined merge diff or one with a malformed quoted path, is an error naming that commit,
and later commits are still read. An annotated tag's preamble is skipped. A final rename, mode-only
change or empty-file change still reports a metadata-only error, while the same commit's text hunks
are judged. Quoted paths decode both Git's octal byte escapes and raw UTF-8 from
`core.quotePath=false`.

Two pre-existing diff-reader limitations remain: CRLF-converted patches can leave a trailing `\r`
in `new_file` (for example, `"a.py\r"`) and produce spurious metadata-only errors; Git-quoted paths
containing spaces can also produce a spurious metadata-only error.

For diff JSON, `file`, `line`, and `end_line` locate the hunk in the **input patch**, while `unit`
contains `commit`, `old_file`, `new_file`, `old_start`, `old_count`, `new_start`, and `new_count`.
Missing file sides are null; zero-length ranges retain the unified diff's insertion/deletion anchor.
`-c` counts matching hunks per input patch and `-l` names matching input patches.

### The function around a hunk

A hunk carries three unchanged lines either side of a change, which is often too little to tell
whether a removed check mattered. `-W` (`--function-context`, named after `git diff -W`) judges each
hunk together with the function that encloses it, read from the commit or working tree. It is
still one decision per hunk and only the hunk is printed: the contract `-C` has for lines.

When a hunk carries a commit id, the new side of its file is read from that commit in `--repo DIR`
(default: the repository of the current directory). This is the blob `git show <commit>:<path>`
prints, fetched through one `git cat-file --batch` process. Otherwise the file is read from the
working tree, with patch paths taken from the repository root; a path that leaves the repository
is not read. In both cases the file must contain the hunk's new-side lines at the hunk's position,
or context falls back to `source_mismatch`. This checks only those lines at that position, not the
file's identity or its remaining contents: a different file with identical lines at the same
position would also attach. Without a commit id, the context is labelled "as it reads in the
working tree"; that tree may contain later edits outside the hunk.

Functions come from the readers `--functions` uses: Python, and Go and C with the `[code]` extra.
A function encloses the change when it contains an added line, or the lines on both sides of a
removal. Unchanged hunk lines that reach into a neighbouring function do not pull it in. When a
change touches several functions, the context runs from the first to the last of them. A removal
at the very end of a Python function can fall outside the remaining span and be counted as
`outside_function`.

`-W` reads files that the patch names and sends the enclosing function to the API, so point
`--repo` at a repository you trust. A patch is treated as untrusted input. jgrep confirms the
repository's own work tree before reading it: the allowed root is the directory that holds
`--repo`'s `.git`, found without consulting repository config, and if Git reports its work tree
elsewhere (`core.worktree`, `GIT_WORK_TREE`) the run stops rather than reading outside files.
Working-tree paths are resolved with their symlinks and must stay inside that root, so `..`, an
absolute path, or a symlink pointing outward is refused. Every Git call runs with a fixed argument
list, never a shell, and with an environment (`GIT_NO_LAZY_FETCH`, `GIT_NO_REPLACE_OBJECTS`,
`GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `protocol.ext.allow=never`) that keeps a hostile
repository from fetching a named object from a promisor remote, following object replacements, or
prompting. This is not a sandbox: `--repo`'s object database and `objects/info/alternates` are
trusted, and a repository you do not control can still cost proportional time by naming large
objects. A source file larger than 10 MiB is not read at all (`source_too_large`).

Context is never dropped silently. A hunk that cannot be given its function is judged alone, with
exactly the request plain `--diff` sends, so the two share cached answers. The reason is counted:

| Reason | The hunk is judged alone because |
|---|---|
| `deleted_file` | The file does not exist after the change. |
| `deletion_only` | The hunk leaves no new-side lines (`+N,0`, as with `-U0`), so there are no lines to check against the file. A removal with unchanged lines can get context if it lies inside the remaining function span. |
| `unsupported_language` | The extension is not `.py`, `.go`, `.c` or `.h`. |
| `parser_unavailable` | The file is Go or C and the `[code]` extra is not installed. |
| `source_unavailable` | The commit or path is not in `--repo`, or the working-tree file cannot be read (missing, a directory, a device, a named pipe, or outside the repository). |
| `source_too_large` | The new-side file is larger than 10 MiB, so it is not read or parsed. |
| `source_mismatch` | The file does not contain the hunk's new-side lines at that position: another checkout, a reversed patch, or edits made since the patch was written. |
| `syntax_error` | The file did not parse or, in C, the change lies in a function or region the parser could not read. |
| `outside_function` | The changed lines are not inside a function. |
| `function_in_hunk` | The hunk already contains the whole function; repeating it would only add cost. |

The totals are printed to stderr whenever a hunk was judged alone or a context was shortened, and
`--stats` adds how many records had context. Each JSON match has `unit.context`: either `symbols`,
`language`, `line`, `end_line`, `shown_line`, `shown_end_line` and `truncated`, or
`{"fallback": reason}`. `--estimate --json` reports the same totals under `function_context`.

The hunk is the judged unit and the function is context, so sizes follow `-C`, where a line and each
neighbour have their own limit. A hunk over `--max-chars` still fails with its size and location,
with or without `-W`. A function over `--max-chars` is shortened, not refused: the context, its
one-line header included, is held to `--max-chars` by keeping the whole lines nearest the change,
the header states which lines are shown, `unit.context.truncated` is true, and the run reports how
many contexts were shortened. One request therefore holds at most `--max-chars` of hunk and
`--max-chars` of context. `--estimate` prices that same request, and `--emit-records` adds a
`context` field with the text the judge would see after the hunk, or null when the hunk is judged
alone.

`-W` requires `--diff`, so it cannot be combined with `--functions`, `-C`, `--para`, `--whole`,
`--chunks` or structured input, and `--repo` requires `-W`. Git can widen hunks itself with
`git log -p -W`, using line patterns rather than a parser; the widened hunk is then the judged and
printed unit, and fails when it exceeds `--max-chars`.

`--functions` supports Python through the standard-library AST, and Go and C through the optional
Tree-sitter parsers. It extracts named functions and methods, retaining decorators, docstrings,
adjacent comments, and nested function bodies. Nested functions are not emitted again separately.
Imports, class-level state and callers are not automatically attached. Recursive discovery skips
other extensions unless `--lang` is explicit. Syntax errors are reported rather than guessed around.
Function JSON includes `unit.language`, `unit.symbol`, and exact decoded-character `start`/`end`
offsets with one-based source lines. The Python API exposes the same deterministic readers in
`jgrep.code_inputs.function_records` and `diff_records`.

C is inferred from `.c` and `.h` and is parsed as written, before preprocessing. The parser sees a
macro as an identifier and reads every branch of an `#if`, so a function defined once per branch
is emitted once per definition. Macro-heavy C often does not parse cleanly: an unknown attribute
macro on a parameter, a type passed to a macro (`va_arg(ap, char *)`), or an `#if` that splits a
statement each leave an error in the syntax tree. Refusing those files would refuse most C, so the
rule applies per function. A function is emitted only when the parser read all of it without
error. A function with an error inside it, or an unparsed region that could hold one, is skipped
and reported by its covered line range in one error per file, with exit status 2; the file's other
functions are still judged. This includes macro-headed bodies such as `TEST(Suite, Name) { ... }`
and `SYSCALL_DEFINE2(...) { ... }`; with `-W`, changes inside these regions use `syntax_error`.
Errors in text with no parameter list and brace, such as a prototype carrying an unknown macro or
an `extern "C"` guard, cannot hide a function and are not reported. A macro that expands to a whole
definition or to braces is invisible to this reader. `.h` is read as C, so C++ and Objective-C
headers are mostly reported as unparsed.

Diffs and functions **never truncate**: units over `--max-chars` fail with their size and location.
Raise that limit deliberately if needed. These modes cannot combine with `-C`, `--para`, `--whole`,
`--chunks`, or structured input. A source file or a plain patch is read into memory whole, and a
commit stream one commit at a time; keep live streams in line mode.

`--emit-records` writes one object containing `schema_version`, `id`, `text`, `source`, line/span
locations and `unit`. IDs include the source location and a text hash, so tools that select only
`text` and `id`, including jselect, retain a traceable reference. It exports full parsed records,
without ordinary line-mode truncation or a relevance judgment. Do not supply a description or match
filters. Errors are JSONL objects with an `error.message` and cause exit 2; a pipeline must check
the producer's exit status before treating its export as complete.

These modes retrieve evidence for review. Model scores do not prove a bug or certify that a change
is safe. Results and observed failures on 20 handwritten examples are in the
[code-review experiment](https://github.com/keltokhy/jgrep/blob/main/docs/CODE_REVIEW.md), and the
same examples on the two local servers are in the
[local-model comparison](https://github.com/keltokhy/jgrep/blob/main/docs/benchmarks/local-models-2026-09-22.md#code-review).

## Cost preview

`--estimate` uses the same input mode, selected field, context window, function context,
descriptions and model as the filter. It reads existing cached answers in read-only mode and
estimates reuse of exact repeated requests. It does not normalize whitespace or identifiers, create
a cache, or contact the provider.
Without a configured provider it uses TypeSafe's default model; use `--api` and `--model` to preview
a specific setup. No API key is required.

The JSON report includes record/cache/duplicate counts, `estimated_calls`, `call_upper_bound` before
new duplicate reuse, `estimated_input_tokens`, `estimated_cost_usd`, `byte_estimate_cost_usd`, errors
and assumptions. Costs use `JEV_PRICE_PER_MTOK` (default $0.042 per million input tokens). The nominal
estimate is UTF-8 request bytes divided by four plus 270 overhead tokens per request; the broader
byte estimate uses those bytes plus 1,024 overhead. Neither is a provider quote or guaranteed cap.
Retries, gateway pricing and concurrent cache misses can change actual cost.

Preview reads to EOF and ignores matching stop conditions (`-q`, `-l`, `-m`, threshold and budget),
because their effects depend on model answers. It reports ordinary-record truncation and oversized
code-unit errors. Use finite input, not an endless `tail -f` stream. With `--json`, the preview is one
object; `errors` makes a partially readable collection explicit and the exit status is 2.

## Structured records

`--jsonl` expects one JSON object per line. `--csv` expects a header with unique column names
and supports quoted commas and multiline cells. Both require `--field NAME`. Only that field
is sent for judgment; `-C` also supplies that field from neighboring records. Matching output
retains the complete JSON line or CSV row, including fields that were not judged. CSV output
includes the original header once for each input file with matches. Record terminators are
written as newlines, while quoting and embedded newlines are preserved.

JSON field names can be dotted paths, such as `event.message` or `events.0.message`. An exact
key takes precedence over a dotted path. Strings are judged directly, null is treated as empty
text, and other values are represented as JSON. Missing fields, invalid JSON, and malformed
CSV rows are reported as errors; valid later records are still processed when parsing can
continue. Blank JSONL lines are skipped.

Structured output omits automatic filename prefixes so it can be read as JSONL or CSV. Use
`-H` or `-n` only when you want those prefixes. `--json` instead emits a match object containing
`file`, the starting physical `line`, `p`, `text` (the original record), `field`, and `record`
(the parsed object, with CSV column values kept as strings). For several CSV files with
different headers, this JSON output is easier to combine. `-c` counts matching records, and
`-l` lists matching files without headers or rows.

## Directory and document search

`-r` visits files in sorted order, respecting `.gitignore` and `.ignore` in each directory and
parent ignore files inside a Git repository. It skips symlinks, `.git` directories, and files
with a NUL byte in the initial binary check. `--glob '*.txt'` selects names anywhere below the
search root; patterns can also match relative paths. `--exclude` uses gitignore patterns.
`--no-ignore` disables ignore files but still honors explicit exclusions. Explicit file
arguments bypass ignore files; include/exclude filters still apply. Use `-l` when only the
matching paths are needed.

Ordinary lines, paragraphs, selected fields, and `--whole` judgments are limited to the first
8000 characters by default. jgrep reports when this truncates a record or its context. Raise
`--max-chars` to change that limit, or use `--chunks N` to search the entire text file a passage
at a time. Chunk size replaces the ordinary character limit. Overlap defaults to the smaller
of 200 characters and one quarter of the chunk size; `--overlap 0` disables it.

Chunk output includes the starting line number. With `--json`, each matching passage also has
`chunk` (one-based), `start` and `end` (zero-based character offsets, end exclusive), and
`end_line` (the last source line containing characters from the passage). Offsets count decoded
Unicode characters, not bytes. Passages can overlap and are judged independently; their scores
are not combined into a document-wide probability. `-c` counts matching passages, while
`--chunks 8000 -l` lists files with at least one matching passage.

Chunk mode applies to plain text files and cannot be combined with structured input,
`--para`, `--whole`, or `-C`; use overlap to retain text across passage boundaries. These modes
read text, not PDF or Word formats.

## Cost

A call bills roughly 270 tokens of fixed overhead plus the line and the description, so a
typical line costs about 300 tokens, or $0.0000126 at $0.042 per million. A million lines is
about $13. Blank lines, repeated lines and anything answered before are free: answers are
cached in `~/.cache/jev/answers.sqlite`, keyed on the provider, endpoint, exact model, judged text
and description. Changing gateways cannot reuse another endpoint's answers. Older cache entries
without provider/endpoint identity are not reused, so the first rerun may make fresh calls.
Extra `-e` descriptions add about 27 tokens each and no time. `-C N` sends 2N+1 lines in
place of one, so `-C 2` costs roughly three times as much per line once the fixed overhead is
counted. `-W` adds the enclosing function, up to `--max-chars` characters, to each hunk that has
one; `--estimate` with the same options shows the difference before any call is made.

jgrep stops at `--budget`, one dollar by default, so a stray `jgrep pattern huge.log` cannot
run up a bill. A dollar is about 80,000 lines. A stopped run loses nothing: rerun with a higher
budget and everything already judged comes from the cache. For a long-lived `tail -f` monitor,
set your own default once with `export JGREP_BUDGET=20`, or `0` for no limit. With `--stats`, or
whenever stderr is a terminal, it prints what the run cost:

```
jgrep: 994 records, 33 matched; 994 calls, 0 cached; 292,839 tokens; $0.0123; 4.6s
```

## How well does it work

Three benchmarks on public labeled text, run on 2026-09-18 with Jev 1.13 through OpenRouter.
Each one runs the installed `jgrep` command itself, uncached, at its default threshold of 0.5.
Reproduce them with `bench/accuracy.py`.

**Against a keyword grep.** The UCI SMS Spam Collection: 5,574 text messages, 747 of them spam.

| Filter | Precision | Recall | F1 | Time | Cost |
|---|---:|---:|---:|---:|---:|
| `jgrep "an unsolicited spam, scam or marketing text message"` | 0.87 | 0.95 | **0.91** | 27 s | $0.07 |
| the same with `-p 0.9` | 0.98 | 0.84 | 0.90 | | |
| `grep -iE "free\|win\|prize\|claim\|urgent\|cash\|txt\|call now\|..."` (17 terms) | 0.64 | 0.81 | 0.72 | 0.03 s | free |

The regular expression was written before looking at any results and is in the script.

**Against asking a chat model.** The do-it-yourself alternative is a loop that asks an LLM the
same yes/no question about each line. On 300 of those messages, 32 requests in flight, all
through OpenRouter:

| Judge | F1 | Wall time | Cost | Median latency |
|---|---:|---:|---:|---:|
| **jgrep (Jev 1.13)** | 0.90 | **2.7 s** | $0.0039 | about 210 ms |
| GPT Luna | 0.88 | 9.3 s | $0.0060 | 802 ms |
| GPT Terra | 0.92 | 10.8 s | $0.0571 | 988 ms |
| Qwen 3.7 Flash, thinking off | 0.78 | 8.4 s | $0.0007 | 789 ms |

jgrep finished three to four times sooner than any of them. Its accuracy sits between the two
GPT tiers; with 45 spam messages in the sample, those three F1 scores are within noise of each
other. It is not the cheapest per line: a small open model costs a sixth as much and is
clearly less accurate. Against the model that matched its accuracy, jgrep cost a fifteenth
as much.

**Several descriptions at once.** AG News test set, 7,600 articles, four descriptions
(`-e "news about sports" -e "news about business, markets or the economy" ...`) judged in one
call per article: 37 seconds and $0.13 for all four. Taking the most probable description as the
label gives 86.6% accuracy with no training. One-vs-rest F1 at 0.5 was 0.97 for sports, 0.82 for
science and technology, 0.82 for world affairs and 0.72 for business, which over-triggers
(precision 0.58) because so much technology news is also business news.

**Does the wording of a description matter?** `bench/phrasing.py` scores 30 hand-labeled lines
against five descriptions of different grammatical shapes, including a negation and a question.
Jev got all 150 right under each of four ways of wording the question; that set is easy on
purpose. Asking five descriptions in one call changed no decision and moved probabilities by
0.001 on average. Latency was flat at about 210 ms from 1 to 64 questions per call.

On borderline lines the probabilities land in between, which is what `-p` is for:

```
0.65  [a complaint about noise]  The music from the church on Sunday mornings is lovely but it does start early.
0.46  [does not mention a landlord]  The owner of the building never answers the phone.
```

**On a local server.** On 2026-09-22 the two [local servers](#local-servers-experimental), on an
Apple M3 Ultra, answered the same questions as Jev 1.13 on 2,000 of the SMS messages, 2,000 of the
AG News articles and 60 records padded with neutral text to 7,000 characters. The
[local-model comparison](https://github.com/keltokhy/jgrep/blob/main/docs/benchmarks/local-models-2026-09-22.md)
has the full results, including code review.

| | Jev 1.13 (OpenRouter) | DiffusionGemma (`openjev-0.1`, local) | Laya (`laya-421m`, local) |
|---|---:|---:|---:|
| SMS spam: precision / recall at 0.5 | 0.86 / 0.96 | 0.80 / 0.94 | 0.53 / 0.99 |
| SMS spam: F1 at 0.5 | **0.91** | 0.87 | 0.69 |
| AG News: top-1 accuracy | 86.75% | 87.35% | **92.45%** |
| 7,000-character records: correct of 300 decisions | 300 | 298 | refused |
| Wall time on the M3 Ultra, SMS / news | | 596 s / 693 s | 46 s / 157 s |

DiffusionGemma is a reasonable substitute for Jev on tasks like these when the text must stay on
your machine or a large run should cost nothing: its scores are close to Jev's on all three tests.
It is the slowest of the three; 2,000 news articles took 693 s. Laya is good for sorting short
texts into a few broad topics, where it was the most accurate. It reads at most 512 tokens,
question included, so jgrep reported an error for every 7,000-character record. Jev remains the
default.

Things to know:

- These are a model's judgments. Check a sample before you rely on a filter.
- Jev answers the description you wrote, not the one you meant. TypeSafe
  [documents](https://docs.typesafe.ai/model-jaggedness/jev-1.13) weak spots: counting,
  comparing numbers or dates, double negatives, and long inputs full of irrelevant detail.
- A line is judged alone unless you pass `-C N`, which shows Jev the N lines either side of
  it. The decision is still per line, and the neighbours are not printed. Context does not
  reach across files, and on `tail -f` a line cannot be judged until the N after it arrive.
- Jev is close to deterministic, not exactly so. Asking 150 questions three times without the
  cache gave identical probabilities for 128; the rest moved by up to 0.03 and no decision
  flipped. The cache makes reruns exact.
- The default model ID is an alias for the latest Jev. For results that must reproduce, pin
  one with `--model` (for example `typesafe/jev-1.13` on OpenRouter).
- Text in the input can try to steer the answer. Do not use jgrep as a security boundary.

## Development

```bash
uv sync && uv run pytest        # offline tests using a fake API and local HTTP server; no key
uv run python bench/phrasing.py # live; costs about a cent
uv run python bench/accuracy.py prepare && uv run python bench/accuracy.py spam   # also: news, llm
```

`src/jgrep/inputs.py` handles file discovery, structured records, and document passages.
`src/jgrep/core.py` names the providers jgrep offers. The client behind them (API backends,
retries inside a time budget, the cache, in-flight deduplication and the cost meter) is the shared
runtime described below, which [jlink](https://github.com/keltokhy/jlink) also uses to link
records across datasets with the same model.

MIT license.

## Shared JevKit development

This tool uses [`jevkit-runtime`](https://github.com/keltokhy/jevkit-core), imported
as `jevkit_runtime`. Clone that repository beside this one as `../jevkit-core`, then
run `uv sync`. Core Python edits apply on the next invocation of this tool;
restart long-lived Python processes after editing.

The distribution name is `jevkit-runtime` because `jevkit-core` on PyPI belongs
to a different project. The runtime is [available on PyPI](https://pypi.org/project/jevkit-runtime/).
Use the sibling checkout for shared development, or `uv sync --no-sources` for a
standalone source checkout. Existing published versions of this tool are
unaffected by this source migration.

From the core checkout, `python scripts/dev.py setup`, `check`, and `wheel-check`
set up and validate all five consumers in separate environments.
CI checks out core tag `v0.3.0`. Prompts, question construction, and budget policies
remain in this repository; answer identity, the answer store, transport, and metering
are the runtime's. Runtime 0.2 keys and stores answers differently from 0.1, so a cache
written by an earlier version is re-asked once after upgrading.
