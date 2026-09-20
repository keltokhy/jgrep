"""Contracts for complete changes, syntax boundaries, provenance, and offline composition."""

import json

import pytest

from jgrep.code_inputs import diff_records, function_records
from test_cli import env, jgrep, write


REMOVAL = '''--- a/store.go
+++ b/store.go
@@ -10,5 +10,2 @@ func Save() {
 alpha := SaveConfig()
-if alpha != nil {
-    return alpha
-}
 return nil
'''


def test_deletion_only_diff_judges_whole_change_and_preserves_patch_location(tmp_path):
    path = write(tmp_path, "review.diff", REMOVAL)
    code, out, err, fake = jgrep(["removes alpha handling", path, "--diff", "--json"])
    result = json.loads(out)
    assert code == 0 and not err
    assert fake.bodies[0]["state"] == REMOVAL
    assert "Compare the before and after" in fake.bodies[0]["questions"]["d0"]["instructions"]
    assert result["text"] == REMOVAL and result["file"] == path
    assert (result["line"], result["end_line"]) == (3, 8)
    assert result["unit"] == {"kind": "diff", "hunk": 1, "old_file": "store.go", "new_file": "store.go",
                              "old_start": 10, "old_count": 5, "new_start": 10, "new_count": 2}


@pytest.mark.parametrize("patch,old,new", [
    ('--- /dev/null\n+++ b/new.go\n@@ -0,0 +1 @@\n+alpha\n', None, "new.go"),
    ('--- a/gone.go\n+++ /dev/null\n@@ -1 +0,0 @@\n-alpha\n', "gone.go", None),
    ('--- a/old name.go\n+++ b/new name.go\n@@ -1 +1 @@\n-old\n+alpha\n', "old name.go", "new name.go"),
    ('--- "a/\\303\\251.go"\n+++ "b/\\303\\251.go"\n@@ -1 +1 @@\n-old\n+alpha\n', "é.go", "é.go"),
])
def test_creation_deletion_rename_and_quoted_unicode_paths(patch, old, new):
    rec, = diff_records(patch, "patch")
    assert rec.unit["old_file"] == old and rec.unit["new_file"] == new
    assert rec.text == patch


def test_each_hunk_has_its_own_file_headers_and_eof_markers():
    patch = ('--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'
             '@@ -9 +9 @@\n-x\n\\ No newline at end of file\n+y\n\\ No newline at end of file\n'
             '--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+z\n')
    rows = list(diff_records(patch, "patch"))
    assert len(rows) == 3
    assert [r.lineno for r in rows] == [3, 6, 13]
    assert rows[1].text.count("No newline") == 2
    assert rows[1].unit["hunk"] == 2
    assert rows[2].unit["new_file"] == "b.py"


@pytest.mark.parametrize("bad", [
    "ordinary source code", "@@@ -1,2 -1,2 +1,2 @@@\n-removed\n",
    "--- a/x\n+++ b/x\n@@ malformed @@\n-x\n+y\n",
    "--- a/x\n+++ b/x\n@@ -1,4 +1,3 @@\n-x\n+y\n",
    "+++ b/x\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-x\n+y\n+extra ignored change\n",
    "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-x\n+y\n--- a/dangling\n",
])
def test_bad_diff_is_an_error_without_model_calls(tmp_path, bad):
    path = write(tmp_path, "bad.diff", bad)
    code, out, err, fake = jgrep(["alpha", path, "--diff"])
    assert code == 2 and not out and err and not fake.bodies


def test_binary_and_metadata_only_diffs_are_not_silently_clean(tmp_path):
    path = write(tmp_path, "binary.diff", "diff --git a/pic b/pic\nBinary files a/pic and b/pic differ\n")
    assert jgrep(["alpha", path, "--diff"])[0] == 2
    path = write(tmp_path, "mode.diff", "diff --git a/x b/x\nold mode 100644\nnew mode 100755\n")
    assert jgrep(["alpha", path, "--diff"])[0] == 2


@pytest.mark.parametrize("flags,content,filename", [
    (["--diff"], REMOVAL, "x.diff"),
    (["--functions"], "def alpha():\n    return 1\n", "x.py"),
])
def test_complete_units_never_truncate(tmp_path, flags, content, filename):
    path = write(tmp_path, filename, content)
    code, out, err, fake = jgrep(["alpha", path, *flags, "--max-chars", "10"])
    assert code == 2 and "never truncated" in err and not fake.bodies and not out


