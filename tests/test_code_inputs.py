"""Contracts for complete changes, syntax boundaries, provenance, and offline composition."""

import json
import sys

import pytest

from jgrep.code_inputs import diff_records, export_record, function_records, parse_functions
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
    assert result["unit"] == {"kind": "diff", "hunk": 1, "commit": None, "old_file": "store.go", "new_file": "store.go",
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


SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40
HUNK = "diff --git a/{0} b/{0}\nindex 111..222 100644\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+alpha\n"
LOG = (f"commit {SHA_A} (HEAD -> main)\nAuthor: T <t@example.com>\nDate:   Sun Sep 20 2026\n\n"
       "    Subject line\n    \n    - a bullet\n    + a plus\n    --- not a header\n\n" + HUNK.format("one.c") + "\n"
       f"commit {SHA_B}\nMerge: 1111111 2222222\nAuthor: T <t@example.com>\n\n    A merge has no patch\n\n"
       f"commit {SHA_C}\nAuthor: T <t@example.com>\n\n    Third\n\n" + HUNK.format("two.c") + HUNK.format("three.c"))


def test_git_log_stream_gives_each_hunk_its_commit_and_input_location():
    rows = list(diff_records(LOG, "log.patch"))
    assert [(r.unit["commit"], r.unit["new_file"], r.unit["hunk"]) for r in rows] == [
        (SHA_A, "one.c", 1), (SHA_C, "two.c", 1), (SHA_C, "three.c", 1)]
    lines = LOG.split("\n")
    for rec in rows:
        assert lines[rec.lineno - 1] == "@@ -1 +1 @@" and lines[rec.end_line - 1] == "+alpha"
        assert rec.text == f"--- a/{rec.unit['new_file']}\n+++ b/{rec.unit['new_file']}\n@@ -1 +1 @@\n-old\n+alpha\n"
    ids = [export_record(r)["id"] for r in rows]
    assert ids[0].startswith(f"log.patch:15-17:one.c@{SHA_A[:12]}:-1+1:") and len(set(ids)) == 3
    short = list(diff_records(LOG.replace(SHA_A, SHA_A[:7]).replace(SHA_C, SHA_C[:9]), "log.patch"))
    assert [r.unit["commit"] for r in short] == [SHA_A[:7], SHA_C[:9], SHA_C[:9]]  # git log --abbrev-commit


def test_format_patch_stream_ignores_message_text_diffstat_and_signature():
    def mail(sha, name, body=""):
        return (f"From {sha} Mon Sep 17 00:00:00 2001\nFrom: T <t@example.com>\nSubject: [PATCH] change\n\n{body}"
                f"---\n {name} | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n\n" + HUNK.format(name) + "-- \n2.50.0\n\n")
    # An unindented message can start a line with a commit id; only mbox separators split this stream.
    body = f"- a bullet\n+ a plus\nThis reverts\ncommit {SHA_C}\n\n"
    rows = list(diff_records(mail(SHA_A, "one.c", body) + mail(SHA_B, "two.c"), "series.patch"))
    assert [(r.unit["commit"], r.unit["new_file"]) for r in rows] == [(SHA_A, "one.c"), (SHA_B, "two.c")]


def test_a_commit_that_cannot_be_read_does_not_hide_later_commits(tmp_path):
    merge = f"commit {SHA_B}\n\n    Merge\n\ndiff --cc x.c\nindex 1,2..3\n--- a/x.c\n+++ b/x.c\n@@@ -1,1 -1,1 +1,1 @@@\n- a\n -b\n++c\n\n"
    binary = f"commit {SHA_C}\n\n    Image\n\ndiff --git a/pic.png b/pic.png\nBinary files a/pic.png and b/pic.png differ\n"
    stream = LOG.split(f"commit {SHA_B}")[0] + merge + binary + f"commit {SHA_C}\n\n    Last\n\n" + HUNK.format("last.c")
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "log.patch", stream), "--diff", "--json"])
    assert code == 2 and [json.loads(line)["unit"]["new_file"] for line in out.splitlines()] == ["one.c", "last.c"]
    assert f"log.patch:19: commit {SHA_B[:12]}: combined merge diffs are unsupported" in err
    assert f"commit {SHA_C[:12]}: binary change 'pic.png'" in err and len(fake.bodies) == 2


