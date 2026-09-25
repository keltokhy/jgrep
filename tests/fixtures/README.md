# Request reference

`requests.json` holds 21 request scenarios: each one's decoded bodies, SHA-256 hashes of the exact
HTTP request bytes, and runtime-0.4 answer keys, with the three estimate scenarios' record and call
counts and bytes plus request overhead. Requests used `httpx.MockTransport`, the fake key `test-key`,
an isolated configuration and cache, explicit `--api openrouter`, `--no-cache` and `-j 1`. No
provider was contacted, and the fixtures contain only synthetic source text.

The file was regenerated for jevkit-runtime 0.4 against the previous reference, captured from
`origin/main` at `fdceb6bdf79165a133667b8e57f3b7244545f0a2`, with a guard that failed unless every
body was identical apart from `model`, which moved from the `~typesafe/jev-latest` alias to the
pinned `typesafe/jev-1.13`, record and call counts were unchanged, and estimated bytes moved only by
the model string's length per call. The answer keys changed as runtime 0.4 intends (v3).

`test_request_compatibility.capture` builds each scenario; a change to what jgrep sends shows up
here as a failed comparison.
