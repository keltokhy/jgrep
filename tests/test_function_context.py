"""--diff -W: one decision per hunk, judged with the function around it, and never silently without."""

import json
import os
import subprocess
import sys

import pytest

from jgrep.diff_context import FALLBACKS
from test_cli import env, jgrep, write

PYTHON = '''LIMIT = 10

def first(items):
    total = sum(items)
    return total
# second copies at most LIMIT bytes
def second(data, size):
    if size > LIMIT:
        raise ValueError("too large")
    buffer = bytearray(LIMIT)
    buffer[:size] = data[:size]
    checked = True
    copied = len(data)
    return buffer, checked, copied
'''
PYTHON_CHECK = '    if size > LIMIT:\n        raise ValueError("too large")\n'

GO = '''package main

func first(items []int) int {
	total := len(items)
	return total
}
// second copies at most limit bytes
func second(data []byte, size int) ([]byte, error) {
	if size > limit {
		return nil, errTooLarge
	}
	buffer := make([]byte, limit)
	copy(buffer, data[:size])
	checked := true
	_ = checked
	return buffer, nil
}
'''
GO_CHECK = "\tif size > limit {\n\t\treturn nil, errTooLarge\n\t}\n"

C = '''#include <string.h>

static int first(int n)
{
  return n + 1;
}
/* second copies at most LIMIT bytes */
static int second(char *out, const char *data, size_t size)
{
  if(size > LIMIT)
    return -1;
  memcpy(out, data, size);
  out[size] = 0;
  log_copy(size);
  count_copy();
  return 0;
}
'''
C_CHECK = "  if(size > LIMIT)\n    return -1;\n"


def git(repo, *args):
    # The developer's own Git configuration (signing, colour, hooks) must not reach these repositories.
    isolated = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, env=isolated)
    return done.stdout.decode("utf-8")


def commit(repo, message, files):
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, newline="")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    return path


def rows(out):
    return [json.loads(line) for line in out.splitlines()]


