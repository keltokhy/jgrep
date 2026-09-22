"""Offline cost preview. Read existing answers without creating or modifying the cache."""

from __future__ import annotations

import json
import math
import sqlite3

from jevkit_core import answer_key

from .core import PROVIDERS, Backend, Cache, Settings
from .diff_context import describe, summary, tally


def preview_backend(args):
    settings = Settings.from_env()
    name = args.api or settings.api
    if name is None:
        name = next((provider.name for provider in PROVIDERS.values()
                     if settings.environ.get(provider.key_env) or provider.key_file(settings).is_file()),
                    "typesafe")
    if name not in PROVIDERS:
        raise ValueError(f"unknown API {name!r}")
    provider = PROVIDERS[name]
    model = args.model or settings.model or provider.model
    price = settings.price_per_mtok if provider.price_per_mtok is None else provider.price_per_mtok
    backend = Backend(provider.name, provider.endpoint(settings) or "", model, price_per_mtok=price)
    return backend, settings


def estimate(stream, args, questions, make_state, function_questions=None):
    backend, settings = preview_backend(args)
    price_per_mtok = backend.price_per_mtok
    path = Cache.default_path(settings)
    cache = None
    contexts = {}
    seen = sqlite3.connect("")  # temporary disk database, bounded Python memory
    seen.execute("CREATE TABLE seen (key TEXT PRIMARY KEY) WITHOUT ROWID")
    result = {"schema_version": 1, "operation": "estimate", "api": backend.name, "model": backend.model,
              "records": 0, "blank_records": 0, "cached_records": 0, "duplicate_records": 0,
              "estimated_calls": 0, "call_upper_bound": 0, "estimated_input_tokens": 0,
              "input_bytes_plus_overhead": 0, "truncated_records": 0, "errors": [],
              "price_per_million_input_tokens_usd": price_per_mtok,
              "notes": ["No API calls. Reads input to EOF; do not use with an endless stream.",
                        "Estimates assume successful calls and cache reuse; retries and concurrent misses can cost more.",
                        "Token estimate: UTF-8 request bytes / 4 plus 270 per request. Byte estimate adds 1024 overhead.",
                        "Both use the configured input-token list price, not a provider quote or billing cap.",
                        "Scans all records regardless of -p, -q, -l, -m or the dollar budget."]}
    try:
        if not args.no_cache and path.exists():
            cache = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
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
            state = make_state(rec, args)
            missing = {}
            fresh = {}
            # The enclosing function is part of the request, so it is part of the price.
            asked = function_questions[bool(rec.unit["commit"])] if rec.context else questions
            for qid, q in asked.items():
                key = answer_key(backend, state, q)
                row = cache.execute("SELECT answer FROM answers WHERE key=?", (key,)).fetchone() if cache else None
                if row:
                    try:
                        p = json.loads(row[0])["noul"]
                        if not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1:
                            continue
                    except (ValueError, KeyError, TypeError):
                        pass
                missing[qid] = q
                inserted = seen.execute("INSERT OR IGNORE INTO seen VALUES (?)", (key,)).rowcount
                if args.no_cache or inserted:
                    fresh[qid] = q
            if not missing:
                result["cached_records"] += 1
                continue
            result["call_upper_bound"] += 1
            if not fresh:
                result["duplicate_records"] += 1
                continue
            result["estimated_calls"] += 1
            payload = json.dumps({"model": backend.model, "state": state, "questions": fresh}, ensure_ascii=False)
            size = len(payload.encode("utf-8"))
            result["estimated_input_tokens"] += math.ceil(size / 4) + 270
            result["input_bytes_plus_overhead"] += size + 1024
        if args.function_context:
            result["function_context"] = summary(contexts)
            result["notes"].append(f"The estimate includes {describe(contexts, always=True)}.")
        result["estimated_cost_usd"] = result["estimated_input_tokens"] * price_per_mtok / 1e6
        result["byte_estimate_cost_usd"] = result["input_bytes_plus_overhead"] * price_per_mtok / 1e6
        result["budget_usd"] = args.budget or None
        result["estimate_exceeds_budget"] = bool(args.budget and result["estimated_cost_usd"] > args.budget)
        return result
    finally:
        if cache:
            cache.close()
        seen.close()
