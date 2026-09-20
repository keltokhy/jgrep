"""Offline cost preview. Read existing answers without creating or modifying the cache."""

from __future__ import annotations

import json
import math
import os
import sqlite3

from .core import BACKENDS, PRICE_PER_MTOK, Cache, cache_path


def preview_backend(args):
    name = args.api or os.environ.get("JEV_API")
    if name is None:
        name = next((b.name for b in BACKENDS.values()
                     if os.environ.get(b.key_env) or b.key_file.exists()), "typesafe")
    if name not in BACKENDS:
        raise ValueError(f"unknown API {name!r}")
    backend = BACKENDS[name]
    return backend, args.model or os.environ.get("JEV_MODEL") or backend.model


def estimate(stream, args, questions, make_state):
    backend, model = preview_backend(args)
    url = backend.endpoint()
    if not math.isfinite(PRICE_PER_MTOK) or PRICE_PER_MTOK < 0:
        raise ValueError("JEV_PRICE_PER_MTOK must be finite and nonnegative")
    path = cache_path()
    cache = None
    seen = sqlite3.connect("")  # temporary disk database, bounded Python memory
    seen.execute("CREATE TABLE seen (key TEXT PRIMARY KEY) WITHOUT ROWID")
    result = {"schema_version": 1, "operation": "estimate", "api": backend.name, "model": model,
              "records": 0, "blank_records": 0, "cached_records": 0, "duplicate_records": 0,
              "estimated_calls": 0, "call_upper_bound": 0, "estimated_input_tokens": 0,
              "input_bytes_plus_overhead": 0, "truncated_records": 0, "errors": [],
              "price_per_million_input_tokens_usd": PRICE_PER_MTOK,
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
            if not rec.text.strip():
                result["blank_records"] += 1
                continue
            if not (args.chunks or args.diff or args.functions) and any(
                    len(t) > args.max_chars for t in (rec.text, *rec.before, *rec.after)):
                result["truncated_records"] += 1
            state = make_state(rec, args)
            missing = {}
            fresh = {}
            for qid, q in questions.items():
                key = Cache.key(model, state, q, api=backend.name, url=url)
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
            payload = json.dumps({"model": model, "state": state, "questions": fresh}, ensure_ascii=False)
            size = len(payload.encode("utf-8"))
            result["estimated_input_tokens"] += math.ceil(size / 4) + 270
            result["input_bytes_plus_overhead"] += size + 1024
        result["estimated_cost_usd"] = result["estimated_input_tokens"] * PRICE_PER_MTOK / 1e6
        result["byte_estimate_cost_usd"] = result["input_bytes_plus_overhead"] * PRICE_PER_MTOK / 1e6
        result["budget_usd"] = args.budget or None
        result["estimate_exceeds_budget"] = bool(args.budget and result["estimated_cost_usd"] > args.budget)
        return result
    finally:
        if cache:
            cache.close()
        seen.close()
