"""jgrep against a fake Decisions endpoint. No network, no key."""

import asyncio
import io
import json
import random
import threading

import httpx
import pytest
from jevkit_runtime import AnswerStore, Backend, Client, answer_key

from jgrep.cli import main
from jgrep import cli as cli_module
from jgrep.core import PROVIDERS

WORDS = ["alpha", "beta", "gamma"]


class Fake:
    """Says yes (0.9) when a word named in the question also appears in the state, else 0.1."""

    def __init__(self, *, jitter=0.0, script=None):
        self.bodies, self.jitter, self.script = [], jitter, list(script or [])

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.jitter:
            # A floor as well as a spread: an answer that lands in microseconds closes the
            # in-flight window before the rest of the input has even been queued.
            await asyncio.sleep(self.jitter * (0.5 + random.random() / 2))
        if self.script and (status := self.script.pop(0)) != 200:
            return httpx.Response(status, json={"error": {"message": f"scripted {status}", "code": status}})
        if "POISON" in body["state"]:
            return httpx.Response(400, json={"error": {"message": "bad state", "code": 400}})
        answers = {}
        for qid, q in body["questions"].items():
            hit = any(w in q["instructions"] and w in body["state"] for w in WORDS)
            answers[qid] = {"type": "noul", "noul": 0.9 if hit else 0.1}
        usage = {"input_tokens": 100, "output_tokens": 1, "cost": 0.01}
        return httpx.Response(200, json={"model": "typesafe/jev-1.13", "answers": answers, "usage": usage})


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("TYPESAFE_API_KEY", "JEV_API", "JEV_MODEL", "JEV_URL", "JGREP_BUDGET",
                 "JEV_GATEWAY_URL", "JEV_GATEWAY_API_KEY", "JEV_PRICE_PER_MTOK",
                 "JEV_DIFFUSIONGEMMA_URL", "JEV_DIFFUSIONGEMMA_API_KEY", "JEV_LAYA_URL", "JEV_LAYA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def jgrep(argv, fake=None, **kw):
    fake = fake or Fake(**kw)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, transport=httpx.MockTransport(fake), out=out, err=err)
    return code, out.getvalue(), err.getvalue(), fake


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_prints_matching_lines_in_input_order(tmp_path):
    lines = [f"{i} {'alpha' if i % 3 == 0 else 'nothing'}" for i in range(60)]
    f = write(tmp_path, "a.txt", "\n".join(lines) + "\n")
    code, out, _, _ = jgrep(["alpha", f, "-j", "16"], jitter=0.02)
    assert code == 0
    assert out.splitlines() == [l for l in lines if "alpha" in l]


@pytest.mark.parametrize("flags,first_status", [([], 200), (["-m", "1"], 200), ([], 400)])
def test_slow_first_record_bounds_ordered_work(tmp_path, flags, first_status):
    calls, ahead = [], []
    second_started = asyncio.Event()

    async def handler(request):
        text = json.loads(request.content)["state"]
        calls.append(text)
        if text == "row 0":
            await asyncio.wait_for(second_started.wait(), 1)
            # Let later requests finish while the first still occupies its slot.
            await asyncio.sleep(0.05)
            ahead.append(len(calls))
            if first_status != 200:
                return httpx.Response(first_status, json={"error": "bad first record"})
        else:
            second_started.set()
        return httpx.Response(200, json={"answers": {"d0": {"noul": 0.9}}})

    lines = [f"row {i}" for i in range(200)]
    path = write(tmp_path, "data.txt", "\n".join(lines) + "\n")
    code, out, _, _ = jgrep(["matches", path, "-j", "2", "--no-cache", "--budget", "0", *flags],
                           fake=handler)
    assert ahead == [2]
    assert code == (0 if first_status == 200 else 2)
    expected = lines[:1] if flags else lines if first_status == 200 else lines[1:]
    assert out.splitlines() == expected
    assert len(calls) == (2 if flags else len(lines))


