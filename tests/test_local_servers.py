"""Local decision servers are opt-in, keyless, free, and use the same request contract."""

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from jevkit_runtime import AnswerStore, Client, JevFatal, resolve
from jgrep.cli import main
from jgrep.core import PROVIDERS
from test_cli import env, jgrep, write  # noqa: F401  (env is the isolation fixture)


@pytest.mark.parametrize("api,url,model", [
    ("diffusiongemma", "http://127.0.0.1:8080/v1/systemone", "openjev-latest"),
    ("laya", "http://127.0.0.1:8081/v1/systemone", "laya-421m"),
])
def test_local_cli_needs_no_key_and_does_not_charge_jev_prices(monkeypatch, tmp_path, api, url, model):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    seen = []

    def answer(request):
        body = json.loads(request.content)
        seen.append((str(request.url), request.headers.get("authorization"), body))
        return httpx.Response(200, json={
            "model": "openjev-0.1", "answers": {"d0": {"noul": 0.9}},
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
        })

    path = write(tmp_path, "lines.txt", "alpha\nbeta\n")
    out, err = io.StringIO(), io.StringIO()
    code = main(["alpha", path, "--api", api, "--stats", "-j", "1", "--budget", "0.001"],
                transport=httpx.MockTransport(answer), out=out, err=err)
    assert code == 0 and out.getvalue() == "alpha\nbeta\n"
    assert "$0.0000" in err.getvalue() and "2,000,000 tokens" in err.getvalue()
    assert len(seen) == 2
    assert seen[0] == (url, None, {
        "model": model, "state": "alpha",
        "questions": {"d0": {"type": "noul", "instructions": 'The text fits this description: "alpha"'}},
    })


@pytest.mark.parametrize("source", ["env", "file"])
def test_custom_server_auth_model_and_cache(monkeypatch, tmp_path, source):
    url = "https://diffusion.example/v1/systemone"
    if source == "env":
        monkeypatch.setenv("JEV_DIFFUSIONGEMMA_URL", url)
        monkeypatch.setenv("JEV_DIFFUSIONGEMMA_API_KEY", "local-key")
    else:
        config = tmp_path / "config" / "jev"
        config.mkdir(parents=True)
        (config / "diffusiongemma.url").write_text(url + "\n")
        (config / "diffusiongemma.key").write_text("local-key\n")
    monkeypatch.setenv("JEV_API", "diffusiongemma")
    seen = []

    def answer(request):
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {qid: {"noul": 0.8} for qid in body["questions"]},
                                         "usage": {"input_tokens": 30, "cost": 0.012}})

    path = write(tmp_path, "lines.txt", "alpha\n")
    args = ["-e", "alpha", "-e", "beta", path, "--model", "openjev-0.1", "--stats"]
    code, out, err, _ = jgrep(args, fake=answer)
    assert code == 0 and out == "alpha\n" and "$0.0120" in err
    assert str(seen[0].url) == url and seen[0].headers["authorization"] == "Bearer local-key"
    assert json.loads(seen[0].content)["model"] == "openjev-0.1"
    assert len(json.loads(seen[0].content)["questions"]) == 2
    assert jgrep(args, fake=answer)[1] == out and len(seen) == 1
    warm = json.loads(jgrep([*args, "--estimate", "--json"])[1])
    assert warm["cached_records"] == 1 and warm["estimated_calls"] == 0
    monkeypatch.setenv("JEV_DIFFUSIONGEMMA_URL", "https://other.example/v1/systemone")
    cold = json.loads(jgrep([*args, "--estimate", "--json"])[1])
    assert cold["cached_records"] == 0 and cold["estimated_calls"] == 1
    assert jgrep(args, fake=answer)[0] == 0 and len(seen) == 2


@pytest.mark.parametrize("api", ["diffusiongemma", "laya"])
def test_local_server_is_never_selected_implicitly(monkeypatch, tmp_path, api):
    monkeypatch.setenv(f"JEV_{api.upper()}_API_KEY", "optional-key")
    monkeypatch.setenv(f"JEV_{api.upper()}_URL", "https://local.example/v1/systemone")
    assert resolve(PROVIDERS).name == "openrouter"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(JevFatal, match="no API key"):
        resolve(PROVIDERS)
    path = write(tmp_path, "lines.txt", "alpha\n")
    result = json.loads(jgrep(["alpha", path, "--estimate", "--json"])[1])
    assert result["api"] == "typesafe"


