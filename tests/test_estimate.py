"""Offline preview must match the actual judged units without spending or changing cache."""

import json

import pytest

from jgrep import cli
from jgrep.core import Cache, cache_path
from test_cli import env, jgrep, write
from test_code_inputs import REMOVAL


def test_estimate_without_credentials_never_creates_client_or_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(cli, "resolve_backend", lambda *_: (_ for _ in ()).throw(AssertionError("auth lookup")))
    path = write(tmp_path, "data.txt", "alpha\nalpha\n\nother\n")
    code, out, err, fake = jgrep(["alpha", path, "--estimate", "--json"])
    result = json.loads(out)
    assert code == 0 and not err and not fake.bodies and not cache_path().exists()
    assert result["records"] == 4 and result["blank_records"] == 1
    assert result["estimated_calls"] == 2 and result["duplicate_records"] == 1
    assert result["call_upper_bound"] == 3
    assert 0 < result["estimated_cost_usd"] < result["byte_estimate_cost_usd"]


def test_estimate_uses_existing_cache_read_only_and_no_cache_flag(tmp_path):
    path = write(tmp_path, "data.txt", "alpha\nother\n")
    assert jgrep(["alpha", path])[0] == 0
    guard = Cache()
    guard.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = cache_path().read_bytes()
    code, out, _, fake = jgrep(["alpha", path, "--estimate", "--json"])
    result = json.loads(out)
    assert code == 0 and not fake.bodies and result["cached_records"] == 2
    assert result["estimated_calls"] == 0 and result["estimated_cost_usd"] == 0
    assert cache_path().read_bytes() == before
    guard.db.close()
    result = json.loads(jgrep(["alpha", path, "--estimate", "--json", "--no-cache"])[1])
    assert result["estimated_calls"] == 2 and result["cached_records"] == 0


def test_partial_question_cache_and_model_separation(tmp_path):
    path = write(tmp_path, "data.txt", "alpha beta\n")
    jgrep(["alpha", path])
    extra = json.loads(jgrep(["-e", "alpha", "-e", "beta", path, "--estimate", "--json"])[1])
    cold = json.loads(jgrep(["-e", "alpha", "-e", "beta", path, "--estimate", "--json", "--model", "different"])[1])
    assert extra["estimated_calls"] == cold["estimated_calls"] == 1
    assert extra["estimated_input_tokens"] < cold["estimated_input_tokens"]


def test_estimate_diff_counts_hunks_not_lines_and_reports_errors(tmp_path):
    path = write(tmp_path, "data.diff", REMOVAL)
    result = json.loads(jgrep(["alpha", path, "--diff", "--estimate", "--json"])[1])
    assert result["records"] == 1 and result["estimated_calls"] == 1
    code, out, _, _ = jgrep(["alpha", path, "--diff", "--estimate", "--json", "--max-chars", "10"])
    result = json.loads(out)
    assert code == 2 and result["errors"] and result["estimated_calls"] == 0


def test_preview_full_input_even_when_matching_would_stop_early(tmp_path):
    path = write(tmp_path, "data.txt", "alpha\nbeta\n")
    result = json.loads(jgrep(["alpha", path, "--estimate", "--json", "-m", "0"])[1])
    assert result["records"] == 2
    result = json.loads(jgrep(["alpha", path, "--estimate", "--json", "--budget", "0.000001"])[1])
    assert result["estimate_exceeds_budget"] is True


def test_missing_input_is_json_error_and_empty_selection_is_success(tmp_path):
    code, out, _, _ = jgrep(["alpha", str(tmp_path / "absent"), "--estimate", "--json"])
    assert code == 2 and json.loads(out)["errors"]
    code, out, _, _ = jgrep(["alpha", str(tmp_path), "-r", "--glob", "*.absent", "--estimate", "--json"])
    assert code == 0 and json.loads(out)["records"] == 0


@pytest.mark.parametrize("url_variable", ["JEV_GATEWAY_URL", "JEV_URL"])
def test_estimate_and_filter_share_endpoint_scoped_cache(monkeypatch, tmp_path, url_variable):
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv(url_variable, "https://first.invalid")
    path = write(tmp_path, "data.txt", "alpha\n")
    command = ["alpha", path, "--api", "gateway"]
    assert len(jgrep(command)[3].bodies) == 1
    warm = json.loads(jgrep([*command, "--estimate", "--json"])[1])
    assert warm["cached_records"] == 1 and warm["estimated_calls"] == 0

    monkeypatch.setenv(url_variable, "https://second.invalid")
    cold = json.loads(jgrep([*command, "--estimate", "--json"])[1])
    assert cold["cached_records"] == 0 and cold["estimated_calls"] == 1
    assert len(jgrep(command)[3].bodies) == 1
