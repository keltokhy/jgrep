"""Deterministic, source-preserving code units; this module never calls a model."""

from __future__ import annotations

import ast
import bisect
import hashlib
import importlib
import io
import re
import warnings
from pathlib import Path

from unidiff import PatchSet, UnidiffParseError

from .inputs import FUNCTION_LANGUAGES, Record

MAX_PROBLEMS_NAMED = 5


class ParserUnavailable(ValueError):
    """The optional Tree-sitter parsers are not installed."""


def git_path(value: str) -> str | None:
    """Decode Git's C-quoted UTF-8 path without interpreting it as a local path."""
    if value == "/dev/null":
        return None
    if value.startswith('"') and value.endswith('"'):
        try:
            # Git uses octal escapes for bytes, but core.quotePath=false leaves UTF-8 raw.
            # Escape only those raw bytes before evaluating the byte literal; existing escapes stay.
            literal = "".join(f"\\{byte:03o}" if byte >= 128 else chr(byte) for byte in value.encode("utf-8"))
            value = ast.literal_eval("b" + literal).decode("utf-8")
        except (ValueError, SyntaxError, UnicodeError) as e:
            raise ValueError(f"malformed quoted path {value}: {e}") from e
    return value[2:] if value.startswith(("a/", "b/")) else value


# Default `git log -p` / `git show` headers, and the mbox separator written by `git format-patch`.
# Git abbreviates ids to as few as four hex digits (--abbrev=4); in `git log -p` a message that
# might also read as a header is indented, so a header only matches at column zero.
_LOG_COMMIT = re.compile(r"commit ([0-9a-f]{4,64})(?: \(.*\))?$")
_MBOX_COMMIT = re.compile(r"From ([0-9a-f]{40}|[0-9a-f]{64}) \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4}$")


def _lf_lines(source):
    """Lines ending at LF only, as Git and unidiff count them.

    str.splitlines and universal-newline readers also break at form feeds and lone carriage
    returns inside a source line, which would shift every later patch line number.
    """
    source = io.StringIO(source) if isinstance(source, str) else source
    tail = ""
    while chunk := source.read(1 << 20):
        *whole, tail = (tail + chunk).split("\n")
        for line in whole:
            yield line + "\n"
    if tail:
        yield tail


def _commit_segments(lines):
    """Split a patch stream at commit headers: (commit id or None, first line number, lines, mbox).

    A hunk line starts with a space, sign or backslash, so a header at column 0 cannot be patch
    content. Unindented mbox messages can mention a commit, so the first header seen fixes which
    of the two formats separates this stream, and whether messages and signatures must be trimmed.
    """
    patterns = ((_LOG_COMMIT, False), (_MBOX_COMMIT, True))
    commit, first, block, mbox = None, 1, [], False
    for number, line in enumerate(lines, 1):
        if line.startswith(("commit ", "From ")):
            for pattern, is_mbox in patterns:
                match = pattern.match(line.rstrip("\r\n"))
                if match:
                    # `git show <annotated tag>` puts tag metadata before its first commit.
                    tag = commit is None and not is_mbox and block and block[0].startswith("tag ")
                    if block and not tag:
                        yield commit, first, block, mbox
                    patterns = ((pattern, is_mbox),)
                    commit, first, block, mbox = match.group(1), number, [], is_mbox
                    break
        block.append(line)
    if block:
        yield commit, first, block, mbox


def _commit_patch_tail(lines: list[str], mbox: bool) -> list[str]:
    """Remove only a commit's trailing separator/signature, preserving physical hunk offsets."""
    end = len(lines)
    if mbox:
        old = new = 0
        for i, line in enumerate(lines):
            # A removed source line may itself be `-- `, so recognize signatures only outside hunks.
            if old <= 0 and new <= 0 and line.rstrip("\r\n") in ("-- ", "--"):
                end = i
                break
            header = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            if header:
                old, new = (int(n) if n is not None else 1 for n in header.groups())
            elif old > 0 or new > 0:
                old -= line.startswith((" ", "-"))
                new -= line.startswith((" ", "+"))
    # unidiff rejects a blank separator after a hunkless last file. A space-prefixed empty
    # context line belongs to a hunk, however, and must never be stripped here.
    while end and not lines[end - 1].rstrip("\r\n"):
        end -= 1
    return lines[:end]


