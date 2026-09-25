# Changelog

## 0.6.0

On `jevkit-runtime` 0.4 ([jevkit-core#14](https://github.com/keltokhy/jevkit-core/issues/14)).
Breaking for scripts that set a budget.

- `--budget` now sends no request that would take spending past the limit: each request sets aside
  its estimated price first, so requests in flight together cannot overshoot, and answers already
  paid for are still printed when the budget stops the run. `--budget none` is no limit and
  `--budget 0` answers only from the cache (it used to mean no limit). `JEV_BUDGET` replaces
  `JGREP_BUDGET` and applies to every JevKit tool.
- The model is pinned to `jev-1.13.0` (`typesafe/jev-1.13` on OpenRouter) instead of the
  `jev-latest` alias; `--model jev-latest` still asks for it. jgrep warns when one requested model
  was answered by more than one.
- `--record FILE` writes the run's record: backends, the models that answered, each question as
  asked, calls, tokens, cost and the budget.
- A malformed cached answer is asked again and replaced, rather than reported as an error.
- `--estimate` is the runtime's plan for each record over a read-only cache.
- Reading, judging and ordered output run on the runtime's streaming engine; a bad key still stops
  the run at once.
- The cache moves to `~/.cache/jev/answers.v3.sqlite`; the first run after upgrading re-asks.

## 0.5.0

- Add `--api gliner`, a local [GLiNER2.5-Decide](https://huggingface.co/fastino/GLiNER2.5-Decide)
  server (`JEV_GLINER_URL`, port 8082). Requires `jevkit-runtime>=0.3.2`.

## 0.4.1

- The tool itself is unchanged from 0.4.0; this release brings its README on PyPI up to date.
- Compare DiffusionGemma and Laya with Jev 1.13 on jgrep's benchmarks, in
  `docs/benchmarks/local-models-2026-09-22.md` and its JSON, summarized in the README.
- Add `bench/backend_eval.py`, which runs and scores a local server, and `bench/context_eval.py`,
  the length and evidence-position test. `bench/code_review.py` now requires `--api` and `--model`.

## 0.4.0

- Add `--api diffusiongemma` and `--api laya` for System One servers running on your own machine,
  through `jevkit-runtime` 0.3: chosen only by name, no key needed, and metered at zero API fees
  unless `JEV_PRICE_PER_MTOK` is set. The runtime's `docs/` explain how to run the servers.

## 0.3.0

- Move transport, configuration, the answer cache and metering to the shared `jevkit-runtime` 0.2.
  Answers are keyed by provider, endpoint and model and stored with who answered them; the cache
  written by earlier versions is reset on first use and re-asked. A 200 response without answers is
  an error rather than a retry, and a stored answer that fails validation is an error rather than a hit.
- Label context without a commit id as the function in the working tree, including in the judge's
  question. Only the hunk's new-side lines at their given position are checked; surrounding code
  may have changed. Plain diff and fallback requests retain their existing questions and cache keys.
- Report macro-headed C bodies that parse as a call followed by a bare block, and count changes
  inside them as `syntax_error` under `-W`. Skipped C diagnostics give the whole covered line range
  instead of a function name that may belong to a preceding prototype.
- Preserve text hunks in commit streams ending in a rename, mode-only change or empty-file change;
  trim trailing separators and mail signatures without shifting input locations. Skip annotated-tag
  preambles, and decode quoted paths containing raw UTF-8 as well as octal byte escapes.
- Add C to `--functions` through Tree-sitter in the `[code]` extra: `--lang c`, inferred from `.c`
  and `.h`. Functions the parser read without error are emitted with adjacent comments and exact
  spans. Functions and regions it cannot read, usually around a macro or `#if`, are skipped and
  reported by line range in one error per file rather than failing the file or guessing at boundaries.
- Read `git log -p`, `git show` and `git format-patch` streams with `--diff`, one commit at a time.
  These were previously rejected as content outside a hunk. Hunks carry the commit id as
  `unit.commit` (null for a plain patch), export IDs include it, and a commit whose patch cannot be
  read no longer hides later commits.
- Add `-W`/`--function-context` and `--repo` for `--diff`: judge each hunk with the Python, Go or C
  function that encloses it, read at the hunk's commit or from the working tree, while printing the
  hunk alone. Hunks that cannot be given a function are judged alone and counted by reason in
  stderr, `unit.context`, and `--estimate --json`. Oversized functions are shortened around the
  change and reported; oversized hunks still fail. `--estimate` prices the context and
  `--emit-records` shows it.
- Count patch lines at line feeds only. A form feed or lone carriage return inside a source line
  previously made a valid patch fail.
- Do not print Python `SyntaxWarning`s raised by the source files being read.
- Confine `-W` to the repository. The allowed root is found without trusting repository config, so
  a repository that points `core.worktree` or `GIT_WORK_TREE` outside itself stops the run instead
  of reading outside files; working-tree paths are resolved with their symlinks and refused when
  they leave the repository; and Git runs with a fixed argument list and an environment that blocks
  promisor fetches, object replacement, and prompts. Treat `--repo` as a repository you trust.
- Bound the source `-W` reads: a new-side file larger than 10 MiB is a counted `source_too_large`
  fallback, read neither from a commit nor the working tree, and a named pipe no longer blocks.
- Trim `git format-patch` mails: read the patch after the last `---` separator and stop at the `-- `
  signature, so a message containing `diff --git` or a dash-led signature no longer voids the patch.
- Recognize abbreviated commit ids down to four hex digits (`git log --abbrev-commit`).
- Keep reading a stream after a commit with a malformed quoted path, and report skipped C before a
  file's functions so a match limit or quiet mode cannot end the run with it unreported.
- Read a Python source file that begins with a byte-order mark instead of reporting a syntax error.
- Leave two pre-existing diff-reader issues outside this change: CRLF-converted patches can retain
  `\r` in `new_file` and add spurious metadata-only errors; quoted paths with spaces can also add
  a spurious metadata-only error.

## 0.2.1

- Scope answer caches and offline estimates to the provider and endpoint as well as model and input.
  Existing entries without that identity are not reused.
- Bound ordered work by `-j`, including completed results waiting behind a slow record, while
  allowing fatal errors and budget limits to stop promptly.
- Preserve complete Python functions containing Unicode separators or form feeds, and separate
  Go functions that share a line using parser columns and exact character offsets.

## 0.2.0

- Add `--diff` for complete unified hunks, including deletions and unchanged context, with both
  source ranges and patch locations. Reject malformed, binary, combined, and metadata-only diffs.
- Add `--functions` for Python functions/methods and optional Tree-sitter Go support via `[code]`.
  Preserve comments, decorators, exact source spans and nested bodies. Never truncate code units.
- Add offline `--estimate` with read-only cache inspection, approximate costs, and explicit assumptions.
- Add offline `--emit-records` JSONL for composition with jselect and other record consumers.
- Include source/provenance tests and a frozen 20-case synthetic benchmark with negative results.

## 0.1.1

- Include the gateway backend for System One-compatible endpoints, with separate URL and key settings.
- Add JSONL and CSV input with `--field` selection, complete matching records, and structured
  JSON output. Support nested JSON paths, quoted multiline CSV, and physical source line numbers.
- Add recursive directory search, include globs, gitignore-style exclusions, nested ignore files,
  and `-l` for matching filenames. Skip symlinks, Git metadata, and detected binary files during
  recursive searches.
- Add `--chunks` and `--overlap` for searching long text files with passage and character locations.
  Warn when ordinary records or their context exceed `--max-chars`.
- Include the `-C` context feature: judge each record with its neighbors while printing the
  matching record only.
- Report read failures and always signal the end of input; continue to later files when possible.
- Give repeated file arguments independent counts and context boundaries.
- Reject invalid numeric limits, including non-finite thresholds, budgets, and timeouts.
- Report malformed API answers without losing later matches; validate answers before caching.
- Cancel outstanding requests when quiet mode, a match limit, or the budget stops a run, including
  after input has reached EOF. Enforce a total request deadline across retries and response reads.
- Apply `-m` separately to each input file; `-m 0` reads no input and makes no API calls.
- Accept options interspersed with file arguments.

## 0.1.0

- Initial release: semantic line, paragraph, and file filtering through TypeSafe or OpenRouter,
  with concurrent requests, cached decisions, budgets, and grep-style output.