@pytest.mark.parametrize("failure", ["fatal", "budget"])
def test_halt_interrupts_wait_for_ordered_output_slot(tmp_path, failure):
    cancelled, calls = [], []

    async def handler(request):
        text = json.loads(request.content)["state"]
        calls.append(text)
        if text == "row 0":
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.append(text)
                raise
        else:
            await asyncio.sleep(0.02)  # consumer is now waiting to admit row 2
            if failure == "fatal":
                return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"answers": {"d0": {"noul": 0.9}}, "usage": {"cost": 0.01}})

    path = write(tmp_path, "data.txt", "".join(f"row {i}\n" for i in range(20)))
    code, out, err, _ = jgrep(["matches", path, "-j", "2", "--no-cache", "--budget", "0.005"],
                             fake=handler)
    assert code == 2 and not out
    assert ("401" if failure == "fatal" else "budget") in err
    assert cancelled == ["row 0"] and len(calls) == 2


def test_invert_prob_and_line_numbers(tmp_path):
    f = write(tmp_path, "a.txt", "alpha one\nplain two\n")
    assert jgrep(["alpha", f, "-v"])[1] == "plain two\n"
    assert jgrep(["alpha", f, "-o", "-n", "-p", "0"])[1] == "0.900\t1:alpha one\n0.100\t2:plain two\n"


def test_count_and_filenames(tmp_path):
    a = write(tmp_path, "a.txt", "alpha\nalpha\nno\n")
    b = write(tmp_path, "b.txt", "no\n")
    assert jgrep(["alpha", a, "-c"])[1] == "2\n"
    assert jgrep(["alpha", a, b, "-c"])[1] == f"{a}:2\n{b}:0\n"
    assert jgrep(["alpha", a, b])[1] == f"{a}:alpha\n{a}:alpha\n"


def test_exit_status_and_quiet(tmp_path):
    f = write(tmp_path, "a.txt", "nothing here\n")
    assert jgrep(["alpha", f])[0] == 1
    g = write(tmp_path, "b.txt", "alpha\n" * 5)
    code, out, _, _ = jgrep(["alpha", g, "-q"])
    assert (code, out) == (0, "")