def test_offline_estimate_uses_the_local_price_unless_one_is_configured(monkeypatch, tmp_path):
    path = write(tmp_path, "lines.txt", "alpha\n")
    args = ["alpha", path, "--api", "diffusiongemma", "--estimate", "--json"]
    code, out, err, fake = jgrep(args)
    result = json.loads(out)
    assert code == 0 and not err and not fake.bodies
    assert result["model"] == "openjev-latest" and result["estimated_calls"] == 1
    assert result["price_per_million_input_tokens_usd"] == result["estimated_cost_usd"] == 0
    monkeypatch.setenv("JEV_PRICE_PER_MTOK", "0.5")
    result = json.loads(jgrep(args)[1])
    assert result["estimated_cost_usd"] == result["estimated_input_tokens"] * 0.5 / 1e6


def test_batch_dependent_answers_do_not_reuse_single_question_cache(tmp_path):
    calls = []

    def answer(request):
        body = json.loads(request.content)
        calls.append(body)
        p = 0.9 if len(body["questions"]) == 2 else 0.1  # a joint read can change every slot's answer
        return httpx.Response(200, json={"answers": {qid: {"noul": p} for qid in body["questions"]}})

    path = write(tmp_path, "lines.txt", "alpha beta\n")
    single = ["--api", "diffusiongemma", "alpha", path]
    packed = ["--api", "diffusiongemma", "-e", "alpha", "-e", "beta", "--all", path]
    assert jgrep(single, fake=answer)[0] == 1
    preview = json.loads(jgrep([*packed, "--estimate", "--json"])[1])
    fresh = json.loads(jgrep([*packed, "--estimate", "--json", "--no-cache"])[1])
    assert preview["estimated_input_tokens"] == fresh["estimated_input_tokens"]
    assert jgrep(packed, fake=answer)[1] == "alpha beta\n"
    assert len(calls[-1]["questions"]) == 2
    assert jgrep(packed, fake=answer)[1] == "alpha beta\n" and len(calls) == 2
    assert jgrep(single, fake=answer)[0] == 1 and len(calls) == 2

    # A joint answer with one slot missing is replayed as the full batch.
    store = AnswerStore()
    try:
        store.db.execute("DELETE FROM answers WHERE key = (SELECT key FROM answers WHERE answer = ? LIMIT 1)",
                         (json.dumps({"noul": 0.9}),))
    finally:
        store.close()
    partial = json.loads(jgrep([*packed, "--estimate", "--json"])[1])
    assert partial["estimated_input_tokens"] == fresh["estimated_input_tokens"]
    assert jgrep(packed, fake=answer)[1] == "alpha beta\n"
    assert len(calls) == 3 and len(calls[-1]["questions"]) == 2


def test_joint_cache_preserves_slot_identity_and_question_order(tmp_path):
    calls = []

    def answer(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"answers": {
            qid: {"noul": p} for qid, p in zip(body["questions"], (0.2, 0.8))}})

    async def exercise():
        store = AnswerStore(tmp_path / "answers.sqlite")
        client = Client(resolve(PROVIDERS, "diffusiongemma"), store=store, transport=httpx.MockTransport(answer))
        q = {"type": "noul", "instructions": "same description"}
        original = {"first": q, "second": q}
        try:
            first = await client.ask("state", original)
            assert first == {"first": {"noul": 0.2}, "second": {"noul": 0.8}}
            reversed_order = await client.ask("state", {"second": q, "first": q})
            assert reversed_order == {"second": {"noul": 0.2}, "first": {"noul": 0.8}}
            assert await client.ask("state", original) == first
            assert len(calls) == 2
        finally:
            await client.close()
            store.close()

    asyncio.run(exercise())


def test_real_cli_reads_local_http_and_respects_a_configured_price(monkeypatch, tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append((self.path, self.headers.get("authorization"), body))
            reply = json.dumps({"model": "openjev-0.1", "answers": {"d0": {"noul": 0.875}},
                                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("JEV_DIFFUSIONGEMMA_URL", f"http://127.0.0.1:{server.server_port}/v1/systemone")
    monkeypatch.setenv("JEV_PRICE_PER_MTOK", "0.125")
    command = [sys.executable, "-m", "jgrep", "--api", "diffusiongemma", "--stats", "--json", "alpha"]
    try:
        result = subprocess.run(command, input="alpha\n", text=True, capture_output=True, timeout=30,
                                env=os.environ.copy())
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["p"] == 0.875 and "$0.1250" in result.stderr
        assert seen[0][:2] == ("/v1/systemone", None)
        assert seen[0][2]["model"] == "openjev-latest"
        preview = subprocess.run([*command, "--estimate", "--no-cache"], input="alpha\n", text=True,
                                 capture_output=True, timeout=30, env=os.environ.copy())
        assert preview.returncode == 0, preview.stderr
        assert json.loads(preview.stdout)["price_per_million_input_tokens_usd"] == 0.125
        assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
