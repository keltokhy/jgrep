# Changelog

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
