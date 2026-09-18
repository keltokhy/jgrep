"""Client for TypeSafe's Jev decision model: two backends, an answer cache and a cost meter.

Jev can be reached through TypeSafe's own API or through OpenRouter. Both take one state and any
number of questions per call and return one typed answer per question. Answers are cached per
(model, state, question), so packing questions into a call and rerunning a command are both cheap.

This file is shared verbatim between the jgrep and jlink repositories.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

RETRYABLE = {408, 429, 500, 502, 503, 504, 529}
FATAL = {401, 402, 403}
# TypeSafe's API reports tokens but not cost; OpenRouter reports both.
PRICE_PER_MTOK = float(os.environ.get("JEV_PRICE_PER_MTOK", 0.042))


class JevError(Exception):
    """One request failed; the rest of the run can continue."""


class JevFatal(Exception):
    """Nothing will work until the user fixes something, such as a bad key or no credits."""


@dataclass(frozen=True)
class Backend:
    name: str
    url: str
    model: str
    key_env: str

    @property
    def key_file(self) -> Path:
        return config_dir() / f"{self.name}.key"

    def key(self) -> str | None:
        if os.environ.get(self.key_env):
            return os.environ[self.key_env].strip()
        return self.key_file.read_text().strip() if self.key_file.exists() else None


# Order matters: with keys for both, TypeSafe's own API is used.
BACKENDS = {
    "typesafe": Backend("typesafe", "https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE_API_KEY"),
    "openrouter": Backend("openrouter", "https://openrouter.ai/api/alpha/decisions", "~typesafe/jev-latest",
                          "OPENROUTER_API_KEY"),
}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jev"


def cache_path() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "jev" / "answers.sqlite"


def resolve_backend(name: str | None = None) -> tuple[Backend, str]:
    """The API to use and its key. A name (or JEV_API) wins; otherwise the first backend with a key."""
    name = name or os.environ.get("JEV_API")
    if name:
        if name not in BACKENDS:
            raise JevFatal(f"unknown API {name!r}; choose from {', '.join(BACKENDS)}")
        backend = BACKENDS[name]
        if not (key := backend.key()):
            raise JevFatal(f"no key for {name}. Set {backend.key_env} or put the key in {backend.key_file}")
        return backend, key
    for backend in BACKENDS.values():
        if key := backend.key():
            return backend, key
    options = " or ".join(b.key_env for b in BACKENDS.values())
    raise JevFatal(f"no API key. Set {options}, or put a key in {config_dir()}/<api>.key")


class Cache:
    """Answers on disk, keyed on the exact model, state and question."""

    def __init__(self, path: Path | None = None):
        path = path or cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        # WAL lets two tools in one pipeline share the file.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS answers "
                        "(key TEXT PRIMARY KEY, answer TEXT NOT NULL, at REAL NOT NULL) WITHOUT ROWID")

    @staticmethod
    def key(model: str, state, question: dict) -> str:
        blob = json.dumps([model, state, question], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        row = self.db.execute("SELECT answer FROM answers WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, answer: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, json.dumps(answer), time.time()))


@dataclass
class Meter:
    calls: int = 0
    cached: int = 0
    retries: int = 0
    input_tokens: int = 0
    cost: float = 0.0
    model: str = ""  # the model the API says answered, which resolves aliases like jev-latest
    latencies: list[float] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.calls:,} calls, {self.cached:,} cached"]
        if self.retries:
            parts.append(f"{self.retries:,} retries")
        if self.calls:
            parts.append(f"{self.input_tokens:,} tokens")
            parts.append(f"${self.cost:.4f}")
        return "; ".join(parts)


class Jev:
    def __init__(self, key: str, backend: Backend | str = "openrouter", *, model: str | None = None,
                 timeout: float = 15.0, attempts: int = 4, concurrency: int = 32, cache: Cache | None = None,
                 transport=None):
        self.backend = BACKENDS[backend] if isinstance(backend, str) else backend
        self.model = model or os.environ.get("JEV_MODEL") or self.backend.model
        self.url = os.environ.get("JEV_URL") or self.backend.url
        self.timeout, self.attempts, self.cache = timeout, attempts, cache
        self.meter = Meter()
        self._flights: dict[str, asyncio.Task] = {}
        self.http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}", "X-Title": "jev tools"},
            limits=httpx.Limits(max_connections=concurrency + 4, max_keepalive_connections=concurrency + 4),
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def ask(self, state, questions: dict[str, dict]) -> dict[str, dict]:
        """Answer every question about one state. Only questions missing from the cache are sent."""
        keys = {qid: Cache.key(self.model, state, q) for qid, q in questions.items()}
        answers = {}
        if self.cache:
            for qid, k in keys.items():
                if (hit := self.cache.get(k)) is not None:
                    answers[qid] = hit
        misses = {qid: q for qid, q in questions.items() if qid not in answers}
        if not misses:
            self.meter.cached += 1
            return answers

        # Identical requests already in the air share one call; logs repeat themselves a lot.
        flight = "|".join(sorted(keys[qid] for qid in misses))
        task = self._flights.get(flight)
        if task is None:
            task = asyncio.ensure_future(self._call(state, misses))
            self._flights[flight] = task
            task.add_done_callback(lambda _: self._flights.pop(flight, None))
        else:
            self.meter.cached += 1
        by_key = await task
        return answers | {qid: by_key[keys[qid]] for qid in misses}

    async def _call(self, state, questions: dict[str, dict]) -> dict[str, dict]:
        """One request, retried inside a total time budget. Returns answers by cache key."""
        body = {"model": self.model, "state": state, "questions": questions}
        deadline = time.monotonic() + self.timeout
        last = "no attempt made"
        for attempt in range(self.attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            t0 = time.perf_counter()
            try:
                r = await self.http.post(self.url, json=body, timeout=remaining)
            except httpx.TransportError as e:
                last = type(e).__name__
            else:
                data = _json(r)
                if r.status_code == 200 and "answers" in data:
                    return self._record(state, questions, data, time.perf_counter() - t0)
                detail = _detail(data) or r.text[:200]
                if r.status_code in FATAL:
                    raise JevFatal(f"{self.backend.name} said {r.status_code}: {detail}")
                if r.status_code != 200 and r.status_code not in RETRYABLE:
                    raise JevError(f"HTTP {r.status_code}: {detail}")
                last = f"HTTP {r.status_code}"
            if attempt + 1 < self.attempts:
                self.meter.retries += 1
                pause = 0.2 * 2 ** attempt + random.random() * 0.1
                await asyncio.sleep(max(0.0, min(pause, deadline - time.monotonic())))
        raise JevError(f"gave up after {self.timeout:g}s ({last})")

    def _record(self, state, questions: dict, data: dict, seconds: float) -> dict[str, dict]:
        usage = data.get("usage") or {}
        tokens = usage.get("input_tokens") or 0
        cost = usage.get("cost")
        self.meter.calls += 1
        self.meter.input_tokens += tokens
        self.meter.cost += tokens * PRICE_PER_MTOK / 1e6 if cost is None else cost
        self.meter.latencies.append(seconds)
        self.meter.model = data.get("model") or self.model
        out = {}
        for qid, q in questions.items():
            if qid not in data["answers"]:
                raise JevError(f"no answer returned for question {qid!r}")
            k = Cache.key(self.model, state, q)
            out[k] = data["answers"][qid]
            if self.cache:
                self.cache.put(k, out[k])
        return out


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _detail(data: dict) -> str:
    """The human-readable part of an error body. OpenRouter nests it under `error`, TypeSafe under `detail`,
    and `detail` may itself be a string, an object or a list of validation problems."""
    found = data.get("error", data.get("detail"))
    if isinstance(found, list):
        found = "; ".join(_detail({"detail": item}) for item in found)
    elif isinstance(found, dict):
        found = found.get("message") or found.get("msg") or json.dumps(found)
    return " ".join(str(found or "").split())[:200]
