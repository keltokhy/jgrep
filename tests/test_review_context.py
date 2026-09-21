"""Context provenance and offline reads, including a real local promisor remote."""

import json

import pytest

from jgrep import diff_context
from test_cli import env, jgrep, write
from test_function_context import PYTHON, commit, git
from test_function_context import repo as repo


@pytest.mark.parametrize("committed", [False, True])
def test_context_words_describe_the_source_and_estimate_the_same_request(tmp_path, repo, committed):
    commit(repo, "start", {"copy.py": PYTHON})
    after = PYTHON.replace("checked = True", "checked = alpha")
    commit(repo, "change", {"copy.py": after})
    stream = git(repo, "show", "--format=medium" if committed else "--format=")
    # Outside the hunk: its new-side lines still match, but the function has changed since then.
    (repo / "copy.py").write_text(after.replace("if size > LIMIT:", "if size > LIMIT + 100:"))
    args = ["alpha", write(tmp_path, "change.patch", stream), "--diff", "-W", "--repo", str(repo), "--no-cache"]
    code, out, err, fake = jgrep(args)
    assert code == 0 and not err and len(fake.bodies) == 1
    body = fake.bodies[0]
    words = "as it reads after the change" if committed else "as it reads in the working tree"
    header = "after this change" if committed else words
    assert "Enclosing function " + header in body["state"]
    assert words in body["questions"]["d0"]["instructions"]
    assert ("LIMIT + 100" in body["state"]) is not committed
    code, out, err, fake_estimate = jgrep([*args, "--estimate", "--json"])
    report = json.loads(out)
    assert code == 0 and not err and not fake_estimate.bodies
    assert report["input_bytes_plus_overhead"] == len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 1024


def test_estimate_does_not_fetch_missing_blobs_from_a_file_promisor(tmp_path, repo, monkeypatch):
    commit(repo, "start", {"copy.py": PYTHON})
    commit(repo, "change", {"copy.py": PYTHON.replace("checked = True", "checked = alpha")})
    stream = git(repo, "log", "-1", "-p")
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    clone = tmp_path / "blobless"
    git(tmp_path, "clone", "--quiet", "--no-checkout", "--filter=blob:none", repo.as_uri(), str(clone))
    assert git(clone, "config", "remote.origin.promisor").strip() == "true"

    def missing():
        return sorted(line for line in git(clone, "rev-list", "--objects", "--all", "--missing=print").splitlines()
                      if line.startswith("?"))

    before = missing()
    assert len(before) == 2
    trace = tmp_path / "git.trace"
    monkeypatch.setitem(diff_context.GIT_ENV, "GIT_TRACE", str(trace))
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "missing.patch", stream), "--diff", "-W",
                                  "--repo", str(clone), "--estimate", "--json"])
    assert code == 0 and not err and not fake.bodies
    assert json.loads(out)["function_context"]["fallbacks"] == {"source_unavailable": 1}
    assert missing() == before and "fetch" not in trace.read_text() and "upload-pack" not in trace.read_text()
    # Positive control: the reachable file:// remote really can supply a missing blob.
    assert "checked = alpha" in git(clone, "cat-file", "-p", "HEAD:copy.py")
    assert len(missing()) == 1


def test_python_tail_removal_keeps_the_counted_fallback(tmp_path, repo):
    commit(repo, "start", {"copy.py": PYTHON})
    (repo / "copy.py").write_text(PYTHON.replace("    return buffer, checked, copied\n", ""))
    patch = write(tmp_path, "tail.patch", git(repo, "diff"))
    args = ["alpha", patch, "--diff", "--no-cache", "-p", "0"]
    code, out, err, fake = jgrep([*args, "-W", "--repo", str(repo), "--json"])
    assert code == 0 and json.loads(out)["unit"]["context"] == {"fallback": "outside_function"}
    assert "1 outside_function" in err and fake.bodies == jgrep(args)[3].bodies