@pytest.mark.parametrize("name,source,check,marker,lines", [
    ("copy.py", PYTHON, PYTHON_CHECK, "# second", "6-12"),
    ("copy.go", GO, GO_CHECK, "// second", "7-14"),
    ("copy.c", C, C_CHECK, "/* second", "7-15"),
])
def test_hunk_is_judged_with_its_enclosing_function_and_printed_alone(tmp_path, repo, name, source, check, marker, lines):
    if not name.endswith(".py"):
        pytest.importorskip("tree_sitter_" + name.rsplit(".", 1)[1])
    commit(repo, "add copy", {name: source})
    after = source.replace(check, "")
    (repo / name).write_text(after)
    patch = write(tmp_path, "change.diff", git(repo, "diff"))
    function = after[after.index(marker):]

    code, out, err, fake = jgrep(["removes the alpha check", patch, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0"])
    row, = rows(out)
    assert code == 0 and not err
    # The removal's unchanged lines reach into `first`, but only `second` encloses the change.
    assert row["unit"]["context"] == {
        "symbols": ["second"], "language": {"py": "python", "go": "go", "c": "c"}[name.rsplit(".", 1)[1]],
        "line": int(lines.split("-")[0]), "end_line": int(lines.split("-")[1]),
        "shown_line": int(lines.split("-")[0]), "shown_end_line": int(lines.split("-")[1]), "truncated": False}
    assert row["unit"]["commit"] is None
    # Only the hunk is printed; the function reaches the judge after it, labelled as context.
    assert row["text"].startswith(f"--- a/{name}\n+++ b/{name}\n@@ ") and marker not in row["text"].split("@@")[2][:1]
    assert fake.bodies[0]["state"] == (
        row["text"] + f"\nEnclosing function after this change ({name} lines {lines}), shown only as context:\n" + function)
    assert "function that encloses the change" in fake.bodies[0]["questions"]["d0"]["instructions"]

    # The offline export shows the same context the judge would see.
    code, out, err, _ = jgrep(["--diff", "-W", "--repo", str(repo), "--emit-records", patch])
    exported, = rows(out)
    assert code == 0 and not err
    assert exported["text"] == row["text"] and exported["text"] + "\n" + exported["context"] == fake.bodies[0]["state"]
    assert exported["unit"]["context"]["symbols"] == ["second"]


def test_git_log_stream_reads_each_file_at_the_commit_that_changed_it(tmp_path, repo, monkeypatch):
    first = commit(repo, "add copy\n\n- a bullet in the message\n+ and a plus", {"copy.py": PYTHON})
    version_two = PYTHON.replace("checked = True", "checked = False  # second version")
    second = commit(repo, "change flag", {"copy.py": version_two})
    third = commit(repo, "remove check", {"copy.py": version_two.replace(PYTHON_CHECK, "")})
    (repo / "copy.py").write_text(version_two.replace("copied = len(data)", "copied = 0  # uncommitted"))
    log = write(tmp_path, "history.patch", git(repo, "log", "-p"))

    code, out, err, fake = jgrep(["alpha", log, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0", "--stats"])
    newest, middle, oldest = rows(out)
    assert code == 0  # -p 0 prints every hunk
    assert [r["unit"]["commit"] for r in (newest, middle, oldest)] == [third, second, first]
    patch_lines = (tmp_path / "history.patch").read_text().split("\n")
    assert all(patch_lines[r["line"] - 1].startswith("@@ ") for r in (newest, middle, oldest))

    states = [body["state"].split("shown only as context:\n") for body in fake.bodies]
    # Each context is the function as of that commit, never the edited working tree.
    assert "second version" in states[0][1] and "if size > LIMIT" not in states[0][1]
    assert "second version" in states[1][1] and "if size > LIMIT" in states[1][1]
    assert not any("uncommitted" in body["state"] for body in fake.bodies)
    assert newest["unit"]["context"]["symbols"] == middle["unit"]["context"]["symbols"] == ["second"]
    # The commit that created the file already shows the whole function in its hunk.
    assert oldest["unit"]["context"] == {"fallback": "function_in_hunk"} and len(states[2]) == 1
    assert "encloses" not in fake.bodies[2]["questions"]["d0"]["instructions"]
    assert "function context for 2 of 3 hunks; 1 judged alone (1 function_in_hunk)" in err
    assert "3 records (2 with function context), 3 matched" in err

    # Without --repo the current directory's repository is used; format-patch streams carry ids too.
    monkeypatch.chdir(repo)
    mbox = write(tmp_path, "series.patch", git(repo, "format-patch", "--stdout", "-2"))
    code, out, err, _ = jgrep(["alpha", mbox, "--diff", "-W", "--json", "-p", "0"])
    assert [r["unit"]["commit"] for r in rows(out)] == [second, third] and not err
    assert all(r["unit"]["context"]["symbols"] == ["second"] for r in rows(out))


def test_every_fallback_is_counted_and_the_hunk_is_still_judged(tmp_path, repo):
    commit(repo, "start", {
        "gone.py": "def gone():\n    return 1\n",
        "notes.txt": "alpha\nbeta\n",
        "settings.py": "LIMIT = 10\nNAME = 'alpha'\n\ndef use():\n    return LIMIT\n",
        "broken.py": PYTHON,
        "copy.py": PYTHON,
    })
    (repo / "gone.py").unlink()
    (repo / "notes.txt").write_text("alpha\ngamma\n")
    (repo / "settings.py").write_text("LIMIT = 20\nNAME = 'alpha'\n\ndef use():\n    return LIMIT\n")
    (repo / "broken.py").write_text(PYTHON.replace("checked = True", "checked = (True"))
    (repo / "copy.py").write_text(PYTHON.replace("checked = True", "checked = False"))
    (repo / "small.py").write_text("def small():\n    return 1\n")
    git(repo, "add", "-N", "small.py")
    patch = write(tmp_path, "many.diff", git(repo, "diff", "--no-renames"))
    expected = {"broken.py": "syntax_error", "copy.py": None, "gone.py": "deleted_file", "notes.txt": "unsupported_language",
                "settings.py": "outside_function", "small.py": "function_in_hunk"}

    code, out, err, fake = jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0"])
    found = {r["unit"]["new_file"] or r["unit"]["old_file"]: r["unit"]["context"].get("fallback") for r in rows(out)}
    assert found == expected and len(fake.bodies) == 6
    # A hunk judged alone is asked exactly what plain --diff asks, so the two share cached answers.
    alone = [body for body in fake.bodies if "Enclosing function" not in body["state"]]
    assert len(alone) == 5 and all("encloses" not in body["questions"]["d0"]["instructions"] for body in alone)
    assert ("function context for 1 of 6 hunks; 5 judged alone (1 deleted_file, 1 function_in_hunk, "
            "1 outside_function, 1 syntax_error, 1 unsupported_language)") in err

    code, out, _, fake = jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo), "--estimate", "--json"])
    report = json.loads(out)
    assert code == 0 and not fake.bodies
    assert report["notes"][-1].startswith("The estimate includes function context for 1 of 6 hunks; 5 judged alone (")
    assert report["function_context"] == {
        "records_with_context": 1, "truncated_contexts": 0, "records_without_context": 5,
        "fallbacks": {"deleted_file": 1, "function_in_hunk": 1, "outside_function": 1, "syntax_error": 1,
                      "unsupported_language": 1}}

    def only(patch_text, *flags):
        path = write(tmp_path, "one.diff", patch_text)
        code, out, err, _ = jgrep(["alpha", path, "--diff", "-W", "--json", "-p", "0", *flags])
        return [r["unit"]["context"].get("fallback") for r in rows(out)], err

    copy_patch = git(repo, "diff", "--", "copy.py")
    # The patch is from this repository, but the file has changed since it was written.
    (repo / "copy.py").write_text(PYTHON.replace("checked = True", "checked = None"))
    assert only(copy_patch, "--repo", str(repo))[0] == ["source_mismatch"]
    # A directory that holds neither the commit nor the file.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert only(copy_patch, "--repo", str(elsewhere))[0] == ["source_unavailable"]
    stream = git(repo, "log", "-p", "--", "copy.py")
    git(elsewhere, "init", "-q")
    assert only(stream, "--repo", str(elsewhere))[0] == ["source_unavailable"]
    # A patch path may not reach outside the repository.
    outside = write(tmp_path, "outside.py", PYTHON.replace(PYTHON_CHECK, ""))
    escape = copy_patch.replace("b/copy.py", "b/../outside.py")
    assert only(escape, "--repo", str(repo))[0] == ["source_unavailable"] and os.path.exists(outside)
    # No new-side lines: nothing places the hunk in the file or confirms the file is the right one.
    (repo / "copy.py").write_text(PYTHON.replace(PYTHON_CHECK, ""))
    fallbacks, err = only(git(repo, "diff", "-U0", "--", "copy.py"), "--repo", str(repo))
    assert fallbacks == ["deletion_only"] and "1 judged alone (1 deletion_only)" in err
    assert set(expected.values()) | {"source_mismatch", "source_unavailable", "deletion_only",
                                     "parser_unavailable", "source_too_large"} == set(FALLBACKS) | {None}


def test_c_context_needs_the_parser_and_an_error_free_function(tmp_path, repo, monkeypatch):
    broken = C.replace("  log_copy(size);\n", "  char *text = va_arg(ap, char *);\n")
    commit(repo, "start", {"copy.c": C, "macro.c": broken})
    (repo / "copy.c").write_text(C.replace(C_CHECK, ""))
    (repo / "macro.c").write_text(broken.replace(C_CHECK, ""))
    patch = write(tmp_path, "c.diff", git(repo, "diff"))
    argv = ["alpha", patch, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0", "--no-cache"]
    try:
        import tree_sitter_c  # noqa: F401
    except ImportError:
        pass
    else:
        code, out, err, _ = jgrep(argv)
        # va_arg(ap, char *) is an error inside `second`, so its boundaries are not trusted as context.
        assert [r["unit"]["context"].get("fallback") for r in rows(out)] == [None, "syntax_error"]
        assert "function context for 1 of 2 hunks; 1 judged alone (1 syntax_error)" in err
    monkeypatch.setitem(sys.modules, "tree_sitter_c", None)
    code, out, err, fake = jgrep(argv)
    assert [r["unit"]["context"]["fallback"] for r in rows(out)] == ["parser_unavailable"] * 2
    assert len(fake.bodies) == 2 and "2 judged alone (2 parser_unavailable)" in err


def test_estimate_prices_the_context_and_matches_the_filter_request(tmp_path, repo):
    commit(repo, "add copy", {"copy.py": PYTHON})
    (repo / "copy.py").write_text(PYTHON.replace(PYTHON_CHECK, ""))
    patch = write(tmp_path, "change.diff", git(repo, "diff"))
    base = ["alpha", patch, "--diff", "--estimate", "--json"]
    alone = json.loads(jgrep(base)[1])
    together = json.loads(jgrep([*base, "-W", "--repo", str(repo)])[1])
    function = PYTHON.replace(PYTHON_CHECK, "")[PYTHON.index("# second"):]
    assert "function_context" not in alone and together["function_context"]["records_with_context"] == 1
    assert together["estimated_input_tokens"] - alone["estimated_input_tokens"] >= len(function) // 4
    assert together["estimated_cost_usd"] > alone["estimated_cost_usd"]
    # The preview keys the cache on the state and question the filter sends: after a run, all is cached.
    assert jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo)])[0] == 1
    after = json.loads(jgrep([*base, "-W", "--repo", str(repo)])[1])
    assert after["cached_records"] == 1 and after["estimated_calls"] == 0
    assert json.loads(jgrep(base)[1])["cached_records"] == 0


