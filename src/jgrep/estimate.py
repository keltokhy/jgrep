"""Offline cost preview: the runtime's plan for each record, read from the cache without changing it."""

from __future__ import annotations

import json
import math
import sqlite3

from jevkit_runtime import AnswerStore, Budget, Client, Settings, estimate_tokens, request_body, resolve

from .core import PROVIDERS
from .diff_context import describe, summary, tally


def preview_client(args) -> Client:
    """The client a run would use, without needing its key, over a read-only cache: a preview costs nothing."""
    settings = Settings.from_env()
    backend = resolve(PROVIDERS, args.api, model=args.model, require_key=False, settings=settings)
    store = None if args.no_cache else AnswerStore(settings=settings, read_only=True)
    return Client(backend, store=store)


def estimate(stream, args, questions, make_state, function_questions, budget: Budget):
    client = preview_client(args)
    backend = client.backend
    seen = sqlite3.connect("")  # keys already counted, on a temporary disk database: bounded Python memory
    seen.execute("CREATE TABLE seen (key TEXT PRIMARY KEY) WITHOUT ROWID")
    result = {"schema_version": 1, "operation": "estimate", "api": backend.name, "model": backend.model,
              "records": 0, "blank_records": 0, "cached_records": 0, "duplicate_records": 0,
              "estimated_calls": 0, "call_upper_bound": 0, "estimated_input_tokens": 0,
              "input_bytes_plus_overhead": 0, "truncated_records": 0, "errors": [],
              "price_per_million_input_tokens_usd": backend.price_per_mtok,
              "notes": ["No API calls. Reads input to EOF; do not use with an endless stream.",
                        "Estimates assume successful calls and cache reuse; retries and concurrent misses can cost more.",
                        "Token estimate: UTF-8 request bytes / 4 plus 270 per request. Byte estimate adds 1024 overhead.",
                        "Both use the configured input-token list price, not a provider quote or billing cap.",
                        "Scans all records regardless of -p, -q, -l, -m or the dollar budget."]}
    contexts: dict = {}
    try:
        for rec in stream:
            if isinstance(rec, str):
                result["errors"].append(rec)
                continue
            result["records"] += 1
            tally(contexts, rec)
            if not rec.text.strip():
                result["blank_records"] += 1
                continue
            if not (args.chunks or args.diff or args.functions) and any(
                    len(t) > args.max_chars for t in (rec.text, *rec.before, *rec.after)):
                result["truncated_records"] += 1
            # The enclosing function is part of the request, so it is part of the price.
            asked = function_questions[bool(rec.unit["commit"])] if rec.context else questions
            plan = client.plan(make_state(rec, args), asked)
            if plan.complete:
                result["cached_records"] += 1
                continue
            result["call_upper_bound"] += 1
            # Without the cache a repeated record is asked again; with it, once.
            fresh = {qid: q for qid, q in plan.misses.items()
                     if seen.execute("INSERT OR IGNORE INTO seen VALUES (?)", (plan.keys[qid],)).rowcount
                     or args.no_cache}
            if not fresh:
                result["duplicate_records"] += 1
                continue
            result["estimated_calls"] += 1
            body = request_body(backend.model, plan.state, fresh)
            result["estimated_input_tokens"] += estimate_tokens(body)
            result["input_bytes_plus_overhead"] += len(json.dumps(body, ensure_ascii=False).encode()) + 1024
        if args.function_context:
            result["function_context"] = summary(contexts)
            result["notes"].append(f"The estimate includes {describe(contexts, always=True)}.")
        result["estimated_cost_usd"] = result["estimated_input_tokens"] * backend.price_per_mtok / 1e6
        result["byte_estimate_cost_usd"] = result["input_bytes_plus_overhead"] * backend.price_per_mtok / 1e6
        limit = None if math.isinf(budget.limit) else budget.limit
        result["budget_usd"] = limit
        result["estimate_exceeds_budget"] = limit is not None and result["estimated_cost_usd"] > limit
        return result
    finally:
        seen.close()
        if client.store is not None:
            client.store.close()
