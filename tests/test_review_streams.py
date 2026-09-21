"""Real Git streams retain every text hunk and its physical input location."""

import json

import pytest

from jgrep.code_inputs import diff_records
from test_cli import env, jgrep, write
from test_function_context import commit, git
from test_function_context import repo as repo


@pytest.mark.parametrize("kind", ["rename", "mode", "empty_added", "empty_deleted"])
@pytest.mark.parametrize("format", ["log", "mail", "mail_custom_signature"])
def test_hunkless_last_file_does_not_discard_text_hunks(tmp_path, repo, kind, format):
    source = "int first(void) { return 1; }\n" + "\n" * 12 + "int last(void) { return 2; }\n"
    base = commit(repo, "start", {"a.c": source, "z_old.h": "header\n", "z_empty.h": ""})
    if kind == "rename":
        git(repo, "mv", "z_old.h", "z_new.h")
    elif kind == "mode":
        (repo / "z_old.h").chmod(0o755)
    elif kind == "empty_added":
        (repo / "z_new.h").touch()
    else:
        (repo / "z_empty.h").unlink()
    changed = commit(repo, "edit and metadata", {"a.c": source.replace("return", "return alpha +")})
    later = commit(repo, "later edit", {"b.c": "int alpha(void) { return 3; }\n"})
    if format == "log":
        stream = git(repo, "log", "--reverse", "-p", base + "..HEAD")
    else:
        options = ["--signature=- signed\n+ continuation\n last line"] if format.endswith("custom_signature") else []
        stream = git(repo, "format-patch", "--stdout", *options, base + "..HEAD")
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "stream.patch", stream), "--diff", "--json"])
    found = [json.loads(line) for line in out.splitlines()]
    assert [(r["unit"]["commit"], r["unit"]["new_file"]) for r in found] == [
        (changed, "a.c"), (changed, "a.c"), (later, "b.c")]
    assert code == 2 and len(fake.bodies) == 3 and "metadata-only change" in err
    assert "invalid unified diff" not in err
    assert all(stream.split("\n")[r["line"] - 1].startswith("@@ ") for r in found)


@pytest.mark.parametrize("quote_path", ["true", "false"])
@pytest.mark.parametrize("format", ["log", "mail"])
def test_quoted_utf8_paths_decode_with_octal_and_raw_bytes(tmp_path, repo, quote_path, format):
    name = "café\t.c"
    base = commit(repo, "start", {name: "int old(void) { return 0; }\n"})
    first = commit(repo, "first", {"a.c": "int alpha(void) { return 1; }\n"})
    second = commit(repo, "unicode", {name: "int alpha(void) { return 2; }\n"})
    third = commit(repo, "last", {"z.c": "int alpha(void) { return 3; }\n"})
    args = ["log", "--reverse", "-p"] if format == "log" else ["format-patch", "--stdout"]
    stream = git(repo, "-c", "core.quotePath=" + quote_path, *args, base + "..HEAD")
    assert ('caf\\303\\251\\t.c' if quote_path == "true" else 'café\\t.c') in stream
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "unicode.patch", stream), "--diff", "--json"])
    found = [json.loads(line) for line in out.splitlines()]
    assert code == 0 and not err and len(fake.bodies) == 3
    assert [(r["unit"]["commit"], r["unit"]["new_file"]) for r in found] == [
        (first, "a.c"), (second, name), (third, "z.c")]
    assert found[1]["unit"]["old_file"] == name
    assert all(stream.split("\n")[r["line"] - 1].startswith("@@ ") for r in found)


def test_annotated_tag_preamble_is_not_a_plain_patch(tmp_path, repo):
    commit(repo, "start", {"a.c": "int old(void) { return 0; }\n"})
    changed = commit(repo, "change", {"a.c": "int alpha(void) { return 1; }\n"})
    git(repo, "tag", "-a", "release", "-m", "Release\n\n- tag message, not a patch")
    stream = git(repo, "show", "release")
    assert stream.startswith("tag release\nTagger:")
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "tag.patch", stream), "--diff", "--json"])
    found = json.loads(out)
    assert code == 0 and not err and len(fake.bodies) == 1
    assert found["unit"]["commit"] == changed
    assert stream.split("\n")[found["line"] - 1].startswith("@@ ")


@pytest.mark.parametrize("signature", [True, False])
def test_commit_tail_keeps_blank_context_and_removed_signature_text(repo, signature):
    base = commit(repo, "start", {"a.txt": "- \nold\n\n"})
    commit(repo, "change", {"a.txt": "alpha\n\n"})
    options = [] if signature else ["--no-signature"]
    stream = git(repo, "format-patch", "--stdout", *options, base + "..HEAD")
    rec, = diff_records(stream, "mail.patch")
    assert "\n-- \n-old\n+alpha\n \n" in rec.text
    assert stream.split("\n")[rec.lineno - 1].startswith("@@ ")