def test_patch_lines_are_counted_at_line_feeds_only():
    # Form feeds and lone carriage returns occur inside C source lines; Git does not end a line there.
    patch = ("--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,3 @@\n \f\n-old\n+new\n keep\rcarriage\n"
             "--- a/y.c\n+++ b/y.c\n@@ -1 +1 @@\n-a\r\n+b\r\n")
    first, second = diff_records(patch, "patch")
    assert (first.lineno, first.end_line, second.lineno, second.end_line) == (3, 7, 10, 12)
    assert first.text + second.text == patch


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


C_SOURCE = """#include <stdio.h>

/*
 * Copies src; the caller frees the result. é
 */
static char *copy(const char *src,
                  size_t len)
{
  char *out = malloc(len + 1); /* } does not end a function */
  memcpy(out, src, len);
  return out;
}

// first line
// second line
int (*handler(void))(int) { return NULL; }
int count; // belongs to count
static inline int twice(int x) { return 2 * x; } int thrice(int x) { return 3 * x; }

#ifdef _WIN32
static int platform(void)
{
  return 1;
}
#else
static int platform(void)
{
  return 2;
}
#endif
"""


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_c_functions_keep_comments_symbols_and_exact_character_spans(newline):
    pytest.importorskip("tree_sitter_c")
    source = C_SOURCE.replace("\n", newline)
    rows = list(function_records(source, "copy.c"))
    assert [r.unit["symbol"] for r in rows] == ["copy", "handler", "twice", "thrice", "platform", "platform"]
    assert all(r.unit == {"kind": "function", "language": "c", "symbol": r.unit["symbol"]} for r in rows)
    assert [(r.lineno, r.end_line) for r in rows] == [(3, 12), (14, 16), (18, 18), (18, 18), (21, 24), (26, 29)]
    assert rows[0].text.startswith("/*" + newline + " * Copies src") and rows[0].text.endswith("}" + newline)
    assert rows[1].text.startswith("// first line" + newline + "// second line" + newline + "int (*handler")
    # A trailing comment on the previous declaration is not this function's documentation.
    assert rows[2].text == "static inline int twice(int x) { return 2 * x; }"
    assert rows[3].text == "int thrice(int x) { return 3 * x; }" + newline
    for rec in rows:
        assert source[rec.start:rec.end] == rec.text


MACRO_HEAVY = """#ifdef __cplusplus
extern "C" {
#endif
CURL_EXTERN int declared(void) UNUSED_ATTR;

int clean_before(void)
{
  return 0;
}

static int split_by_if(const char *ptr)
{
#ifdef USE_SSL
  if(ptr) {
#else
  if(!ptr) {
#endif
    return 1;
  }
  return 0;
}

int type_as_macro_argument(va_list ap)
{
  char *text = va_arg(ap, char *);
  return text != NULL;
}

int clean_after(void)
{
  return 3;
}
#ifdef __cplusplus
}
#endif
"""


def test_macro_heavy_c_emits_error_free_functions_and_reports_what_it_skipped(tmp_path):
    pytest.importorskip("tree_sitter_c")
    found, problems = parse_functions(MACRO_HEAVY, "macros.c")
    assert [r.unit["symbol"] for r in found] == ["clean_before", "clean_after"]
    for rec in found:
        assert MACRO_HEAVY[rec.start:rec.end] == rec.text
    # The unbalanced #if branches hide split_by_if from the parser; va_arg(ap, char *) is an error
    # inside a function it still recognizes. The prototype and extern "C" guard hold no body.
    assert problems == [(11, 21, "lines 11-21"), (23, 27, "function 'type_as_macro_argument' at line 23")]

    code, out, err, fake = jgrep(["alpha", write(tmp_path, "macros.c", MACRO_HEAVY), "--functions", "-p", "0", "--json"])
    assert code == 2 and len(fake.bodies) == 2
    assert [json.loads(line)["unit"]["symbol"] for line in out.splitlines()] == ["clean_before", "clean_after"]
    assert err.count("\n") == 1 and "lines 11-21, function 'type_as_macro_argument' at line 23" in err
    assert "macro or #if" in err and "Other functions were read" in err