def test_oversized_function_is_shortened_around_the_change_but_a_hunk_never_is(tmp_path, repo):
    body = "".join(f"    step_{i} = {i}\n" for i in range(200))
    source = "def long(value):\n" + body + "    return value\n"
    commit(repo, "add long", {"long.py": source})
    (repo / "long.py").write_text(source.replace("step_120 = 120", "step_120 = alpha(120)"))
    patch = write(tmp_path, "long.diff", git(repo, "diff"))
    argv = ["alpha", patch, "--diff", "-W", "--repo", str(repo), "--json", "--max-chars", "600"]

    code, out, err, fake = jgrep(argv)
    context = rows(out)[0]["unit"]["context"]
    header, shown = fake.bodies[0]["state"].split("shown only as context:\n")
    assert code == 0 and context["truncated"] and (context["line"], context["end_line"]) == (1, 202)
    assert context["shown_line"] < 122 < context["shown_end_line"] and "step_120 = alpha(120)" in shown
    assert len(shown) <= 600 and shown.endswith("\n") and "def long" not in shown
    assert f"long.py lines 1-202, showing lines {context['shown_line']}-{context['shown_end_line']} nearest the change" in header
    assert "1 contexts shortened to --max-chars" in err
    assert json.loads(jgrep([*argv, "--estimate"])[1])["function_context"]["truncated_contexts"] == 1

    # The hunk is the judged unit: over the limit it fails with its size, with or without context.
    code, out, err, fake = jgrep([*argv[:-1], "100"])
    assert code == 2 and "never truncated" in err and not out and not fake.bodies