def test_python_methods_decorators_comments_unicode_and_nested_functions():
    source = '''# é is preserved
class Store:
    # alpha persists state
    @decorator
    async def save(self):
        def helper():
            return "é"
        return helper()

def other():
    return 0
'''
    rows = list(function_records(source, "store.py"))
    assert [r.unit["symbol"] for r in rows] == ["Store.save", "other"]
    assert rows[0].lineno == 3 and rows[0].end_line == 8
    assert "@decorator" in rows[0].text and "helper" in rows[0].text
    for rec in rows:
        assert source[rec.start:rec.end] == rec.text


def test_go_parser_handles_braces_in_strings_comments_methods_and_generics():
    pytest.importorskip("tree_sitter_go")
    source = '''package main
// Save alpha.
func (s *Store) Save() error {
    s.value = "{é}"
    // } does not end a function
    return nil
}
func Identity[T any](value T) T { return value }
'''
    rows = list(function_records(source, "store.go"))
    assert len(rows) == 2 and rows[0].lineno == 2 and rows[0].end_line == 7
    assert rows[0].unit["symbol"] == "(s *Store).Save"
    for rec in rows:
        assert source[rec.start:rec.end] == rec.text


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029", "\v", "\f"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_python_spans_use_physical_lines_not_unicode_separators(separator, newline):
    first = f'def first():\n    """left{separator}right"""\n    return "é"\n'.replace("\n", newline)
    second = "def second():\n\f    return 2\n".replace("\n", newline)
    source = first + newline + second
    rows = list(function_records(source, "source.py"))
    assert [r.text for r in rows] == [first, second]
    assert [(r.lineno, r.end_line) for r in rows] == [(1, 3), (5, 6)]
    for rec in rows:
        assert source[rec.start:rec.end] == rec.text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_go_functions_sharing_a_line_have_separate_character_spans(newline):
    pytest.importorskip("tree_sitter_go")
    first = 'func Écho() string { return "é\u2028text" }'
    second = "func Second() {}" + newline
    source = "package main" + newline + first + "; " + second
    rows = list(function_records(source, "source.go"))
    assert [r.text for r in rows] == [first, second]
    assert [r.unit["symbol"] for r in rows] == ["Écho", "Second"]
    assert [(r.lineno, r.end_line) for r in rows] == [(2, 2), (2, 2)]
    for rec in rows:
        assert source[rec.start:rec.end] == rec.text


def test_go_doc_comments_do_not_pull_in_previous_function():
    pytest.importorskip("tree_sitter_go")
    source = ('package main\nfunc First() {} // belongs to First\n'
              '// Second documentation\nfunc Second() {}\n')
    rows = list(function_records(source, "source.go"))
    assert rows[1].text == '// Second documentation\nfunc Second() {}\n'


@pytest.mark.parametrize("name,source", [("bad.py", "def invalid(\n"), ("bad.go", "package main\nfunc f( {\n")])
def test_invalid_syntax_fails_without_partial_function_results(tmp_path, name, source):
    if name.endswith(".go"):
        pytest.importorskip("tree_sitter_go")
    code, out, err, fake = jgrep(["alpha", write(tmp_path, name, source), "--functions"])
    assert code == 2 and err and not out and not fake.bodies


def test_function_record_export_needs_no_key_and_keeps_locations(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    path = write(tmp_path, "store.py", "# alpha\ndef save():\n    return 1\n")
    code, out, err, fake = jgrep(["--functions", "--emit-records", path])
    row = json.loads(out)
    assert code == 0 and not err and not fake.bodies
    assert row["text"].startswith("# alpha")
    assert row["id"].startswith(path + ":1-3:") and row["unit"]["symbol"] == "save"


def test_recursive_functions_skip_other_formats(tmp_path):
    write(tmp_path, "good.py", "def alpha():\n    return 1\n")
    write(tmp_path, "README.md", "not code")
    code, out, err, fake = jgrep(["alpha", str(tmp_path), "--functions", "-r", "--json"])
    assert code == 0 and not err and len(fake.bodies) == 1
    assert json.loads(out)["unit"]["symbol"] == "alpha"


def test_whole_file_export_is_not_cut_at_the_judgment_limit(tmp_path):
    text = "x" * 40 + "alpha\n"
    path = write(tmp_path, "data.txt", text)
    code, out, err, fake = jgrep(["--emit-records", "--whole", "--max-chars", "10", path])
    assert code == 0 and json.loads(out)["text"] == text and not fake.bodies


@pytest.mark.parametrize("flags", [
    ["--diff", "-C", "1"], ["--functions", "--para"], ["--lang", "go"],
    ["--emit-records", "-e", "alpha"], ["--emit-records", "-c"],
])
def test_invalid_combinations_never_call_provider(flags):
    code, out, err, fake = jgrep([*flags, "alpha"])
    assert code == 2 and err and not fake.bodies
