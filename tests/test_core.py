"""Response validation and deadline regressions against fake transports."""

import asyncio
import json

import httpx
import pytest

from jgrep.core import Backend, Cache, Jev, JevError


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("JEV_MODEL", "JEV_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("retry", [False, True])
def test_total_deadline_includes_drip_fed_body_and_retries(retry):
    async def exercise():
        calls = []
        chunks = []

        class Drip(httpx.AsyncByteStream):
            def __init__(self, body):
                self.body = body

            async def __aiter__(self):
                for byte in self.body:
                    await asyncio.sleep(0.02)
                    chunks.append(1)
                    yield bytes([byte])

        async def handle(request):
            calls.append(1)
            if retry and len(calls) == 1:
                return httpx.Response(503)
            body = json.dumps({"answers": {"d0": {"noul": 0.9}}}).encode()
            return httpx.Response(200, headers={"Content-Length": str(len(body))}, stream=Drip(body))

        backend = Backend("local", "https://fixture.invalid", "test", key="test-key")
        timeout = 0.5 if retry else 0.15
        jev = Jev(backend, timeout=timeout, transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(JevError, match="gave up after"):
                await jev.ask("alpha", {"d0": {"type": "noul", "instructions": "alpha"}})
            assert len(calls) == (2 if retry else 1)
            assert chunks  # timed out during the body, not while connecting
            assert jev.meter.calls == 0
        finally:
            await jev.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("answers", [None, [], {"d0": {"noul": 0.9}, "d1": {}}])
def test_invalid_answers_are_not_partially_cached(tmp_path, answers):
    async def exercise():
        cache = Cache(tmp_path / "answers.sqlite")
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"answers": answers})

        transport = httpx.MockTransport(respond)
        backend = Backend("openrouter", "https://fixture.invalid", "v1", key="test-key")
        jev = Jev(backend, store=cache, transport=transport)
        questions = {"d0": {"type": "noul", "instructions": "alpha"},
                     "d1": {"type": "noul", "instructions": "beta"}}
        try:
            with pytest.raises(JevError, match="answer"):
                await jev.ask("text", questions)
            assert len(calls) == 1
            assert cache.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 0
        finally:
            await jev.close()
            cache.close()

    asyncio.run(exercise())


def test_cache_separates_endpoints_and_providers_but_reuses_same_origin(tmp_path):
    async def exercise():
        cache = Cache(tmp_path / "answers.sqlite")
        calls = []
        questions = {"d0": {"type": "noul", "instructions": "alpha"}}
        origins = [("gateway", "https://first.invalid", 0.9),
                   ("gateway", "https://second.invalid", 0.1),
                   ("other", "https://second.invalid", 0.2),
                   ("gateway", "https://first.invalid", 0.9)]
        try:
            for api, url, expected in origins:
                def respond(request):
                    calls.append(str(request.url))
                    return httpx.Response(200, json={"answers": {"d0": {"noul": expected}}})

                backend = Backend(api, url, "same-model", key="test-key")
                jev = Jev(backend, store=cache, transport=httpx.MockTransport(respond))
                try:
                    assert (await jev.ask("text", questions))["d0"]["noul"] == expected
                finally:
                    await jev.close()
            assert len(calls) == 3
        finally:
            cache.close()

    asyncio.run(exercise())