@pytest.mark.parametrize("flags,message", [
    (["-W"], "requires --diff"),
    (["-W", "--functions"], "requires --diff"),
    (["-W", "--diff", "-C", "1"], "cannot be combined"),
    (["-W", "--diff", "--para"], "cannot be combined"),
    (["-W", "--jsonl", "--field", "text"], "requires --diff"),
    (["--diff", "--repo", "."], "--repo requires"),
    (["--diff", "-W", "--repo", "no/such/dir"], "is not a directory"),
])
def test_function_context_option_errors_never_call_provider(flags, message):
    code, out, err, fake = jgrep([*flags, "alpha"])
    assert code == 2 and message in err and not out and not fake.bodies


SECRET = 'def load():\n    path = "config"\n    token = "SECRET-DO-NOT-LEAK-123"\n    return path, token\n'
# A plain patch whose new-side lines an attacker can guess; it never mentions the token line.
GUESS = "--- a/{0}\n+++ b/{0}\n@@ -1,2 +1,2 @@\n def load():\n-    path = None\n+    path = \"config\"\n"


def leaks(tmp_path, repo_dir, patch_text):
    """Run the export and the filter; return (exit code, stderr, every byte that would leave the machine)."""
    patch = write(tmp_path, "guess.diff", patch_text)
    _, out, _, _ = jgrep(["--diff", "-W", "--repo", str(repo_dir), "--emit-records", patch])
    code, _, err, fake = jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo_dir), "--no-cache"])
    return code, err, out + json.dumps(fake.bodies)


