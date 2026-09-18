"""End-to-end input, traversal and passage-search behavior against a fake API."""

import asyncio
import builtins
import csv
import io
import json

import httpx
import pytest

from jgrep import cli, inputs
from jgrep.core import Jev
from test_cli import Fake, env, jgrep, write


@pytest.mark.parametrize("flag,value", [
    ("-p", "nan"), ("-p", "inf"), ("-p", "-0.1"), ("-p", "1.1"),
    ("--budget", "nan"), ("--budget", "inf"), ("--budget", "-1"),
    ("--timeout", "nan"), ("--timeout", "inf"), ("--timeout", "0"), ("--timeout", "-1"),
    ("--max-chars", "0"), ("--max-chars", "-1"), ("--chunks", "0"), ("--chunks", "-1"),
])
def test_numeric_validation_precedes_client_setup(monkeypatch, flag, value):
    def no_backend(*args):
        pytest.fail("invalid options must not create a client")

    monkeypatch.setattr(cli, "resolve_backend", no_backend)
    code, out, err, fake = jgrep(["alpha", f"{flag}={value}"])
    assert code == 2 and flag in err and not out and not fake.bodies


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_budget_environment_rejects_nonfinite_and_negative_values(monkeypatch, value):
    monkeypatch.setenv("JGREP_BUDGET", value)
    code, _, err, fake = jgrep(["alpha"])
    assert code == 2 and "JGREP_BUDGET" in err and not fake.bodies


def test_read_failure_reports_error_and_continues_next_file(monkeypatch, tmp_path):
    a = str(tmp_path / "broken.txt")
    b = write(tmp_path, "good.txt", "alpha later\n")

    class Faulty(io.StringIO):
        def __next__(self):
            if self.tell():
                raise OSError("disk read failed")
            return super().__next__()

    def open_input(name, *args, **kwargs):
        return Faulty("alpha before error\n") if name == a else builtins.open(name, *args, **kwargs)

    monkeypatch.setattr(inputs, "open", open_input, raising=False)
    code, out, err, _ = jgrep(["alpha", a, b, "--no-filename"])
    assert code == 2 and out == "alpha before error\nalpha later\n"
    assert "disk read failed" in err


def test_unexpected_reader_failure_always_signals_eof(monkeypatch):
    def broken(*args):
        yield cli.Record(0, "broken.txt", 1, "alpha before error")
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(cli, "records", broken)

    async def exercise():
        args = cli.parser().parse_args(["--budget", "0"])
        out, err = io.StringIO(), io.StringIO()
        jev = Jev("test", transport=httpx.MockTransport(Fake()))
        code = await asyncio.wait_for(cli.run(args, ["alpha"], ["broken.txt"], jev, out, err), timeout=1)
        assert code == 2 and out.getvalue() == "alpha before error\n"
        assert "reader exploded" in err.getvalue()

    asyncio.run(exercise())


def test_duplicate_paths_have_independent_counts_and_context(tmp_path):
    f = write(tmp_path, "a.txt", "alpha one\nalpha two\n")
    assert jgrep(["alpha", f, f, "-c"])[1] == f"{f}:2\n{f}:2\n"
    _, _, _, fake = jgrep(["alpha", f, f, "-C", "1", "--no-cache"])
    assert {body["state"] for body in fake.bodies} == {"> alpha one\n  alpha two", "  alpha one\n> alpha two"}


def test_options_can_appear_between_file_arguments(tmp_path):
    a = write(tmp_path, "a.txt", "alpha\n")
    b = write(tmp_path, "b.txt", "alpha\n")
    assert jgrep(["alpha", a, "-c", b])[1] == f"{a}:1\n{b}:1\n"


def test_jsonl_judges_only_selected_field_and_preserves_original(tmp_path):
    first = '{ "id": 1, "message": "alpha hit", "secret": "untouched" }'
    second = '{"id":2,"message":"nothing","other":"alpha outside field"}'
    f = write(tmp_path, "a.jsonl", first + "\n" + second + "\n")
    code, out, _, fake = jgrep(["alpha", f, "--jsonl", "--field", "message"])
    assert code == 0 and out == first + "\n"
    assert {body["state"] for body in fake.bodies} == {"alpha hit", "nothing"}


def test_jsonl_nested_field_and_json_envelope(tmp_path):
    obj = {"events": [{"message": "alpha hit"}], "id": 7}
    raw = json.dumps(obj)
    f = write(tmp_path, "a.jsonl", raw + "\n")
    code, out, _, _ = jgrep(["alpha", f, "--jsonl", "--field", "events.0.message", "--json"])
    row = json.loads(out)
    assert code == 0 and row["record"] == obj and row["text"] == raw
    assert row["field"] == "events.0.message" and row["line"] == 1


