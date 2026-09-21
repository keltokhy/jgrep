# Request compatibility reference

`main_requests.json` captures 21 scenarios from local `origin/main` at
`fdceb6bdf79165a133667b8e57f3b7244545f0a2`. Requests used `httpx.MockTransport`,
the fake key `test-key`, an isolated configuration/cache, explicit `--api openrouter`,
`--no-cache`, and `-j 1`. No provider was contacted.

`test_request_compatibility.capture` builds each scenario and records the fake transport's
decoded bodies, SHA-256 hashes of the exact HTTP request bytes, and endpoint-scoped cache keys.
The three estimate scenarios also retain record/call counts and bytes plus request overhead.
Export scenarios must send no requests. The fixtures contain only synthetic source text.

To audit the reference, extract that commit's `src/` with local `git archive` into a temporary
directory, place its `src/` before the current `tests/` on `sys.path` in a fresh Python process,
and call `capture(temp_directory, name)` for each entry in `CASES`. This uses the same fake
transport helper against the reference package, without switching the checkout or fetching Git
objects. Compare the results to this file; do not regenerate it from the implementation under test.
