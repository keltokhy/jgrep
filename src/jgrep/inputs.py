"""File discovery and streaming records. Each input occurrence has its own identity."""

from __future__ import annotations

import csv
import fnmatch
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

STDIN = "(standard input)"
# Extensions --functions can infer. Recursive searches skip other files unless --lang is given.
FUNCTION_LANGUAGES = {".py": "python", ".go": "go", ".c": "c", ".h": "c"}


@dataclass
class Record:
    seq: int
    file: str
    lineno: int
    text: str
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    input_id: int = 0
    original: str | None = None
    data: dict | None = None
    header: str | None = None
    chunk: int | None = None
    start: int | None = None
    end: int | None = None
    end_line: int | None = None
    unit: dict | None = None
    context: str | None = None


def discover(files: list[str], args) -> tuple[list[str], list[str]]:
    """Expand directories in stable order; explicit files bypass ignore files."""
    found, errors = [], []
    excluded = GitIgnoreSpec.from_lines(args.exclude)

    def included(relative: str) -> bool:
        return not args.glob or any(
            fnmatch.fnmatchcase(relative, pattern)
            or fnmatch.fnmatchcase(Path(relative).name, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:]))
            for pattern in args.glob)

    def load_rules(directory: Path):
        if args.no_ignore:
            return []
        rules = []
        for name in (".gitignore", ".ignore"):
            path = directory / name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError) as e:
                errors.append(f"{path}: {e}")
                continue
            rules.append((directory, GitIgnoreSpec.from_lines(lines)))
        return rules

    def ignored(path: Path, is_dir: bool, rules) -> bool:
        result = False
        for base, spec in rules:
            relative = path.relative_to(base).as_posix() + ("/" if is_dir else "")
            match = spec.check_file(relative).include
            if match is not None:
                result = match
        return result

    def walk(directory: Path, root: Path, rules):
        rules = rules + load_rules(directory)
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as e:
            errors.append(f"{directory}: {e}")
            return
        for entry in children:
            path = directory / entry.name
            try:
                if entry.is_symlink() or entry.name == ".git":
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                relative = path.relative_to(root).as_posix()
                if ignored(path, is_dir, rules) or excluded.match_file(relative + ("/" if is_dir else "")):
                    continue
                if is_dir:
                    yield from walk(path, root, rules)
                elif entry.is_file(follow_symlinks=False) and included(relative):
                    yield str(path)
            except OSError as e:
                errors.append(f"{path}: {e}")

    for name in files or (["."] if args.recursive else ["-"]):
        path = Path(name)
        if name != "-" and path.is_dir():
            if not args.recursive:
                errors.append(f"{name}: is a directory (use -r to search it)")
                continue
            # When starting inside a repository, its parent ignore files still apply.
            absolute = path.resolve()
            ancestors = []
            if not (absolute / ".git").exists():
                for parent in absolute.parents:
                    ancestors.append(parent)
                    if (parent / ".git").exists():
                        break
                else:
                    ancestors = []
            rules = [rule for parent in reversed(ancestors) for rule in load_rules(parent)]
            # Keep paths relative when the user supplied a relative directory.
            for candidate in walk(absolute, absolute, rules):
                relative = Path(candidate).relative_to(absolute)
                found.append(str(path / relative))
        elif name == "-" or (included(path.as_posix()) and not excluded.match_file(path.as_posix())):
            found.append(name)
    return found, errors


def field_value(data: dict, field: str):
    if field in data:  # allow literal keys containing dots
        return data[field]
    value = data
    for part in field.split("."):
        if isinstance(value, dict):
            value = value[part]
        elif isinstance(value, list) and part.isdecimal():
            value = value[int(part)]
        else:
            raise KeyError(field)
    return value


def field_text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def json_records(f, label: str, field: str, stop: threading.Event):
    for lineno, line in enumerate(f, 1):
        if stop.is_set():
            return
        raw = line.rstrip("\r\n")
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            value = field_value(data, field)
        except (ValueError, KeyError, IndexError) as e:
            message = f"missing field {field!r}" if isinstance(e, (KeyError, IndexError)) else str(e)
            yield f"{label}:{lineno}: {message}"
            continue
        yield Record(0, label, lineno, field_text(value), original=raw, data=data)