def test_jsonl_literal_dotted_keys_and_nonstring_values(tmp_path):
    f = write(tmp_path, "a.jsonl", '{"event.message": {"body": "alpha"}}\n')
    code, _, _, fake = jgrep(["alpha", f, "--jsonl", "--field", "event.message"])
    assert code == 0 and json.loads(fake.bodies[0]["state"]) == {"body": "alpha"}


def test_jsonl_errors_are_reported_and_not_inverted_into_matches(tmp_path):
    f = write(tmp_path, "a.jsonl", 'broken\n{"wrong": "alpha"}\n[]\n{"message":"plain"}\n')
    code, out, err, fake = jgrep(["alpha", f, "--jsonl", "--field", "message", "-v"])
    assert code == 2 and out == '{"message":"plain"}\n' and len(fake.bodies) == 1
    assert f"{f}:1:" in err and f"{f}:2:" in err and f"{f}:3:" in err


def test_csv_preserves_header_quoting_and_multiline_rows(tmp_path):
    header = 'id,description,other'
    matched = '1,"alpha, on line one\nand line two","quote ""here"""'
    f = write(tmp_path, "a.csv", header + "\n" + matched + '\n2,plain,alpha\n')
    code, out, _, fake = jgrep(["alpha", f, "--csv", "--field", "description"])
    assert code == 0 and out == header + "\n" + matched + "\n"
    assert list(csv.reader(io.StringIO(out)))[1] == ['1', 'alpha, on line one\nand line two', 'quote "here"']
    assert {body["state"] for body in fake.bodies} == {"alpha, on line one\nand line two", "plain"}


def test_csv_json_output_has_full_record_and_physical_line_number(tmp_path):
    f = write(tmp_path, "a.csv", 'id,description\n1,"plain\ncontinued"\n2,alpha\n')
    code, out, _, _ = jgrep(["alpha", f, "--csv", "--field", "description", "--json"])
    row = json.loads(out)
    assert code == 0 and row["line"] == 4 and row["record"] == {"id": "2", "description": "alpha"}


def test_csv_bom_and_quoted_header(tmp_path):
    raw = '\ufeff"description",id\r\nalpha,1\r\n'
    f = tmp_path / "a.csv"
    f.write_bytes(raw.encode("utf-8"))
    code, out, _, _ = jgrep(["alpha", str(f), "--csv", "--field", "description"])
    assert code == 0 and out == '\ufeff"description",id\nalpha,1\n'


def test_csv_counts_identify_each_input_file(tmp_path):
    a = write(tmp_path, "a.csv", 'message\nalpha\n')
    b = write(tmp_path, "b.csv", 'message\nplain\n')
    assert jgrep(["alpha", a, b, "--csv", "--field", "message", "-c"])[1] == f"{a}:1\n{b}:0\n"


@pytest.mark.parametrize("content,message", [
    ('id,other\n1,alpha\n', "missing CSV column"),
    ('description,description\nalpha,alpha\n', "duplicate CSV column"),
    ('id,description\n1,alpha,extra\n', "expected 2 CSV columns"),
    ('id,description\n1,"unclosed\n', "unexpected end of data"),
])
def test_csv_bad_headers_and_rows_are_errors(tmp_path, content, message):
    f = write(tmp_path, "a.csv", content)
    code, out, err, fake = jgrep(["alpha", f, "--csv", "--field", "description"])
    assert code == 2 and message in err and not out and not fake.bodies


@pytest.mark.parametrize("extra,expected", [(["-c"], "1\n"), (["-q"], ""), (["-l"], "FILE\n")])
def test_csv_counts_quiet_and_filenames_do_not_emit_headers(tmp_path, extra, expected):
    f = write(tmp_path, "a.csv", 'id,description\n1,alpha\n2,plain\n')
    code, out, _, _ = jgrep(["alpha", f, "--csv", "--field", "description", *extra])
    assert code == 0 and out == expected.replace("FILE", f)


def test_structured_multifile_output_has_no_implicit_filename_prefix(tmp_path):
    a = write(tmp_path, "a.jsonl", '{"text":"alpha one"}\n')
    b = write(tmp_path, "b.jsonl", '{"text":"alpha two"}\n')
    code, out, _, _ = jgrep(["alpha", a, b, "--jsonl", "--field", "text"])
    assert code == 0 and [json.loads(line)["text"] for line in out.splitlines()] == ["alpha one", "alpha two"]


def test_recursive_search_respects_nested_ignore_rules_globs_and_exclusions(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "ignored").mkdir()
    (root / ".git").mkdir()
    (root / ".gitignore").write_text('*.log\n!keep.log\nignored/\n')
    (root / "sub" / ".gitignore").write_text('!local.log\n')
    for name in ('keep.log', 'skip.log', 'other.txt', 'sub/local.log', 'sub/skip.log', 'ignored/a.log', '.git/keep.log'):
        (root / name).write_text("alpha " + name + "\n")
    code, out, _, fake = jgrep(["alpha", str(root), "-r", "-l", "--glob", "*.log", "--exclude", "skip.log"])
    assert code == 0 and out.splitlines() == [str(root / "keep.log"), str(root / "sub/local.log")]
    assert len(fake.bodies) == 2