def diff_records(source, label: str):
    """One complete unified hunk, retaining both sides and its unchanged context.

    `source` is patch text or a text file. `git log -p` and `git format-patch` streams are read one
    commit at a time, and each hunk carries its commit id. Line numbers locate hunks in the input.
    """
    for commit, first, lines, mbox in _commit_segments(_lf_lines(source)):
        if commit is None:
            yield from _patch_records(lines, first - 1, label, None)
            continue
        # A mail's message can itself contain "diff --git", so the patch begins after the last "---"
        # separator, which Git writes between the message and the diffstat. `git log -p` indents its
        # message, so there the first column-zero "diff --" is the patch. A merge may have no patch.
        start = max((i + 1 for i, line in enumerate(lines) if mbox and line.rstrip("\r\n") == "---"), default=0)
        body = next((i for i in range(start, len(lines)) if lines[i].startswith("diff --")), len(lines))
        try:
            patch_lines = _commit_patch_tail(lines[body:], mbox)
            yield from _patch_records(patch_lines, first - 1 + body, label, commit, signature=mbox)
        except (ValueError, SyntaxError, UnicodeError) as e:  # a bad path or hunk fails its commit only
            yield f"{label}:{first}: commit {commit[:12]}: {e}"  # later commits are still read


def _patch_records(lines: list[str], offset: int, label: str, commit: str | None, signature: bool = False):
    text = "".join(lines)
    if not text.strip():
        return
    if any(line.startswith(("@@@", "diff --cc ", "diff --combined ")) for line in lines):
        raise ValueError("combined merge diffs are unsupported; use git diff --diff-merges=separate")
    try:
        patch = PatchSet(lines)
    except (UnidiffParseError, UnboundLocalError, AttributeError) as e:
        raise ValueError(f"invalid unified diff: {e}") from e
    if not patch:
        raise ValueError("expected a unified diff (git diff --no-color), not ordinary text")
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
        # A mail ends with git's "-- " signature delimiter; the version line below it is not a hunk.
        if signature and line.rstrip("\r\n") in ("-- ", "--"):
            break
        if line.startswith("--- "):
            old_headers += 1
        elif line.startswith("+++ "):
            new_headers += 1
        elif line.startswith(("+", "-", " ")) and line.strip() and line != "-- \n":
            raise ValueError(f"unexpected diff content outside a hunk at line {number + offset}")
    if old_headers != new_headers:
        raise ValueError("unpaired source/target file headers")
    where = label if commit is None else f"{label}: commit {commit[:12]}"
    for file in patch:
        if file.is_binary_file:
            yield f"{where}: binary change {file.path!r} cannot be judged as a text diff"
            continue
        if not file:
            yield f"{where}: metadata-only change {file.path!r} has no text hunks to judge"
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
            unit = {"kind": "diff", "hunk": number, "commit": commit, "old_file": old, "new_file": new,
                    "old_start": hunk.source_start, "old_count": hunk.source_length,
                    "new_start": hunk.target_start, "new_count": hunk.target_length}
            # Repeating the file headers makes every hunk independently interpretable.
            excerpt = f"--- {file.source_file}\n+++ {file.target_file}\n" + raw
            yield Record(0, label, first + offset, excerpt, end_line=last + offset, unit=unit)