def csv_records(f, label: str, field: str, stop: threading.Event):
    captured = []

    def lines():
        first = True
        for line in f:
            if stop.is_set():
                return
            captured.append(line)
            # Strip a UTF-8 BOM before parsing quotes, but preserve it in output.
            yield line.lstrip("\ufeff") if first else line
            first = False

    reader = csv.reader(lines(), strict=True)
    header = next(reader, None)
    if header is None:
        return
    raw_header = "".join(captured).rstrip("\r\n")
    captured.clear()
    if len(set(header)) != len(header):
        yield f"{label}: duplicate CSV column names"
        return
    if field not in header:
        yield f"{label}: missing CSV column {field!r}"
        return
    while not stop.is_set():
        lineno = reader.line_num + 1
        row = next(reader, None)
        if row is None:
            return
        raw = "".join(captured).rstrip("\r\n")
        captured.clear()
        if not row:
            continue
        if len(row) != len(header):
            yield f"{label}:{lineno}: expected {len(header)} CSV columns, got {len(row)}"
            continue
        data = dict(zip(header, row))
        yield Record(0, label, lineno, data[field], original=raw, data=data, header=raw_header)


def chunks(f, label: str, size: int, overlap: int, stop: threading.Event):
    text = f.read(size)
    start, lineno, number = 0, 1, 1
    step = size - overlap
    while text and not stop.is_set():
        yield Record(0, label, lineno, text, chunk=number, start=start, end=start + len(text),
                     end_line=lineno + text.count("\n") - int(text.endswith("\n")))
        if stop.is_set():
            return
        more = f.read(step)
        if not more:
            return
        lineno += text[:step].count("\n")
        start += step
        number += 1
        text = text[step:] + more


def plain_records(f, label: str, args, stop: threading.Event):
    if args.diff or args.functions:
        from .code_inputs import code_records
        yield from code_records(f, label, args, stop)
        return
    if args.whole:
        # One extra character makes truncation detectable without reading the entire file.
        yield Record(0, label, 1, f.read() if args.emit_records else f.read(args.max_chars + 1))
        return
    if args.chunks:
        yield from chunks(f, label, args.chunks, args.overlap, stop)
        return
    para, start = [], 0
    for lineno, raw in enumerate(f, 1):
        if stop.is_set():
            return
        line = raw.rstrip("\r\n")
        if not args.para:
            yield Record(0, label, lineno, line)
        elif line.strip():
            start = start if para else lineno
            para.append(line)
        elif para:
            yield Record(0, label, start, "\n".join(para))
            para = []
    if para:
        yield Record(0, label, start, "\n".join(para))


def records(files: list[str], args, stop: threading.Event):
    """Yield records or error strings, continuing to the next file after read errors."""
    seq = 0
    for input_id, name in enumerate(files or ["-"]):
        if stop.is_set():
            return
        label = STDIN if name == "-" else name
        try:
            # A private reader avoids sys.stdin's lock during early process exit.
            f = (open(0, "r", encoding="utf-8", errors="replace", newline="", closefd=False) if name == "-"
                 else open(name, "r", encoding="utf-8", errors="replace", newline=""))
            with f:
                if args.recursive and name != "-" and b"\0" in f.buffer.peek(8192)[:8192]:
                    continue  # binary files discovered during a directory search
                if args.functions and args.recursive and not args.lang and Path(name).suffix not in FUNCTION_LANGUAGES:
                    continue
                if args.jsonl:
                    stream = json_records(f, label, args.field, stop)
                elif args.csv:
                    stream = csv_records(f, label, args.field, stop)
                else:
                    stream = plain_records(f, label, args, stop)
                for item in stream:
                    if isinstance(item, Record):
                        item.seq, item.input_id = seq, input_id
                        seq += 1
                    yield item
        except (OSError, UnicodeError, csv.Error) as e:
            yield f"{label}: {e}"
