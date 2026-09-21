"""The function around a diff hunk, read from Git or the working tree; this module never calls a model."""

from __future__ import annotations

import bisect
import os
import stat
import subprocess
from pathlib import Path

from .inputs import FUNCTION_LANGUAGES, Record

# A source file read for context is bounded so a patch cannot direct jgrep at a huge blob.
MAX_SOURCE_BYTES = 10 * 1024 * 1024

# A patch is untrusted input, so every Git call runs with a fixed argument list (never a shell) and
# an environment that keeps a hostile repository from reaching the network or replacing objects.
# GIT_NO_LAZY_FETCH stops cat-file from contacting a partial clone's promisor remote for an object a
# patch named; the rest disable object replacement, prompts, and background lock/monitor processes.
GIT_ENV = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_ALLOW_PROTOCOL": "file"}
GIT_FLAGS = ["-c", "core.fsmonitor=false", "-c", "protocol.ext.allow=never"]

# Why a hunk is judged alone. Under --function-context every hunk gets a context or one of these.
FALLBACKS = {
    "deleted_file": "the file does not exist after the change",
    "deletion_only": "the hunk leaves no new-side lines to locate or check against the file",
    "unsupported_language": "no function reader for this file extension",
    "parser_unavailable": "the language needs the code extra: uv tool install --reinstall 'jev-grep[code]'",
    "source_unavailable": "the new-side file could not be read from the commit or working tree",
    "source_too_large": f"the new-side file is larger than {MAX_SOURCE_BYTES // (1024 * 1024)} MiB",
    "source_mismatch": "the file that was read does not contain the hunk's new-side lines at that place",
    "syntax_error": "the file, or the code around the change, did not parse",
    "outside_function": "the changed lines are not inside a function",
    "function_in_hunk": "the hunk already contains the whole function",
}


class RepositoryError(ValueError):
    """The repository cannot be trusted to answer -W safely; the whole run stops."""


