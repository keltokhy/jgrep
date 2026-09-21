# Changelog

## Unreleased

- Add C to `--functions` through Tree-sitter in the `[code]` extra: `--lang c`, inferred from `.c`
  and `.h`. Functions the parser read without error are emitted with adjacent comments and exact
  spans. Functions and regions it cannot read, usually around a macro or `#if`, are skipped and
  named in one error per file rather than failing the file or guessing at boundaries.
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
