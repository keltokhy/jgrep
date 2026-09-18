"""Response validation and deadline regressions; only a local HTTP server is used."""

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
        handlers = set()
        calls = []
        chunks = []

        async def handle(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                await reader.readexactly(length)
                calls.append(1)
                if retry and len(calls) == 1:
                    writer.write(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                body = json.dumps({"answers": {"d0": {"noul": 0.9}}}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                             + b"\r\nConnection: close\r\n\r\n")
                # Every socket read completes well within the HTTPX timeout, but
                # the complete response takes longer than the remaining budget.
                for byte in body:
                    await asyncio.sleep(0.02)
                    writer.write(bytes([byte]))
                    await writer.drain()
                    chunks.append(1)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                handlers.discard(task)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        backend = Backend("local", f"http://127.0.0.1:{port}", "test", "UNUSED")
        timeout = 0.5 if retry else 0.15
        jev = Jev("test-key", backend, timeout=timeout)
        try:
            with pytest.raises(JevError, match="gave up after"):
                await jev.ask("alpha", {"d0": {"type": "noul", "instructions": "alpha"}})
            assert len(calls) == (2 if retry else 1)
            assert chunks  # timed out during the body, not while connecting
            assert jev.meter.calls == 0
        finally:
            await jev.close()
            server.close()
            await server.wait_closed()
            pending = list(handlers)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("answers", [None, [], {"d0": {"noul": 0.9}, "d1": {}}])
def test_invalid_answers_are_not_partially_cached(tmp_path, answers):
    async def exercise():
        cache = Cache(tmp_path / "answers.sqlite")
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"answers": answers}))
        jev = Jev("test-key", cache=cache, transport=transport)
        questions = {"d0": {"type": "noul", "instructions": "alpha"},
                     "d1": {"type": "noul", "instructions": "beta"}}
        try:
            with pytest.raises(JevError, match="answer"):
                await jev.ask("text", questions)
            assert cache.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 0
        finally:
            await jev.close()
            cache.db.close()

    asyncio.run(exercise())
