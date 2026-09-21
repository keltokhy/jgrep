"""The function around a diff hunk, read from Git or the working tree; this module never calls a model."""

from __future__ import annotations

import bisect
import subprocess
from pathlib import Path

from .inputs import FUNCTION_LANGUAGES, Record

# Why a hunk is judged alone. Under --function-context every hunk gets a context or one of these.
FALLBACKS = {
    "deleted_file": "the file does not exist after the change",
    "deletion_only": "the hunk leaves no new-side lines to locate or check against the file",
    "unsupported_language": "no function reader for this file extension",
    "parser_unavailable": "the language needs the code extra: uv tool install --reinstall 'jev-grep[code]'",
    "source_unavailable": "the new-side file could not be read from the commit or working tree",
    "source_mismatch": "the file that was read does not contain the hunk's new-side lines at that place",
    "syntax_error": "the file, or the code around the change, did not parse",
    "outside_function": "the changed lines are not inside a function",
    "function_in_hunk": "the hunk already contains the whole function",
}


class FunctionContext:
    """Attach to each hunk the new-side function(s) that enclose its changed lines."""

    def __init__(self, repo: str | None, max_chars: int):
        from . import code_inputs  # imported here so line mode never loads the diff and syntax parsers
        self.code, self.repo, self.max_chars = code_inputs, repo or ".", max_chars
        self._objects = None  # one `git cat-file --batch` for the run; a process per file is slow
        self._root = None
        self._loaded = (None, None)  # the hunks of one file arrive together

    def close(self) -> None:
        if self._objects is not None:
            try:
                self._objects.stdin.close()
            except OSError:
                pass  # Git had already exited, as it does outside a repository
            self._objects.stdout.close()
            self._objects.wait()

    def attach(self, rec: Record) -> None:
        found = self._find(rec.unit, rec.text)
        if isinstance(found, str):
            rec.unit["context"] = {"fallback": found}
        else:
            rec.context, rec.unit["context"] = found

    def _blob(self, commit: str, path: str) -> str | None:
        """What `git show <commit>:<path>` prints, or None when Git has no such file."""
        if "\n" in path:
            return None
        try:
            if self._objects is None:
                self._objects = subprocess.Popen(["git", "-C", self.repo, "cat-file", "--batch"],
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.DEVNULL)
            self._objects.stdin.write(f"{commit}:{path}\n".encode("utf-8"))
            self._objects.stdin.flush()
            header = self._objects.stdout.readline().split()
            if len(header) != 3 or not header[2].isdigit():
                return None  # "<name> missing", or Git cannot run in this directory
            data = self._objects.stdout.read(int(header[2]) + 1)[:int(header[2])]
            return data.decode("utf-8", errors="replace") if header[1] == b"blob" else None
        except OSError:
            return None

    def _worktree(self, path: str) -> str | None:
        if self._root is None:
            try:
                top = subprocess.run(["git", "-C", self.repo, "rev-parse", "--show-toplevel"],
                                     capture_output=True, text=True).stdout.strip()
            except OSError:
                top = ""
            self._root = Path(top or self.repo).resolve()  # patch paths are relative to the repository root
        try:
            target = (self._root / path).resolve()
            # A patch is untrusted input; it must not pull files outside the tree into a request.
            if not target.is_relative_to(self._root):
                return None
            with open(target, "r", encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except (OSError, ValueError):
            return None

    def _load(self, commit: str | None, path: str, language: str):
        text = self._blob(commit, path) if commit else self._worktree(path)
        if text is None:
            return "source_unavailable"
        try:
            functions, problems = self.code.parse_functions(text, path, language)
        except self.code.ParserUnavailable:
            return "parser_unavailable"
        except (ValueError, SyntaxError, RecursionError):
            return "syntax_error"
        # Git numbers lines at LF only, while Python's parser also counts a lone CR. Exact
        # character offsets give every language the patch's numbering.
        lines = list(self.code._lf_lines(text))
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line))
        spans = [(bisect.bisect_right(starts, f.start), bisect.bisect_right(starts, f.end - 1), f.unit["symbol"])
                 for f in functions]
        return lines, spans, [(first, last) for first, last, _ in problems]

    def _find(self, unit: dict, hunk: str):
        path, new_start = unit["new_file"], unit["new_start"]
        if path is None:
            return "deleted_file"
        if not unit["new_count"]:
            return "deletion_only"
        language = FUNCTION_LANGUAGES.get(Path(path).suffix)
        if language is None:
            return "unsupported_language"
        if self._loaded[0] != (unit["commit"], path):
            self._loaded = ((unit["commit"], path), self._load(unit["commit"], path, language))
        if isinstance(self._loaded[1], str):
            return self._loaded[1]
        lines, spans, problems = self._loaded[1]

        # New-side positions of the change: added lines, and the line after each removal.
        added, removed_before, expected, number = [], [], [], new_start
        for line in list(self.code._lf_lines(hunk))[3:]:
            if line.startswith("\\"):
                continue
            if line.startswith("-"):
                removed_before.append(number)
                continue
            if line.startswith("+"):
                added.append(number)
            expected.append(line[1:].rstrip("\r\n"))
            number += 1
        actual = [line.rstrip("\r\n") for line in lines[new_start - 1:new_start - 1 + len(expected)]]
        if actual != expected:
            return "source_mismatch"  # another commit or checkout, a reversed patch, or an edited file

        def touches(first: int, last: int) -> bool:
            # A removal is inside a function only when the lines on both sides of it are.
            return (bisect.bisect_left(added, first) < bisect.bisect_right(added, last)
                    or bisect.bisect_right(removed_before, first) < bisect.bisect_right(removed_before, last))

        if any(touches(first, last) for first, last in problems):
            return "syntax_error"
        enclosing = [span for span in spans if touches(span[0], span[1])]
        if not enclosing:
            return "outside_function"
        first, last = enclosing[0][0], enclosing[-1][1]
        if new_start <= first and last < new_start + unit["new_count"]:
            return "function_in_hunk"

        low, high = first, last
        if sum(len(line) for line in lines[first - 1:last]) > self.max_chars:
            # The function is context, not the judged unit, so it may be shortened: keep whole
            # lines nearest the change, as -C keeps the lines nearest a record.
            changed = added + removed_before
            low = high = min(max(min(changed), first), last)
            reach = min(max(changed), last)
            used = len(lines[low - 1])
            while high < reach and used + len(lines[high]) <= self.max_chars:
                used += len(lines[high])
                high += 1
            grew = True
            while grew:
                grew = False
                if low > first and used + len(lines[low - 2]) <= self.max_chars:
                    used += len(lines[low - 2])
                    low -= 1
                    grew = True
                if high < last and used + len(lines[high]) <= self.max_chars:
                    used += len(lines[high])
                    high += 1
                    grew = True
        body = "".join(lines[low - 1:high])
        shown = "" if (low, high) == (first, last) else f", showing lines {low}-{high} nearest the change"
        noun = "function" if len(enclosing) == 1 else "functions"
        header = f"Enclosing {noun} after this change ({path} lines {first}-{last}{shown}), shown only as context:\n"
        # The final cut only matters when a single line is longer than the limit.
        return header + body[:self.max_chars], {
            "symbols": [symbol for _, _, symbol in enclosing], "language": language,
            "line": first, "end_line": last, "shown_line": low, "shown_end_line": high,
            "truncated": (low, high) != (first, last) or len(body) > self.max_chars}