def test_core_worktree_cannot_move_the_boundary_outside_the_repository(tmp_path, repo):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.py").write_text(SECRET)
    git(repo, "config", "core.worktree", str(outside))
    code, err, sent = leaks(tmp_path, repo, GUESS.format("secrets.py"))
    assert "SECRET" not in sent
    # Refused loudly, before any hunk is read or judged: Git's work tree is not the directory holding .git.
    assert code == 2 and "core.worktree" in err and str(outside.resolve()) in err and not json.loads("[" + sent.split("[", 1)[1])
    # A parent directory as work tree passes an "is an ancestor" test but is just as wrong.
    git(repo, "config", "core.worktree", str(tmp_path))
    (tmp_path / "secrets.py").write_text(SECRET)
    code, err, sent = leaks(tmp_path, repo, GUESS.format("secrets.py"))
    assert code == 2 and "SECRET" not in sent and "core.worktree" in err


@pytest.mark.parametrize("name", ["link.py", "../outside/secrets.py", "{outside}/secrets.py", "sub/../../outside/secrets.py"])
def test_patch_paths_and_symlinks_cannot_reach_outside_the_repository(tmp_path, repo, name):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.py").write_text(SECRET)
    (repo / "sub").mkdir()
    (repo / "link.py").symlink_to(outside / "secrets.py")
    name = name.format(outside=outside)
    patch = GUESS.format(name) if not name.startswith("/") else GUESS.format("x.py").replace("+++ b/x.py", "+++ " + name)
    code, err, sent = leaks(tmp_path, repo, patch)
    assert "SECRET" not in sent and "1 judged alone (1 source_unavailable)" in err


def test_named_pipe_in_the_repository_does_not_hang_the_reader(tmp_path, repo):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no named pipes on this platform")
    os.mkfifo(repo / "pipe.py")
    code, err, sent = leaks(tmp_path, repo, GUESS.format("pipe.py"))
    assert "1 judged alone (1 source_unavailable)" in err


def test_missing_objects_never_contact_a_promisor_remote(tmp_path, repo):
    commit(repo, "start", {"copy.py": PYTHON})
    marker = tmp_path / "EXECUTED"
    # An untrusted repository can declare a promisor remote whose transport runs a command.
    for key, value in (("extensions.partialClone", "origin"), ("remote.origin.promisor", "true"),
                       ("remote.origin.url", f"ext::sh -c touch% {marker}"), ("protocol.ext.allow", "always")):
        git(repo, "config", key, value)
    diff = "diff --git a/copy.py b/copy.py\nindex 111..222 100644\n" + GUESS.format("copy.py")
    stream = f"commit {'a' * 40}\nAuthor: T <t@example.com>\n\n    Names an object this repository lacks\n\n" + diff
    code, err, sent = leaks(tmp_path, repo, stream)
    # The object is missing, cat-file reports it without a lazy fetch, and the ext transport never runs.
    assert not marker.exists() and "1 judged alone (1 source_unavailable)" in err and "SECRET" not in sent