def test_c_problem_report_is_one_bounded_message_per_file():
    pytest.importorskip("tree_sitter_c")
    source = "".join(f"int f{i}(va_list ap)\n{{\n  return va_arg(ap, char *) != 0;\n}}\n" for i in range(8))
    *rows, message = function_records(source, "many.c")
    assert not rows and "function 'f4' at line 17 and 3 more" in message and "f5" not in message


def test_c_language_is_inferred_for_headers_and_required_for_other_names(tmp_path):
    pytest.importorskip("tree_sitter_c")
    source = "static inline int alpha(void)\n{\n  return 1;\n}\n"
    code, out, err, _ = jgrep(["alpha", write(tmp_path, "util.h", source), "--functions", "--json"])
    assert code == 0 and not err and json.loads(out)["unit"] == {"kind": "function", "language": "c", "symbol": "alpha"}
    other = write(tmp_path, "util.inc", source)
    assert jgrep(["alpha", other, "--functions"])[0] == 2
    code, out, err, _ = jgrep(["alpha", other, "--functions", "--lang", "c", "--no-filename"])
    assert code == 0 and not err and out.startswith("1:static inline int alpha")


def test_recursive_functions_find_c_sources_and_headers(tmp_path):
    pytest.importorskip("tree_sitter_c")
    write(tmp_path, "a.c", "int alpha(void) { return 1; }\n")
    write(tmp_path, "b.h", "static int alpha_inline(void) { return 1; }\n")
    write(tmp_path, "notes.txt", "alpha is not code\n")
    code, out, err, fake = jgrep(["alpha", str(tmp_path), "--functions", "-r", "--json"])
    assert code == 0 and not err and len(fake.bodies) == 2
    assert [json.loads(line)["unit"]["symbol"] for line in out.splitlines()] == ["alpha", "alpha_inline"]


def test_c_functions_export_and_estimate_offline(monkeypatch, tmp_path):
    pytest.importorskip("tree_sitter_c")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    path = write(tmp_path, "copy.c", C_SOURCE)
    code, out, err, fake = jgrep(["--functions", "--emit-records", path])
    rows = [json.loads(line) for line in out.splitlines()]
    assert code == 0 and not err and not fake.bodies and len(rows) == 6
    assert rows[0]["id"].startswith(path + ":3-12:") and rows[0]["unit"]["language"] == "c"
    assert C_SOURCE[rows[0]["start"]:rows[0]["end"]] == rows[0]["text"]
    code, out, err, fake = jgrep(["alpha", path, "--functions", "--estimate", "--json"])
    report = json.loads(out)
    assert code == 0 and not fake.bodies and report["records"] == report["estimated_calls"] == 6
    # Skipped C is an error in the preview as well, beside the functions that would be judged.
    code, out, _, _ = jgrep(["alpha", write(tmp_path, "macros.c", MACRO_HEAVY), "--functions", "--estimate", "--json"])
    report = json.loads(out)
    assert code == 2 and report["records"] == 2 and "lines 11-21" in report["errors"][0]


def test_c_without_the_code_extra_names_the_install_command(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tree_sitter_c", None)
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "a.c", "int alpha(void) { return 1; }\n"), "--functions"])
    assert code == 2 and not out and not fake.bodies and "jev-grep[code]" in err and "C functions" in err


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


REVIEW = pytest.mark.xfail(strict=True, reason="reproduces a PR 6 review finding; fixed in the following commits")


def mail(sha, name, body="", signature="2.50.0\n"):
    return (f"From {sha} Mon Sep 17 00:00:00 2001\nFrom: T <t@example.com>\nSubject: [PATCH] change\n\n{body}"
            f"---\n {name} | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n\n" + HUNK.format(name) + "-- \n" + signature + "\n")