def test_recursive_search_honors_parent_ignore_rules_and_no_ignore(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text('*.log\n')
    (tmp_path / "sub").mkdir()
    f = write(tmp_path / "sub", "a.log", "alpha\n")
    monkeypatch.chdir(tmp_path)
    assert jgrep(["alpha", "sub", "-r"])[0] == 1
    assert jgrep(["alpha", "sub", "-r", "--no-ignore", "-l"])[1] == "sub/a.log\n"
    assert jgrep(["alpha", f])[1] == "alpha\n"  # explicit files override ignore files


def test_recursive_default_root_binary_files_and_symlink_loops(monkeypatch, tmp_path):
    write(tmp_path, "a.txt", "alpha\n")
    (tmp_path / "binary.txt").write_bytes(b'alpha\x00binary')
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    code, out, _, fake = jgrep(["alpha", "-r", "-l", "--glob", "**/*.txt"])
    assert code == 0 and out == "a.txt\n" and len(fake.bodies) == 1


def test_filtered_out_files_do_not_fall_back_to_stdin_or_require_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    write(tmp_path, "a.txt", "alpha\n")
    assert jgrep(["alpha", str(tmp_path), "-r", "--glob", "*.log"])[0] == 1


def test_directory_without_recursive_is_an_error(tmp_path):
    code, out, err, fake = jgrep(["alpha", str(tmp_path)])
    assert code == 2 and "use -r" in err and not out and not fake.bodies


def test_filenames_only_stops_each_file_and_overrides_count(tmp_path):
    a = write(tmp_path, "a.txt", "alpha\n" * 100)
    b = write(tmp_path, "b.txt", "plain\nalpha later\n")
    code, out, _, fake = jgrep(["alpha", a, b, "-l", "-c", "-j", "1", "--no-cache"])
    assert code == 0 and out == f"{a}\n{b}\n" and len(fake.bodies) < 10


@pytest.mark.parametrize("mode", [[], ["--para"], ["--whole"]])
def test_truncation_is_reported_even_when_it_hides_a_match(tmp_path, mode):
    f = write(tmp_path, "a.txt", "x" * 20 + "alpha\n")
    code, out, err, fake = jgrep(["alpha", f, "--max-chars", "10", *mode])
    assert code == 1 and not out and "truncated 1 records" in err
    assert fake.bodies[0]["state"] == "x" * 10


def test_chunks_find_late_passages_with_source_offsets(tmp_path):
    text = "x" * 9000 + "\nalpha later\n"
    f = write(tmp_path, "a.txt", text)
    code, out, err, fake = jgrep(["alpha", f, "--chunks", "1000", "--overlap", "0", "--json"])
    row = json.loads(out)
    assert code == 0 and not err and len(fake.bodies) <= 10
    assert row["start"] == 9000 and row["end"] == len(text) and row["chunk"] == 10
    assert row["text"] == text[9000:] and row["line"] == 1 and row["end_line"] == 2


def test_chunk_overlap_finds_a_phrase_across_a_boundary(tmp_path):
    f = write(tmp_path, "a.txt", "xxxxxalphaend")
    code, out, _, _ = jgrep(["alpha", f, "--chunks", "8", "--overlap", "3", "--json"])
    row = json.loads(out)
    assert code == 0 and row["text"] == "alphaend" and row["start"] == 5


def test_chunk_locations_use_unicode_characters_and_default_overlap(tmp_path):
    f = write(tmp_path, "a.txt", "ééé\nalpha\n")
    # Use threshold 0 to inspect every passage without relying on a word being
    # fully present in a particular default overlap window.
    code, out, _, _ = jgrep(["alpha", f, "--chunks", "8", "--json", "-p", "0"])
    rows = [json.loads(line) for line in out.splitlines()]
    assert [row["start"] for row in rows] == [0, 6]
    assert rows[1]["line"] == 2 and rows[1]["text"] == "pha\n"


def test_chunk_filenames_mode_finds_match_past_default_truncation(tmp_path):
    f = write(tmp_path, "a.txt", "x" * 8100 + "alpha")
    code, out, _, _ = jgrep(["alpha", f, "--chunks", "1000", "-l"])
    assert code == 0 and out == f + "\n"


@pytest.mark.parametrize("flags", [
    ["--jsonl"], ["--csv"], ["--field", "text"],
    ["--jsonl", "--field", "text", "--whole"],
    ["--csv", "--field", "text", "--chunks", "100"],
    ["--chunks", "100", "--para"], ["--chunks", "100", "-C", "1"],
    ["--chunks", "100", "--overlap", "100"], ["--overlap", "1"],
])
def test_incompatible_input_modes_fail_before_reading(flags):
    code, _, err, fake = jgrep(["alpha", *flags])
    assert code == 2 and err and not fake.bodies