def test_max_count_stops_early(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\n" * 500)
    code, out, _, fake = jgrep(["alpha", f, "-m", "3", "--no-cache", "-j", "4"])
    assert out == "alpha\n" * 3
    assert len(fake.bodies) < 50


def test_blank_lines_cost_nothing(tmp_path):
    f = write(tmp_path, "a.txt", "\n   \nalpha\n")
    code, out, _, fake = jgrep(["alpha", f])
    assert out == "alpha\n" and len(fake.bodies) == 1
    assert jgrep(["alpha", f, "-v"])[1] == "\n   \n"


def test_second_run_is_served_from_cache(tmp_path):
    f = write(tmp_path, "a.txt", "alpha a\nalpha b\nplain c\n")
    first = jgrep(["alpha", f])
    second = jgrep(["alpha", f])
    assert first[1] == second[1]
    assert len(first[3].bodies) == 3 and len(second[3].bodies) == 0


def test_cache_is_per_description(tmp_path):
    f = write(tmp_path, "a.txt", "alpha beta\n")
    jgrep(["alpha", f])
    code, out, _, fake = jgrep(["-e", "alpha", "-e", "beta", f])
    assert list(fake.bodies[0]["questions"]) == ["d1"]  # only beta was new


def test_repeated_lines_share_one_call(tmp_path):
    f = write(tmp_path, "a.txt", "alpha same\n" * 40)
    code, out, _, fake = jgrep(["alpha", f, "--no-cache", "-j", "40"], jitter=0.05)
    assert out == "alpha same\n" * 40
    assert len(fake.bodies) == 1


def test_any_and_all(tmp_path):
    f = write(tmp_path, "a.txt", "alpha only\nalpha beta\nneither\n")
    assert jgrep(["-e", "alpha", "-e", "beta", f])[1] == "alpha only\nalpha beta\n"
    assert jgrep(["-e", "alpha", "-e", "beta", "--all", f])[1] == "alpha beta\n"
    body = jgrep(["-e", "alpha", "-e", "beta", f, "--no-cache"])[3].bodies[0]
    assert len(body["questions"]) == 2


def test_retries_a_gateway_timeout(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\n")
    code, out, err, fake = jgrep(["alpha", f, "--stats"], script=[504, 200])
    assert (code, out) == (0, "alpha\n")
    assert len(fake.bodies) == 2 and "1 retries" in err


def test_one_bad_line_does_not_stop_the_run(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\nPOISON alpha\nalpha again\n")
    code, out, err, _ = jgrep(["alpha", f])
    assert code == 2
    assert out == "alpha\nalpha again\n"
    assert f"{f}:2: HTTP 400: bad state" in err


def test_bad_key_is_fatal(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\n" * 20)
    code, out, err, fake = jgrep(["alpha", f, "--no-cache", "-j", "2"], script=[401] * 20)
    assert code == 2 and out == ""
    assert "openrouter said 401" in err and len(fake.bodies) <= 4


def test_missing_file_is_reported(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\n")
    code, out, err, _ = jgrep(["alpha", str(tmp_path / "nope.txt"), f])
    assert code == 2 and out.endswith("alpha\n") and "No such file" in err


def test_budget_halts_the_run(tmp_path):
    f = write(tmp_path, "a.txt", "".join(f"alpha {i}\n" for i in range(400)))
    code, out, err, fake = jgrep(["alpha", f, "--budget", "0.05", "-j", "2", "--no-cache"])
    assert code == 2 and "budget" in err
    assert len(fake.bodies) < 20


def test_budget_default_comes_from_the_environment(monkeypatch, tmp_path):
    f = write(tmp_path, "a.txt", "".join(f"alpha {i}\n" for i in range(400)))
    monkeypatch.setenv("JGREP_BUDGET", "0.05")
    assert jgrep(["alpha", f, "-j", "2", "--no-cache"])[0] == 2          # the environment's cap applies
    assert jgrep(["alpha", f, "--budget", "0", "--no-cache"])[0] == 0    # the flag overrides it
    monkeypatch.setenv("JGREP_BUDGET", "lots")
    code, _, err, _ = jgrep(["alpha", f])
    assert code == 2 and "JGREP_BUDGET must be a number" in err


def test_paragraphs_and_whole_files(tmp_path):
    f = write(tmp_path, "a.txt", "first line\nhas alpha\n\nsecond para\nplain\n")
    assert jgrep(["alpha", f, "--para"])[1] == "first line\nhas alpha\n\n"
    g = write(tmp_path, "b.txt", "nothing\n")
    code, out, _, fake = jgrep(["alpha", f, g, "--whole", "-o"])
    assert out == f"0.900\t{f}\n" and len(fake.bodies) == 2


def test_json_output(tmp_path):
    f = write(tmp_path, "a.txt", "plain\nalpha here\n")
    row = json.loads(jgrep(["alpha", f, "--json"])[1])
    assert row == {"file": f, "line": 2, "p": 0.9, "text": "alpha here"}


def test_no_key_is_explained(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    code, _, err, _ = jgrep(["alpha", write(tmp_path, "a.txt", "alpha\n")])
    assert code == 2 and "no API key" in err and "TYPESAFE_API_KEY" in err


def test_typesafe_api_is_preferred_when_it_has_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["authorization"], json.loads(request.content)["model"]))
        # TypeSafe's own API reports tokens but no cost.
        return httpx.Response(200, json={"model": "jev-latest", "answers": {"d0": {"type": "noul", "noul": 0.9}},
                                         "usage": {"input_tokens": 1_000_000, "output_tokens": 1}})

    out, err = io.StringIO(), io.StringIO()
    code = main(["alpha", write(tmp_path, "a.txt", "alpha\n"), "--stats"],
                transport=httpx.MockTransport(handler), out=out, err=err)
    assert code == 0 and out.getvalue() == "alpha\n"
    assert seen == [("https://api.typesafe.ai/v1/systemone", "Bearer ts-key", "jev-latest")]
    assert "$0.0420" in err.getvalue()  # priced from tokens


def test_api_flag_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    code, out, _, fake = jgrep(["alpha", write(tmp_path, "a.txt", "alpha\n"), "--api", "openrouter"])
    assert code == 0 and fake.bodies[0]["model"] == "~typesafe/jev-latest"


def test_key_file_is_read(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    (tmp_path / "config" / "jev").mkdir(parents=True)
    (tmp_path / "config" / "jev" / "openrouter.key").write_text("file-key\n")
    assert jgrep(["alpha", write(tmp_path, "a.txt", "alpha\n")])[0] == 0


def gateway_handler(seen):
    def handler(request):
        seen.append((str(request.url), request.headers["authorization"], json.loads(request.content)["model"]))
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {"d0": {"type": "noul", "noul": 0.9}},
                                         "usage": {"input_tokens": 300, "output_tokens": 1}})
    return handler


def test_gateway_is_called_at_its_own_url_with_its_own_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("JEV_GATEWAY_URL", "https://gateway.example.com/v1/systemone")
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "gw-key")
    seen, out, err = [], io.StringIO(), io.StringIO()
    code = main(["alpha", write(tmp_path, "a.txt", "alpha\n")],
                transport=httpx.MockTransport(gateway_handler(seen)), out=out, err=err)
    assert code == 0 and out.getvalue() == "alpha\n"
    assert seen == [("https://gateway.example.com/v1/systemone", "Bearer gw-key", "jev-latest")]


def test_gateway_url_and_key_can_come_from_config_files(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    (tmp_path / "config" / "jev").mkdir(parents=True)
    (tmp_path / "config" / "jev" / "gateway.key").write_text("file-key\n")
    (tmp_path / "config" / "jev" / "gateway.url").write_text("https://gw.internal/v1/systemone\n")
    seen = []
    code = main(["alpha", write(tmp_path, "a.txt", "alpha\n"), "--api", "gateway"],
                transport=httpx.MockTransport(gateway_handler(seen)), out=io.StringIO(), err=io.StringIO())
    assert code == 0
    assert seen == [("https://gw.internal/v1/systemone", "Bearer file-key", "jev-latest")]


def test_gateway_without_a_url_is_explained(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "gw-key")
    code, _, err, _ = jgrep(["alpha", write(tmp_path, "a.txt", "alpha\n"), "--api", "gateway"])
    assert code == 2 and "no URL for gateway" in err and "JEV_GATEWAY_URL" in err


def test_gateway_key_alone_does_not_win_the_default(monkeypatch, tmp_path):
    # A gateway key with no URL cannot be used, so the default falls through to a usable API.
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "gw-key")
    code, out, _, fake = jgrep(["alpha", write(tmp_path, "a.txt", "alpha\n")])
    assert code == 0 and fake.bodies[0]["model"] == "~typesafe/jev-latest"


def test_typesafe_error_bodies_are_readable(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_API", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "bad")

    def handler(request):
        return httpx.Response(401, json={"detail": {"error_type": "authentication_error",
                                                    "message": "Cannot authenticate with the server."}})

    out, err = io.StringIO(), io.StringIO()
    code = main(["alpha", write(tmp_path, "a.txt", "alpha\n")], transport=httpx.MockTransport(handler), out=out, err=err)
    assert code == 2 and "typesafe said 401: Cannot authenticate with the server." in err.getvalue()


def test_context_shows_the_neighbouring_lines_to_the_judge(tmp_path):
    f = write(tmp_path, "a.txt", "one\ntwo alpha\nthree\nfour\n")
    assert jgrep(["alpha", f])[1] == "two alpha\n"                  # judged alone, only line 2 fits
    assert jgrep(["alpha", f, "-C", "1"])[1] == "one\ntwo alpha\nthree\n"
    assert jgrep(["alpha", f, "-C", "2"])[1] == "one\ntwo alpha\nthree\nfour\n"


def test_context_marks_the_record_being_judged(tmp_path):
    f = write(tmp_path, "a.txt", "one\ntwo\nthree\n")
    code, out, _, fake = jgrep(["alpha", f, "-C", "1"])
    assert {b["state"] for b in fake.bodies} == {"> one\n  two", "  one\n> two\n  three", "  two\n> three"}
    assert '">" fit this description: "alpha"' in fake.bodies[0]["questions"]["d0"]["instructions"]


def test_context_stops_at_the_edges_of_a_file(tmp_path):
    a = write(tmp_path, "a.txt", "one\nlast alpha\n")
    b = write(tmp_path, "b.txt", "first\nsecond\n")
    code, out, _, fake = jgrep(["alpha", a, b, "-C", "1", "--no-filename"])
    assert out == "one\nlast alpha\n"                               # b.txt cannot see a.txt's alpha
    assert {body["state"] for body in fake.bodies} == {"> one\n  last alpha", "  one\n> last alpha",
                                                       "> first\n  second", "  first\n> second"}


def test_context_is_part_of_the_cache_key(tmp_path):
    f = write(tmp_path, "a.txt", "one\ntwo\nthree\n")
    assert len(jgrep(["alpha", f])[3].bodies) == 3
    assert len(jgrep(["alpha", f])[3].bodies) == 0                  # the same run is free
    assert len(jgrep(["alpha", f, "-C", "1"])[3].bodies) == 3       # a different window is not
    assert len(jgrep(["alpha", f, "-C", "1"])[3].bodies) == 0
    # a wider -C only re-asks the windows that actually widened: line 2 already saw the whole file
    assert len(jgrep(["alpha", f, "-C", "2"])[3].bodies) == 2


def test_context_keeps_input_order_and_line_numbers(tmp_path):
    lines = [f"{i} {'alpha' if i == 30 else 'nothing'}" for i in range(60)]
    f = write(tmp_path, "a.txt", "\n".join(lines) + "\n")
    code, out, _, _ = jgrep(["alpha", f, "-C", "2", "-n", "-j", "8"], jitter=0.02)
    assert code == 0
    assert out == "".join(f"{i + 1}:{lines[i]}\n" for i in range(28, 33))


def test_context_works_on_paragraphs(tmp_path):
    f = write(tmp_path, "a.txt", "first para\n\nhas alpha\n\nthird para\n")
    code, out, _, fake = jgrep(["alpha", f, "--para", "-C", "1"])
    assert out == "first para\n\nhas alpha\n\nthird para\n\n"   # --para keeps a blank line after each
    assert "> first para\n  has alpha" in {b["state"] for b in fake.bodies}


def test_context_zero_is_the_plain_behaviour(tmp_path):
    f = write(tmp_path, "a.txt", "one\ntwo alpha\n")
    code, out, _, fake = jgrep(["alpha", f, "-C", "0"])
    assert out == "two alpha\n"
    assert {b["state"] for b in fake.bodies} == {"one", "two alpha"}   # no marks, no neighbours


def test_context_is_rejected_where_it_makes_no_sense(tmp_path):
    f = write(tmp_path, "a.txt", "alpha\n")
    assert jgrep(["alpha", f, "-C", "-1"])[0] == 2
    assert "-C takes 0 or more" in jgrep(["alpha", f, "-C", "-1"])[2]
    assert jgrep(["alpha", f, "-C", "1", "--whole"])[0] == 2
    assert "--whole and -C" in jgrep(["alpha", f, "-C", "1", "--whole"])[2]


@pytest.mark.parametrize("answer", [None, {}, {"noul": None}, {"noul": "bad"},
                                   {"noul": float("nan")}, {"noul": float("inf")},
                                   {"noul": -0.1}, {"noul": 1.1}, {"noul": True}])
def test_malformed_answer_reports_error_and_preserves_later_matches(tmp_path, answer):
    f = write(tmp_path, "a.txt", "bad answer\nalpha later\n")
    good = Fake(jitter=0.01)

    async def handler(request):
        if json.loads(request.content)["state"] == "bad answer":
            await asyncio.sleep(0.02)
            return httpx.Response(200, content=json.dumps({"answers": {"d0": answer}}))
        return await good(request)

    code, out, err, _ = jgrep(["alpha", f], fake=handler)
    assert code == 2
    assert out == "alpha later\n"
    assert f"{f}:1:" in err and "answer" in err
    cache = AnswerStore()
    backend = Backend("openrouter", PROVIDERS["openrouter"].url, "~typesafe/jev-latest")
    key = answer_key(backend, "bad answer", cli_module.question("alpha"))
    assert cache.get(key) is None
    cache.close()


def test_malformed_cached_answer_does_not_block_later_matches(tmp_path):
    cache = AnswerStore()
    backend = Backend("openrouter", PROVIDERS["openrouter"].url, "~typesafe/jev-latest")
    key = answer_key(backend, "bad answer", cli_module.question("alpha"))
    cache.put(key, {})
    cache.close()
    f = write(tmp_path, "a.txt", "bad answer\nalpha later\n")
    code, out, err, _ = jgrep(["alpha", f])
    assert code == 2 and out == "alpha later\n"
    assert f"{f}:1:" in err and "question 'd0'" in err


def test_unexpected_judge_exception_is_reported(monkeypatch, tmp_path):
    original = Client.ask

    async def broken(self, state, questions):
        if state == "bad answer":
            raise RuntimeError("unexpected client failure")
        return await original(self, state, questions)

    monkeypatch.setattr(Client, "ask", broken)
    f = write(tmp_path, "a.txt", "bad answer\nalpha later\n")
    code, out, err, _ = jgrep(["alpha", f])
    assert code == 2 and out == "alpha later\n"
    assert "unexpected client failure" in err


@pytest.mark.parametrize("flags, expected", [(["-q"], 0), (["--budget", "0.005"], 2), (["-m", "1"], 0)])
def test_stop_after_eof_cancels_pending_requests(monkeypatch, tmp_path, flags, expected):
    eof = threading.Event()
    original = cli_module.records

    def tracked(*args, **kwargs):
        yield from original(*args, **kwargs)
        eof.set()

    monkeypatch.setattr(cli_module, "records", tracked)
    cancelled, completed = [], []
    fake = Fake()

    async def handler(request):
        state = json.loads(request.content)["state"]
        if state == "alpha fast":
            while not eof.is_set():
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.02)  # let the consumer receive EOF before this match
        else:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.append(state)
                raise
            completed.append(state)
        return await fake(request)

    f = write(tmp_path, "a.txt", "alpha fast\nalpha slow\n")
    code, _, _, _ = jgrep(["alpha", f, "-j", "2", "--no-cache", *flags], fake=handler)
    assert code == expected
    assert cancelled == ["alpha slow"] and completed == []


@pytest.mark.parametrize("extra", [[], ["--unordered"], ["-C", "1"]])
def test_max_count_is_per_input_file(tmp_path, extra):
    a = write(tmp_path, "a.txt", "alpha first\n" * 100)
    b = write(tmp_path, "b.txt", "alpha second\n" * 100)
    code, out, _, fake = jgrep(["alpha", a, b, "-m", "1", "-j", "2", *extra])
    assert code == 0 and out == f"{a}:alpha first\n{b}:alpha second\n"
    assert len(fake.bodies) < 20


def test_max_count_handles_repeated_filenames_and_counts(tmp_path):
    a = write(tmp_path, "a.txt", "alpha first\nalpha second\n")
    assert jgrep(["alpha", a, a, "-m", "1", "-c"])[1] == f"{a}:1\n{a}:1\n"


@pytest.mark.parametrize("flags, output", [([], ""), (["-c"], "0\n"), (["-c", "-q"], "")])
def test_max_count_zero_needs_no_key_input_or_requests(monkeypatch, flags, output):
    monkeypatch.delenv("OPENROUTER_API_KEY")

    def no_input(*args):
        pytest.fail("-m 0 must not read input")

    monkeypatch.setattr(cli_module, "records", no_input)
    code, out, err, fake = jgrep(["alpha", "-m", "0", *flags])
    assert (code, out, err) == (1, output, "") and not fake.bodies


def test_max_count_zero_prints_zero_for_each_file(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    code, out, _, fake = jgrep(["alpha", a, b, "-m", "0", "-c"])
    assert code == 1 and out == f"{a}:0\n{b}:0\n" and not fake.bodies


@pytest.mark.parametrize("flag,value", [("-j", "0"), ("-j", "-1"), ("-m", "-1")])
def test_invalid_concurrency_and_match_limits_fail_before_setup(monkeypatch, flag, value):
    def no_backend(*_, **__):
        pytest.fail("invalid limits must fail before creating a client")

    monkeypatch.setattr(cli_module, "resolve", no_backend)
    code, _, err, fake = jgrep(["alpha", flag, value])
    assert code == 2 and flag in err and not fake.bodies
