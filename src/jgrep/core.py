"""jgrep compatibility adapter for the shared JevKit implementation.

Prompts, cache identity, answer reuse, and budget policy retain their existing contracts.
Transport, configuration, storage, validation, and usage parsing come from jevkit_core.
"""

from __future__ import annotations

from jevkit_core import (
    FATAL,
    RETRYABLE,
    PRICE_PER_MTOK,
    AnswerCache,
    Backend as _Backend,
    DecisionClient,
    JevBudgetExceeded,
    JevError,
    JevFatal,
    Meter as _Meter,
    backend_catalog,
    cache_path,
    config_dir,
    digest,
    parse_usage,
    resolve_backend as _resolve_backend,
    validate_answer as _validate_answer,
)


Backend = _Backend


BACKENDS = backend_catalog("typesafe", "openrouter", "gateway")


def resolve_backend(name: str | None = None):
    return _resolve_backend(BACKENDS, name)


class Cache(AnswerCache):
    """Keep the existing cache identity while sharing its storage implementation."""

    metadata = False

    @staticmethod
    def key(model: str, state, question: dict, *, api: str, url: str) -> str:
        return digest([api, url, model, state, question])


Meter = _Meter


class Jev(DecisionClient):
    def __init__(
        self,
        key: str,
        backend: Backend | str = "openrouter",
        *,
        model: str | None = None,
        timeout: float = 15.0,
        attempts: int = 4,
        concurrency: int = 32,
        cache: Cache | None = None,
        transport=None,
    ):
        backend = BACKENDS[backend] if isinstance(backend, str) else backend
        meter = Meter()
        super().__init__(
            key,
            backend,
            model=model,
            timeout=timeout,
            attempts=attempts,
            concurrency=concurrency,
            cache=cache,
            transport=transport,
            meter=meter,
        )

    async def ask(self, state, questions: dict[str, dict]) -> dict[str, dict]:
        """Answer questions missing from the cache, preserving the existing per-question identity."""
        keys = {
            qid: Cache.key(self.model, state, q, api=self.backend.name, url=self.url)
            for qid, q in questions.items()
        }
        answers = {}
        if self.cache:
            for qid, k in keys.items():
                if (hit := self.cache.get(k)) is not None:
                    _validate_answer(qid, questions[qid], hit)
                    answers[qid] = hit
        misses = {qid: q for qid, q in questions.items() if qid not in answers}
        if not misses:
            self.meter.cached += 1
            return answers

        task, _ = self.share_request(
            (keys[qid] for qid in misses), lambda: self._call(state, misses)
        )
        by_key = await task
        return answers | {qid: by_key[keys[qid]] for qid in misses}

    def _record(
        self, state, questions: dict, data: dict, seconds: float
    ) -> dict[str, dict]:
        usage = parse_usage(
            data.get("usage"), price_per_mtok=self.backend.price_per_mtok
        )
        self.meter.record(usage, seconds, model=data.get("model") or self.model)
        answers = data["answers"]
        if not isinstance(answers, dict):
            raise JevError("invalid answers returned: expected an object")
        out = {}
        keys = {
            qid: Cache.key(self.model, state, q, api=self.backend.name, url=self.url)
            for qid, q in questions.items()
        }
        for qid, q in questions.items():
            if qid not in answers:
                raise JevError(f"no answer returned for question {qid!r}")
            _validate_answer(qid, q, answers[qid])
            out[keys[qid]] = answers[qid]
        # Validate the entire response before storing any part of it.
        if self.cache:
            for k, answer in out.items():
                self.cache.put(k, answer)
        return out


__all__ = [
    "BACKENDS",
    "Backend",
    "Cache",
    "Meter",
    "Jev",
    "JevError",
    "JevFatal",
    "JevBudgetExceeded",
    "PRICE_PER_MTOK",
    "RETRYABLE",
    "FATAL",
    "cache_path",
    "config_dir",
    "resolve_backend",
]
