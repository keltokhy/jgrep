"""Frozen fake-transport requests and their runtime-0.4 answer keys. See fixtures/README.md."""

import hashlib
import json
from pathlib import Path

import pytest
from jevkit_runtime import Backend, answer_key, from_body

from jgrep.core import PROVIDERS
from test_cli import Fake, env, jgrep, write


REFERENCE = Path(__file__).with_name("fixtures") / "requests.json"
PATCH = "--- a/alpha.py\n+++ b/alpha.py\n@@ -1 +1 @@\n-old\n+alpha\n"
CASES = ["line", "context", "multi", "all", "para", "whole", "chunks", "jsonl", "csv",
         "python", "go", "recursive", "diff", "diff_json", "diff_multi", "diff_all",
         "export_python", "export_diff", "estimate_line", "estimate_diff", "estimate_python"]


def arguments(tmp_path, name):
    text = write(tmp_path, "lines.txt", "alpha é\nbeta\n\nother\n")
    python = write(tmp_path, "alpha.py", "# alpha é\ndef first():\n    return 1\n\ndef second():\n    return 2\n")
    go = write(tmp_path, "alpha.go", "package main\n// alpha é\nfunc First() int { return 1 }\n")
    patch = write(tmp_path, "change.patch", PATCH)
    jsonl = write(tmp_path, "records.jsonl", '{"text":"alpha é","id":1}\n{"text":"beta","id":2}\n')
    csv = write(tmp_path, "records.csv", 'id,text\n1,alpha é\n2,beta\n')
    return {
        "line": ["alpha", text], "context": ["alpha", text, "-C", "1"],
        "multi": ["-e", "alpha", "-e", "beta", text],
        "all": ["-e", "alpha", "-e", "beta", "--all", text],
        "para": ["alpha", text, "--para"], "whole": ["alpha", text, "--whole"],
        "chunks": ["alpha", text, "--chunks", "12", "--overlap", "2"],
        "jsonl": ["alpha", jsonl, "--jsonl", "--field", "text"],
        "csv": ["alpha", csv, "--csv", "--field", "text"],
        "python": ["alpha", python, "--functions"], "go": ["alpha", go, "--functions"],
        "recursive": ["alpha", str(tmp_path), "--functions", "-r", "--glob", "*.py"],
        "diff": ["alpha", patch, "--diff"], "diff_json": ["alpha", patch, "--diff", "--json"],
        "diff_multi": ["-e", "alpha", "-e", "beta", patch, "--diff"],
        "diff_all": ["-e", "alpha", "-e", "beta", patch, "--diff", "--all"],
        "export_python": [python, "--functions", "--emit-records"],
        "export_diff": [patch, "--diff", "--emit-records"],
        "estimate_line": ["alpha", text, "--estimate", "--json"],
        "estimate_diff": ["alpha", patch, "--diff", "--estimate", "--json"],
        "estimate_python": ["alpha", python, "--functions", "--estimate", "--json"],
    }[name]


class Captured(Fake):
    def __init__(self):
        super().__init__()
        self.wire = []

    async def __call__(self, request):
        self.wire.append(hashlib.sha256(request.content).hexdigest())
        return await super().__call__(request)


def capture(tmp_path, name):
    fake = Captured()
    code, out, err, _ = jgrep([*arguments(tmp_path, name), "--api", "openrouter", "--no-cache", "-j", "1"], fake=fake)
    assert code in (0, 1) and not err
    provider = PROVIDERS["openrouter"]
    keys = [[answer_key(Backend(provider.name, provider.url, body["model"]), body["state"], from_body(q))
             for q in body["questions"].values()] for body in fake.bodies]
    result = {"bodies": fake.bodies, "wire_sha256": fake.wire, "cache_keys": keys}
    if name.startswith("estimate_"):
        report = json.loads(out)
        result["estimate"] = {k: report[k] for k in ("records", "estimated_calls", "input_bytes_plus_overhead")}
    return result


@pytest.mark.parametrize("name", CASES)
def test_requests_and_answer_keys_match_reference(tmp_path, name):
    if name == "go":
        pytest.importorskip("tree_sitter_go")
    reference = json.loads(REFERENCE.read_text())
    assert capture(tmp_path, name) == reference["cases"][name]


def test_fallback_uses_main_request_and_reuses_plain_diff_cache(tmp_path):
    expected = json.loads(REFERENCE.read_text())["cases"]["diff"]
    # No repository at this path: source_unavailable must retain the original plain-diff request.
    patch = write(tmp_path, "change.patch", PATCH)
    common = ["alpha", patch, "--diff", "--api", "openrouter"]
    fake = Captured()
    code, _, err, _ = jgrep([*common, "-W", "--repo", str(tmp_path)], fake=fake)
    assert code == 0 and "1 source_unavailable" in err
    assert fake.bodies == expected["bodies"] and fake.wire == expected["wire_sha256"]
    code, _, err, cached = jgrep(common)
    assert code == 0 and not err and not cached.bodies
    report = json.loads(jgrep([*common, "-W", "--repo", str(tmp_path), "--estimate", "--json"])[1])
    assert report["cached_records"] == 1 and report["estimated_calls"] == 0