def test_linked_worktrees_and_subdirectories_are_ordinary_repositories(tmp_path, repo):
    commit(repo, "start", {"pkg/copy.py": PYTHON})
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked))
    assert (linked / ".git").is_file()  # a gitfile, not a directory
    for root, given in ((linked, linked), (repo, repo / "pkg")):
        (root / "pkg" / "copy.py").write_text(PYTHON.replace(PYTHON_CHECK, ""))
        patch = write(tmp_path, "change.diff", git(root, "diff"))
        code, out, err, _ = jgrep(["--diff", "-W", "--repo", str(given), "--emit-records", patch])
        assert code == 0 and not err and rows(out)[0]["unit"]["context"]["symbols"] == ["second"]


def test_oversized_source_is_a_counted_fallback_before_it_is_read_or_parsed(tmp_path, repo, monkeypatch):
    from jgrep import diff_context
    parsed = []
    real = diff_context.FunctionContext._parse
    monkeypatch.setattr(diff_context.FunctionContext, "_parse",
                        lambda self, text, path, lang: (parsed.append(path), real(self, text, path, lang))[1])
    # A ceiling between the two files: one is read and parsed, the larger one never is.
    monkeypatch.setattr(diff_context, "MAX_SOURCE_BYTES", len(PYTHON) + 100)
    big = "# padding line\n" * 50 + PYTHON
    commit(repo, "start", {"copy.py": PYTHON, "big.py": big})
    (repo / "copy.py").write_text(PYTHON.replace(PYTHON_CHECK, ""))
    (repo / "big.py").write_text(big.replace(PYTHON_CHECK, ""))
    patch = write(tmp_path, "tree.diff", git(repo, "diff"))

    # Working tree: size is checked from stat, before the file is opened.
    code, out, err, _ = jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0"])
    found = {r["unit"]["new_file"]: r["unit"]["context"].get("fallback") for r in rows(out)}
    assert found == {"big.py": "source_too_large", "copy.py": None} and "1 source_too_large" in err
    assert "big.py" not in parsed and "source_too_large" in FALLBACKS

    # Commit stream: size comes from the batch header, and the pipe stays aligned for the next blob.
    parsed.clear()
    log = write(tmp_path, "log.patch", git(repo, "log", "-p", "--reverse"))
    code, out, err, _ = jgrep(["--diff", "-W", "--repo", str(repo), "--emit-records", log])
    created = [r for r in rows(out) if r["unit"]["old_file"] is None]  # the commit that added each file
    assert {r["unit"]["new_file"]: r["unit"]["context"].get("fallback") for r in created} == {
        "big.py": "source_too_large", "copy.py": "function_in_hunk"}
    assert "big.py" not in parsed


def test_context_header_and_body_share_the_limit(tmp_path, repo):
    body = "".join(f"    step_{i} = {i}\n" for i in range(200))
    source = "def long(value):\n" + body + "    return value\n"
    commit(repo, "add long", {"long.py": source})
    (repo / "long.py").write_text(source.replace("step_120 = 120", "step_120 = alpha(120)"))
    patch = write(tmp_path, "long.diff", git(repo, "diff"))
    for limit in (400, 600, 1000):
        code, out, err, fake = jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo),
                                      "--json", "--max-chars", str(limit), "--no-cache"])
        row = rows(out)[0]
        state = fake.bodies[0]["state"]
        assert state.startswith(row["text"] + "\n")
        context = state[len(row["text"]) + 1:]  # the enclosing function, after the hunk and the join
        # The whole context, header included, is held to --max-chars, and --estimate prices the same.
        assert len(context) <= limit and "step_120 = alpha(120)" in context
        assert row["unit"]["context"]["truncated"] is True
        report = json.loads(jgrep(["alpha", patch, "--diff", "-W", "--repo", str(repo),
                                   "--estimate", "--json", "--max-chars", str(limit)])[1])
        assert report["function_context"]["truncated_contexts"] == 1