def tally(counts: dict, rec: Record) -> None:
    """Count a hunk's context outcome into {"with_context", "truncated", "fallbacks"}."""
    info = (rec.unit or {}).get("context")
    if info is None:
        return
    if "fallback" in info:
        fallbacks = counts.setdefault("fallbacks", {})
        fallbacks[info["fallback"]] = fallbacks.get(info["fallback"], 0) + 1
    else:
        counts["with_context"] = counts.get("with_context", 0) + 1
        counts["truncated"] = counts.get("truncated", 0) + bool(info["truncated"])


def summary(counts: dict) -> dict:
    fallbacks = dict(sorted(counts.get("fallbacks", {}).items(), key=lambda item: (-item[1], item[0])))
    return {"records_with_context": counts.get("with_context", 0),
            "truncated_contexts": counts.get("truncated", 0),
            "records_without_context": sum(fallbacks.values()), "fallbacks": fallbacks}


def describe(counts: dict, always: bool = False) -> str | None:
    """One line of totals; by default only when a hunk was judged alone or a context was shortened."""
    report = summary(counts)
    alone, total = report["records_without_context"], report["records_with_context"] + report["records_without_context"]
    if not (always or alone or report["truncated_contexts"]):
        return None
    parts = [f"function context for {report['records_with_context']:,} of {total:,} hunks"]
    if alone:
        reasons = ", ".join(f"{n:,} {reason}" for reason, n in report["fallbacks"].items())
        parts.append(f"{alone:,} judged alone ({reasons})")
    if report["truncated_contexts"]:
        parts.append(f"{report['truncated_contexts']:,} contexts shortened to --max-chars")
    return "; ".join(parts)
