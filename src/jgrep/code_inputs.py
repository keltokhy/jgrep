"""Deterministic, source-preserving code units; this module never calls a model."""

from __future__ import annotations

import ast
import hashlib
import io
from pathlib import Path

from unidiff import PatchSet, UnidiffParseError

from .inputs import Record


def git_path(value: str) -> str | None:
    """Decode Git's C-quoted UTF-8 path without interpreting it as a local path."""
    if value == "/dev/null":
        return None
    if value.startswith('"') and value.endswith('"'):
        # Git quotes non-ASCII bytes as octal escapes, unlike JSON.
        value = ast.literal_eval("b" + value).decode("utf-8")
    return value[2:] if value.startswith(("a/", "b/")) else value


def diff_records(text: str, label: str):
    """One complete unified hunk, retaining both sides and its unchanged context."""
    if not text.strip():
        return
    if any(line.startswith(("@@@", "diff --cc ", "diff --combined ")) for line in text.splitlines()):
        raise ValueError("combined merge diffs are unsupported; use git diff --diff-merges=separate")
    try:
        patch = PatchSet(io.StringIO(text))
    except (UnidiffParseError, UnboundLocalError, AttributeError) as e:
        raise ValueError(f"invalid unified diff: {e}") from e
    if not patch:
        raise ValueError("expected a unified diff (git diff --no-color), not ordinary text")
    lines = text.splitlines(keepends=True)
    # The parser accepts some unknown lines as metadata. Reject malformed hunk headers.
    parsed_headers = sum(len(file) for file in patch)
    if sum(line.startswith("@@") for line in lines) != parsed_headers:
        raise ValueError("invalid or unsupported hunk header")
    covered = set()
    for file in patch:
        for hunk in file:
            positions = [line.diff_line_no for line in hunk if line.diff_line_no is not None]
            if positions:
                covered.update(range(positions[0] - 1, positions[-1] + 1))
    old_headers = new_headers = 0
    for number, line in enumerate(lines, 1):
        if number in covered or line.startswith("\\ No newline at end of file"):
            continue
        if line.startswith("--- "):
            old_headers += 1
        elif line.startswith("+++ "):
            new_headers += 1
        elif line.startswith(("+", "-", " ")) and line.strip() and line != "-- \n":
            raise ValueError(f"unexpected diff content outside a hunk at line {number}")
    if old_headers != new_headers:
        raise ValueError("unpaired source/target file headers")
    for file in patch:
        if file.is_binary_file:
            yield f"{label}: binary change {file.path!r} cannot be judged as a text diff"
            continue
        if not file:
            yield f"{label}: metadata-only change {file.path!r} has no text hunks to judge"
            continue
        for number, hunk in enumerate(file, 1):
            body = [line for line in hunk if line.diff_line_no is not None]
            if not body:
                raise ValueError("empty diff hunk")
            first = body[0].diff_line_no - 1  # one-based physical hunk-header line
            last = body[-1].diff_line_no
            # Include a trailing EOF marker even when unidiff assigns it no source position.
            if last < len(lines) and lines[last].startswith("\\ No newline at end of file"):
                last += 1
            raw = "".join(lines[first - 1:last])
            old, new = git_path(file.source_file), git_path(file.target_file)
            unit = {"kind": "diff", "hunk": number, "old_file": old, "new_file": new,
                    "old_start": hunk.source_start, "old_count": hunk.source_length,
                    "new_start": hunk.target_start, "new_count": hunk.target_length}
            # Repeating the file headers makes every hunk independently interpretable.
            excerpt = f"--- {file.source_file}\n+++ {file.target_file}\n" + raw
            yield Record(0, label, first, excerpt, end_line=last, unit=unit)


def _python_spans(text: str):
    tree = ast.parse(text)

    def visit(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            yield first, node.end_lineno, scope + node.name
            return  # nested functions remain in their enclosing function's record
        if isinstance(node, ast.ClassDef):
            scope += node.name + "."
        for child in ast.iter_child_nodes(node):
            yield from visit(child, scope)

    yield from visit(tree)


def _go_spans(text: str):
    try:
        import tree_sitter_go
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ValueError("Go functions require the code extra: uv tool install --reinstall 'jev-grep[code]'") from e
    encoded = text.encode("utf-8")
    root = Parser(Language(tree_sitter_go.language())).parse(encoded).root_node
    if root.has_error:
        raise ValueError("invalid Go syntax; refusing partial function extraction")
    for node in root.named_children:
        if node.type not in {"function_declaration", "method_declaration"}:
            continue
        name = node.child_by_field_name("name")
        symbol = encoded[name.start_byte:name.end_byte].decode("utf-8")
        receiver = node.child_by_field_name("receiver")
        if receiver:
            symbol = encoded[receiver.start_byte:receiver.end_byte].decode("utf-8") + "." + symbol
        start = node.start_point.row + 1
        previous = node.prev_named_sibling
        while previous and previous.type == "comment" and previous.end_point.row + 2 == start:
            start = previous.start_point.row + 1
            previous = previous.prev_named_sibling
        yield start, node.end_point.row + 1, symbol


def function_records(text: str, label: str, language: str | None = None):
    language = language or {".py": "python", ".go": "go"}.get(Path(label).suffix)
    if language not in {"python", "go"}:
        raise ValueError("--functions supports .py and .go; use --lang python|go for stdin")
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = _python_spans(text) if language == "python" else _go_spans(text)
    for first, last, symbol in spans:
        if language == "python":
            while first > 1 and lines[first - 2].lstrip().startswith("#"):
                first -= 1
        start, end = offsets[first - 1], offsets[last]
        yield Record(0, label, first, text[start:end], start=start, end=end, end_line=last,
                     unit={"kind": "function", "language": language, "symbol": symbol})


def code_records(text: str, label: str, args, stop):
    try:
        stream = diff_records(text, label) if args.diff else function_records(text, label, args.lang)
        for item in stream:
            if stop.is_set():
                return
            if isinstance(item, Record) and len(item.text) > args.max_chars:
                yield (f"{label}:{item.lineno}: complete {item.unit['kind']} is {len(item.text)} characters; "
                       f"exceeds --max-chars {args.max_chars}. Raise the limit; code units are never truncated")
            else:
                yield item
    except (ValueError, SyntaxError, UnicodeError) as e:
        yield f"{label}: {e}"


def export_record(rec: Record) -> dict:
    """Plain JSONL records; IDs retain locations when another tool selects only text."""
    digest = hashlib.sha256(rec.text.encode("utf-8")).hexdigest()[:16]
    location = f"{rec.file}:{rec.lineno}-{rec.end_line or rec.lineno}"
    if rec.unit and rec.unit["kind"] == "diff":
        d = rec.unit
        location += f":{d['new_file'] or d['old_file']}:-{d['old_start']}+{d['new_start']}"
    return {"schema_version": 1, "id": f"{location}:{digest}", "text": rec.text,
            "source": rec.file, "line": rec.lineno, "end_line": rec.end_line,
            "start": rec.start, "end": rec.end, "unit": rec.unit}