class FunctionContext:
    """Attach to each hunk the new-side function(s) that enclose its changed lines."""

    def __init__(self, repo: str | None, max_chars: int):
        from . import code_inputs  # imported here so line mode never loads the diff and syntax parsers
        self.code, self.repo, self.max_chars = code_inputs, repo or ".", max_chars
        self._objects = None  # one `git cat-file --batch` for the run; a process per file is slow
        self._root = False  # the working-tree root, computed once; None once known to be absent
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

    def _git(self, *args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", self.repo, *GIT_FLAGS, *args],
                                  capture_output=True, text=True, env=GIT_ENV)
        except OSError:
            return None
        return done.stdout if done.returncode == 0 else None

    def _read_exact(self, count: int) -> bytes:
        parts = []
        while count > 0:
            chunk = self._objects.stdout.read(count)
            if not chunk:
                break
            parts.append(chunk)
            count -= len(chunk)
        return b"".join(parts)

    def _drain(self, count: int) -> None:
        """Discard a blob's bytes in bounded pieces so the batch pipe stays in step without loading it."""
        while count > 0:
            chunk = self._objects.stdout.read(min(count, 1 << 20))
            if not chunk:
                break
            count -= len(chunk)

    def _blob(self, commit: str, path: str) -> tuple[str | None, str | None]:
        """The bytes `git show <commit>:<path>` prints as text, or (None, reason) when there is none."""
        if "\n" in path:
            return None, "source_unavailable"
        try:
            if self._objects is None:
                self._objects = subprocess.Popen(["git", "-C", self.repo, *GIT_FLAGS, "cat-file", "--batch"],
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.DEVNULL, env=GIT_ENV)
            self._objects.stdin.write(f"{commit}:{path}\n".encode("utf-8"))
            self._objects.stdin.flush()
            header = self._objects.stdout.readline().split()
            if len(header) != 3 or not header[2].isdigit():
                return None, "source_unavailable"  # "<name> missing", or Git cannot run in this directory
            size = int(header[2])
            if header[1] != b"blob" or size > MAX_SOURCE_BYTES:
                self._drain(size + 1)  # keep the pipe aligned for the next object, but do not hold it
                return None, "source_too_large" if header[1] == b"blob" else "source_unavailable"
            data = self._read_exact(size)
            self._objects.stdout.read(1)  # the trailing newline the batch format adds after content
            return data.decode("utf-8", errors="replace"), None
        except OSError:
            return None, "source_unavailable"

    def _worktree_root(self) -> Path | None:
        """The directory holding --repo's own .git, refusing a work tree that Git points elsewhere.

        The allowed root is found by walking up to the `.git` entry, without trusting repository
        config: a hostile repository can set core.worktree outside itself, and `git rev-parse
        --show-toplevel` would then report that outside directory as the tree. Any such disagreement
        stops the run rather than reading files the repository does not actually contain.
        """
        if self._root is not False:
            return self._root
        base = Path(self.repo).resolve()
        root = next((d for d in (base, *base.parents) if (d / ".git").exists()), None)
        if root is not None:
            top = self._git("rev-parse", "--show-toplevel")
            if top and top.strip():
                reported = Path(top.strip()).resolve()
                if reported != root:
                    raise RepositoryError(
                        f"-W will not read this work tree: Git reports it as {reported}, outside the "
                        f"repository at {root} (core.worktree or GIT_WORK_TREE points elsewhere). "
                        "Point --repo at a repository you trust")
        self._root = root
        return root

    def _worktree(self, path: str) -> tuple[str | None, str | None]:
        root = self._worktree_root()  # raises RepositoryError when the work tree is outside the repository
        if root is None:
            return None, "source_unavailable"
        try:
            target = (root / path).resolve()  # resolves every symlink, so a link cannot point out of the tree
            if not target.is_relative_to(root):
                return None, "source_unavailable"
            st = target.stat()
        except (OSError, ValueError):
            return None, "source_unavailable"
        if not stat.S_ISREG(st.st_mode):  # a directory, device, or named pipe whose open() could block
            return None, "source_unavailable"
        if st.st_size > MAX_SOURCE_BYTES:
            return None, "source_too_large"
        try:
            with open(target, "r", encoding="utf-8", errors="replace", newline="") as f:
                return f.read(), None
        except OSError:
            return None, "source_unavailable"

    def _parse(self, text: str, path: str, language: str):
        return self.code.parse_functions(text, path, language)

    def _load(self, commit: str | None, path: str, language: str):
        text, reason = self._blob(commit, path) if commit else self._worktree(path)
        if text is None:
            return reason
        try:
            functions, problems = self._parse(text, path, language)
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

        noun = "function" if len(enclosing) == 1 else "functions"
        prefix = f"Enclosing {noun} after this change ({path} lines {first}-{last}"
        full = "".join(lines[first - 1:last])
        whole_header = prefix + "), shown only as context:\n"
        if len(whole_header) + len(full) <= self.max_chars:
            text = whole_header + full
            low, high, truncated = first, last, False
        else:
            # The function is context, not the judged unit, so it may be shortened to fit --max-chars,
            # header included: keep the whole lines nearest the change, as -C keeps a record's neighbours.
            # Reserve room using the widest possible "showing" clause so the final line never overruns.
            reserved = len(prefix + f", showing lines {first}-{last} nearest the change), shown only as context:\n")
            low, high, body = self._shorten(lines, first, last, added + removed_before, max(0, self.max_chars - reserved))
            shown = "" if (low, high) == (first, last) else f", showing lines {low}-{high} nearest the change"
            text = prefix + shown + "), shown only as context:\n" + body
            truncated = True
        return text, {
            "symbols": [symbol for _, _, symbol in enclosing], "language": language,
            "line": first, "end_line": last, "shown_line": low, "shown_end_line": high, "truncated": truncated}

    def _shorten(self, lines, first, last, changed, budget):
        """Whole lines around the change, then the join, holding the body to `budget` characters."""
        low = high = min(max(min(changed), first), last)
        reach = min(max(changed), last)
        used = len(lines[low - 1])
        while high < reach and used + len(lines[high]) <= budget:
            used += len(lines[high])
            high += 1
        grew = True
        while grew:
            grew = False
            if low > first and used + len(lines[low - 2]) <= budget:
                used += len(lines[low - 2])
                low -= 1
                grew = True
            if high < last and used + len(lines[high]) <= budget:
                used += len(lines[high])
                high += 1
                grew = True
        return low, high, "".join(lines[low - 1:high])[:budget]  # the slice only bites a single over-long line


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