@REVIEW
def test_format_patch_message_and_signature_are_not_patch_content():
    # The message quotes a diff header at column zero and has its own "---" rule; format.signature
    # starts with a dash. Neither is part of the patch that follows the real separator.
    body = "Reverts the change below.\n\n---\ndiff --git a/quoted.c b/quoted.c\n--- a/quoted.c\n+ not a hunk\n\n"
    stream = mail(SHA_A, "one.c", body, signature="- signed with a dash\n+ and a plus\n trailing\n") + mail(SHA_B, "two.c")
    rows = list(diff_records(stream, "series.patch"))
    assert [(r.unit["commit"], r.unit["new_file"]) if not isinstance(r, str) else r for r in rows] == [
        (SHA_A, "one.c"), (SHA_B, "two.c")]
    lines = stream.split("\n")
    assert all(lines[r.lineno - 1] == "@@ -1 +1 @@" for r in rows)
    # Inside a hunk, "-- " is a removed line whose text is "- ", not a signature delimiter.
    dashes = mail(SHA_A, "list.md").replace("@@ -1 +1 @@\n-old\n+alpha\n", "@@ -1,2 +1 @@\n-- \n-old\n+alpha\n")
    rec, = diff_records(dashes, "series.patch")
    assert "\n-- \n-old\n+alpha\n" in rec.text and rec.unit["old_count"] == 2


@REVIEW
@pytest.mark.parametrize("bad", ['"a/bad\\x"', '"a/\\377.c"', '"a/unterminated'])
def test_malformed_quoted_path_fails_its_commit_only(bad):
    broken = HUNK.format("one.c").replace("--- a/one.c", "--- " + bad)
    stream = (f"commit {SHA_A}\nAuthor: T <t@example.com>\n\n    Bad path\n\n" + broken + "\n"
              f"commit {SHA_B}\nAuthor: T <t@example.com>\n\n    Good\n\n" + HUNK.format("two.c"))
    first, second = diff_records(stream, "log.patch")
    assert isinstance(first, str) and f"commit {SHA_A[:12]}" in first and "path" in first
    assert second.unit["new_file"] == "two.c" and second.unit["commit"] == SHA_B
    # In a plain patch the same path is one reported error, as other malformed patches are.
    with pytest.raises(ValueError, match="path"):
        list(diff_records(broken, "plain.patch"))


@pytest.mark.parametrize("length", [pytest.param(4, marks=REVIEW), pytest.param(5, marks=REVIEW), pytest.param(6, marks=REVIEW), 7, 12])
def test_short_commit_abbreviations_are_headers_only_above_a_git_log_field(length):
    stream = LOG.replace(SHA_A, SHA_A[:length]).replace(SHA_C, SHA_C[:length])
    assert [r.unit["commit"] for r in diff_records(stream, "log.patch")] == [SHA_A[:length]] + [SHA_C[:length]] * 2
    # "added", "decade" and "faced" are hexadecimal words. Unindented text before a patch is not a header.
    for word in ("added", "decade", "faced", "beef"):
        rec, = diff_records(f"commit {word}\nremoved the check\n\n" + HUNK.format("one.c"), "mail.patch")
        assert rec.unit["commit"] is None


@REVIEW
def test_skipped_c_is_reported_even_when_the_first_match_ends_the_run(tmp_path):
    pytest.importorskip("tree_sitter_c")
    clean = "".join(f"int alpha_{i}(void)\n{{\n  return {i};\n}}\n" for i in range(40))
    source = clean + "int skipped(va_list ap)\n{\n  char *text = va_arg(ap, char *);\n  return text != 0;\n}\n"
    path = write(tmp_path, "many.c", source)
    for flags in (["-m", "1"], ["-q"], ["-l"]):
        code, out, err, fake = jgrep(["alpha", path, "--functions", "-j", "2", *flags])
        assert code == (0 if flags == ["-q"] else 2) and "function 'skipped' at line 161" in err, flags
    first, *_ = function_records(source, "many.c")
    assert isinstance(first, str) and "skipped" in first


@REVIEW
def test_python_source_with_a_byte_order_mark_is_not_a_syntax_error():
    source = "﻿# alpha\ndef first():\n    return 1\n"
    rec, = function_records(source, "bom.py")
    assert rec.unit["symbol"] == "first" and source[rec.start:rec.end] == rec.text == source