def _python_spans(text: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # the source is data; its warnings are not jgrep's
        # A leading byte-order mark is not a statement; dropping it for the parse keeps line numbers,
        # since the mark sits on line one and columns are unused for Python spans.
        tree = ast.parse(text[1:] if text.startswith("﻿") else text)

    def visit(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            yield first, node.end_lineno, scope + node.name, 0, None
            return  # nested functions remain in their enclosing function's record
        if isinstance(node, ast.ClassDef):
            scope += node.name + "."
        for child in ast.iter_child_nodes(node):
            yield from visit(child, scope)

    yield from visit(tree)


def _syntax_tree(text: str, grammar: str, title: str):
    try:
        module = importlib.import_module(grammar)
        from tree_sitter import Language, Parser
    except ImportError as e:
        raise ParserUnavailable(f"{title} functions require the code extra: uv tool install --reinstall 'jev-grep[code]'") from e
    encoded = text.encode("utf-8")
    return encoded, Parser(Language(module.language())).parse(encoded).root_node


def _with_comments(node, encoded: bytes):
    """Start of a declaration, moved up over comment lines that sit directly above it."""
    start = node.start_point
    previous = node.prev_named_sibling
    while (previous and previous.type == "comment" and previous.end_point.row + 1 == start.row
           and not encoded[previous.start_byte - previous.start_point.column:previous.start_byte].strip()):
        start = previous.start_point
        previous = previous.prev_named_sibling
    return start


def _go_spans(text: str):
    encoded, root = _syntax_tree(text, "tree_sitter_go", "Go")
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
        start = _with_comments(node, encoded)
        yield start.row + 1, node.end_point.row + 1, symbol, start.column, node.end_point.column


# Nodes that hold top-level C items. Both branches of an #if are source text, so both are read.
_C_CONTAINERS = {"translation_unit", "preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif",
                 "preproc_elifdef", "linkage_specification", "declaration_list"}
# Children of an ERROR node that the parser still recovered whole; anything else there is unplaced.
_C_RECOVERED = {"comment", "declaration", "type_definition", "function_definition",
                "linkage_specification", "ERROR"}


def _c_spans(text: str):
    """Functions whose own syntax tree is error-free, and the line ranges that were skipped.

    C is parsed before preprocessing, so a macro or an #if that splits a statement leaves ERROR
    nodes in most real files. Failing the file would make the reader useless; emitting a function
    around an error would guess at its boundaries. So a function is emitted only when the parser
    read all of it, and every other range that could hold a function body is reported.
    """
    encoded, root = _syntax_tree(text, "tree_sitter_c", "C")
    lines = encoded.split(b"\n")
    spans, skipped, unplaced = [], [], set()

    def symbol(node):
        declarator = node.child_by_field_name("declarator")
        while declarator is not None and declarator.type != "identifier":
            # Pointer, function and array declarators nest; a parenthesized one has no field name.
            declarator = declarator.child_by_field_name("declarator") or next(
                (c for c in declarator.named_children if c.type == "identifier" or c.type.endswith("declarator")), None)
        return None if declarator is None else encoded[declarator.start_byte:declarator.end_byte].decode("utf-8")

    def visit(node, everywhere=False, in_function=False):
        for child in node.children:
            if child.type == "function_definition":
                name = symbol(child)
                if name and not child.has_error:
                    start = _with_comments(child, encoded)
                    spans.append((start.row + 1, child.end_point.row + 1, name, start.column, child.end_point.column))
                    continue  # nested functions remain in their enclosing function's record
                found = len(spans)
                # An unbalanced brace can swallow later functions; those are still complete.
                visit(child, True, True)
                last = spans[found][0] - 1 if len(spans) > found else child.end_point.row + 1
                if not in_function:
                    first = child.start_point.row + 1
                    skipped.append((first, last, f"lines {first}-{last}" if last > first else f"line {first}"))
                continue
            if child.type == "compound_statement" and not in_function:
                # A macro-headed body can parse as a call with a missing semicolon followed by a
                # bare block, without an ERROR node. Report the whole region that nobody read.
                before = child.prev_sibling
                first = before.start_point.row if before is not None and before.has_error else child.start_point.row
                unplaced.update(range(first, child.end_point.row + 1))
                continue
            if (node.type == "ERROR" and not in_function and child.type not in _C_RECOVERED
                    and not child.type.startswith("preproc_")):
                unplaced.update(range(child.start_point.row, child.end_point.row + 1))
            if child.has_error or everywhere or child.type in _C_CONTAINERS:
                visit(child, everywhere or child.type == "ERROR", in_function)

    visit(root)
    starts = sorted(first - 1 for first, *_ in [*spans, *skipped])
    for first, last, *_ in [*spans, *skipped]:
        unplaced.difference_update(range(first - 1, last))
    ranges = []
    for row in sorted(unplaced):
        # Declarations and comments recovered inside a broken function stay part of its region;
        # only another function separates one unparsed region from the next.
        between = bisect.bisect_right(starts, ranges[-1][1]) if ranges else 0
        if ranges and (between == len(starts) or starts[between] > row):
            ranges[-1][1] = row
        else:
            ranges.append([row, row])
    # Without a parameter list and a brace there is no function to lose: a prototype carrying an
    # unknown macro, or an extern "C" guard, is not worth an error.
    problems = skipped + [(a + 1, b + 1, f"lines {a + 1}-{b + 1}" if b > a else f"line {a + 1}")
                          for a, b in ranges if all(mark in b"".join(lines[a:b + 1]) for mark in (b"(", b"{"))]
    return spans, sorted(problems)


def parse_functions(text: str, label: str, language: str | None = None):
    """Function records, plus (first line, last line, description) for C ranges that were skipped."""
    language = language or FUNCTION_LANGUAGES.get(Path(label).suffix)
    if language not in set(FUNCTION_LANGUAGES.values()):
        raise ValueError("--functions supports .py, .go, .c and .h; use --lang python|go|c for stdin")
    # Python recognizes CR, CRLF and LF; Tree-sitter counts LF only. Neither
    # treats form feeds or Unicode separators inside source text as new lines.
    lines = list(io.StringIO(text, newline="" if language == "python" else "\n"))
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    problems = []
    if language == "python":
        spans = _python_spans(text)
    elif language == "go":
        spans = _go_spans(text)
    else:
        spans, problems = _c_spans(text)
    found = []
    for first, last, symbol, first_col, last_col in spans:
        if language == "python":
            while first > 1 and lines[first - 2].lstrip().startswith("#"):
                first -= 1
        # Parser columns count UTF-8 bytes; exported offsets count characters.
        prefix = lines[first - 1].encode("utf-8")[:first_col].decode("utf-8")
        start = offsets[first - 1] + (len(prefix) if prefix.strip() else 0)
        end = offsets[last]
        if last_col is not None:
            body = lines[last - 1].encode("utf-8")[:last_col].decode("utf-8")
            if lines[last - 1][len(body):].strip():
                end = offsets[last - 1] + len(body)
        found.append(Record(0, label, first, text[start:end], start=start, end=end, end_line=last,
                            unit={"kind": "function", "language": language, "symbol": symbol}))
    return found, problems


def function_records(text: str, label: str, language: str | None = None):
    found, problems = parse_functions(text, label, language)
    # Report skipped C before its file's functions: a match limit, quiet mode or a budget stop can
    # end the run once a function is emitted, and unreviewed code must not be lost with it.
    if problems:
        named = ", ".join(p[2] for p in problems[:MAX_PROBLEMS_NAMED])
        more = f" and {len(problems) - MAX_PROBLEMS_NAMED} more" if len(problems) > MAX_PROBLEMS_NAMED else ""
        yield (f"{label}: skipped C that Tree-sitter could not parse, usually around a macro or #if: "
               f"{named}{more}. Other functions were read")
    yield from found


def code_records(source, label: str, args, stop):
    context = None
    if args.function_context:
        from .diff_context import FunctionContext
        context = FunctionContext(args.repo, args.max_chars)
    try:
        stream = diff_records(source, label) if args.diff else function_records(source.read(), label, args.lang)
        for item in stream:
            if stop.is_set():
                return
            if isinstance(item, Record) and len(item.text) > args.max_chars:
                yield (f"{label}:{item.lineno}: complete {item.unit['kind']} is {len(item.text)} characters; "
                       f"exceeds --max-chars {args.max_chars}. Raise the limit; code units are never truncated")
                continue
            if context and isinstance(item, Record):
                context.attach(item)
            yield item
    except (ValueError, SyntaxError, UnicodeError) as e:
        yield f"{label}: {e}"
    finally:
        if context:
            context.close()


def export_record(rec: Record) -> dict:
    """Plain JSONL records; IDs retain locations when another tool selects only text."""
    digest = hashlib.sha256(rec.text.encode("utf-8")).hexdigest()[:16]
    location = f"{rec.file}:{rec.lineno}-{rec.end_line or rec.lineno}"
    if rec.unit and rec.unit["kind"] == "diff":
        d = rec.unit
        commit = f"@{d['commit'][:12]}" if d["commit"] else ""
        location += f":{d['new_file'] or d['old_file']}{commit}:-{d['old_start']}+{d['new_start']}"
    row = {"schema_version": 1, "id": f"{location}:{digest}", "text": rec.text,
           "source": rec.file, "line": rec.lineno, "end_line": rec.end_line,
           "start": rec.start, "end": rec.end, "unit": rec.unit}
    if rec.unit and "context" in rec.unit:
        row["context"] = rec.context  # what the judge is shown after the hunk; null when judged alone
    return row
