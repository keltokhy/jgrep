"""Unparsed C bodies are reported in function search and counted in diff context."""

import json

import pytest

from jgrep.code_inputs import parse_functions
from test_cli import env, jgrep, write
from test_function_context import commit, git
from test_function_context import repo as repo


@pytest.mark.parametrize("head", [
    "TEST(Suite, Name)", "SYSCALL_DEFINE2(foo, int, a, int, b)", "MACRO_DEFINED_FN(name, arg)",
])
def test_macro_headed_c_bodies_are_reported_and_fall_back_to_syntax_error(tmp_path, repo, head):
    pytest.importorskip("tree_sitter_c")
    source = head + "\n{\n  int n = 1;\n  use(n);\n  finish();\n}\n\nint alpha(void) { return 1; }\n"
    commit(repo, "start", {"macro.c": source})
    after = source.replace("n = 1", "n = 2")
    (repo / "macro.c").write_text(after)
    code, out, err, fake = jgrep(["alpha", str(repo / "macro.c"), "--functions", "--json"])
    assert code == 2 and "lines 1-6" in err and len(fake.bodies) == 1
    assert json.loads(out)["unit"]["symbol"] == "alpha"
    patch = write(tmp_path, "macro.patch", git(repo, "diff", "-U1"))
    args = ["alpha", patch, "--diff", "-W", "--repo", str(repo), "--json", "-p", "0", "--no-cache"]
    code, out, err, fake = jgrep(args)
    assert code == 0 and json.loads(out)["unit"]["context"] == {"fallback": "syntax_error"}
    assert "1 syntax_error" in err
    assert fake.bodies == jgrep(["alpha", patch, "--diff", "--no-cache"])[3].bodies


def test_c_skip_reports_the_whole_region_when_a_prototype_swallows_a_function(tmp_path):
    pytest.importorskip("tree_sitter_c")
    source = ('CURL_EXTERN int curl_thing(int a) CURL_DEPRECATED(1, "old");\n'
              'static inline int clean(int a)\n{\n  return a;\n}\n')
    found, problems = parse_functions(source, "prototype.c")
    assert not found and problems == [(1, 5, "lines 1-5")]
    code, out, err, fake = jgrep(["alpha", write(tmp_path, "prototype.c", source), "--functions"])
    assert code == 2 and not out and not fake.bodies
    assert "lines 1-5" in err and "function '" not in err
