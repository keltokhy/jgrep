"""jgrep: print lines that fit a description.

    tail -f app.log | jgrep "a user is getting frustrated"
    jgrep -o "asks for police overtime records" requests.txt | sort -rn | head
    jgrep -c -p 0.8 "uses a bunching estimator" abstracts.txt

Each line is one yes/no question to Jev. Lines are read as they arrive, judged concurrently and
printed in input order. Exit status follows grep: 0 if anything matched, 1 if not, 2 on error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

from . import __version__
from .core import BACKENDS, Cache, Jev, JevError, JevFatal, config_dir, resolve_backend

STDIN = "(standard input)"
MAX_ERRORS_SHOWN = 10
DEFAULT_BUDGET = 1.0  # dollars; a grep-shaped command that bills per line needs a seat belt


@dataclass
class Record:
    seq: int
    file: str
    lineno: int
    text: str
    before: tuple[str, ...] = ()   # the records just above, shown to Jev but not judged
    after: tuple[str, ...] = ()    # and the ones just below


def question(description: str, context: bool = False) -> dict:
    if context:
        return {"type": "noul", "instructions":
                f'The lines marked ">" fit this description: "{description}". The other lines are the '
                "surrounding text, shown only so the marked lines can be read in context; they are not "
                "themselves being judged."}
    return {"type": "noul", "instructions": f'The text fits this description: "{description}"'}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="jgrep", formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="jgrep [options] DESCRIPTION [FILE ...]",
        description="Print lines that fit a plain-English description, as judged by TypeSafe's Jev model.",
        epilog='examples:\n'
               '  tail -f app.log | jgrep "a user is getting frustrated"\n'
               '  jgrep -o "about heat or hot water" complaints.txt | sort -rn | head\n'
               '  jgrep -v -p 0.2 "spam" inbox.txt\n'
               '  jgrep --whole "uses a bunching estimator" abstracts/*.txt\n\n'
               "Jev is reached through TypeSafe's API (TYPESAFE_API_KEY), OpenRouter (OPENROUTER_API_KEY) or a\n"
               "System One gateway of your own (JEV_GATEWAY_URL and JEV_GATEWAY_API_KEY).\n"
               f"Keys can also live in {config_dir()}/typesafe.key, openrouter.key or gateway.key.")
    ap.add_argument("args", nargs="*", help=argparse.SUPPRESS)
    ap.add_argument("-e", dest="descriptions", action="append", metavar="DESCRIPTION",
                    help="a description; repeat for several, which are judged in one call (a line matches if any fits)")
    ap.add_argument("--all", action="store_true", help="with several -e, a line must fit all of them")
    ap.add_argument("-p", "--threshold", type=float, default=0.5, metavar="P",
                    help="match when the probability is at least P (default 0.5)")
    ap.add_argument("-v", "--invert-match", action="store_true", help="print lines that do not match")
    ap.add_argument("-o", "--prob", action="store_true", help="put the probability in a first, tab-separated column")
    ap.add_argument("-n", "--line-number", action="store_true", help="prefix each line with its line number")
    ap.add_argument("-H", "--with-filename", action="store_true", help="prefix each line with its file name")
    ap.add_argument("--no-filename", action="store_true", help="never print file names")
    ap.add_argument("-c", "--count", action="store_true", help="print only a count of matching lines")
    ap.add_argument("-m", "--max-count", type=int, metavar="NUM", help="stop each input file after NUM matches")
    ap.add_argument("-q", "--quiet", action="store_true", help="print nothing; exit 0 at the first match")
    ap.add_argument("--json", action="store_true", help="print one JSON object per match")
    ap.add_argument("--para", action="store_true", help="judge paragraphs (separated by blank lines), not lines")
    ap.add_argument("--whole", action="store_true", help="judge each file as a whole and print matching file names")
    ap.add_argument("-C", "--context", type=int, default=0, metavar="N",
                    help="also show Jev the N records either side of each one; the decision, and what is "
                         "printed, is still one record at a time")
    ap.add_argument("--unordered", action="store_true", help="print matches as answers arrive, not in input order")
    ap.add_argument("-j", "--concurrency", type=int, default=32, metavar="N", help="calls in flight (default 32)")
    ap.add_argument("--timeout", type=float, default=15.0, metavar="SECONDS",
                    help="give up on a line after this long, retries included (default 15)")
    ap.add_argument("--budget", type=float, default=None, metavar="DOLLARS",
                    help="stop once this much has been spent (default 1.00, or $JGREP_BUDGET; 0 for no limit)")
    ap.add_argument("--max-chars", type=int, default=8000, metavar="N",
                    help="judge only the first N characters of a record (default 8000)")
    ap.add_argument("--no-cache", action="store_true", help="do not read or write the answer cache")
    ap.add_argument("--api", choices=list(BACKENDS), help="which API to call (default: whichever has a key)")
    ap.add_argument("--model", metavar="ID", help="model ID to request (default: the API's latest Jev)")
    ap.add_argument("--stats", action=argparse.BooleanOptionalAction, default=None,
                    help="print calls, tokens and cost to stderr at the end (default: when stderr is a terminal)")
    ap.add_argument("--version", action="version", version=f"jgrep {__version__}")
    return ap


def records(files: list[str], args, stop: threading.Event):
    """Yield Records lazily, so `tail -f` works. Yields a str for a file that cannot be read."""
    seq = 0
    for name in files or ["-"]:
        label = STDIN if name == "-" else name
        try:
            # A private reader on fd 0: sys.stdin's lock can wedge interpreter shutdown.
            f = open(0, "rb", closefd=False) if name == "-" else open(name, "rb")
        except OSError as e:
            yield f"{name}: {e.strerror}"
            continue
        with f:
            if args.whole:
                yield Record(seq, label, 1, f.read(args.max_chars * 4).decode("utf-8", "replace"))
                seq += 1
                continue
            para, start = [], 0
            for lineno, raw in enumerate(iter(f.readline, b""), 1):
                if stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not args.para:
                    yield Record(seq, label, lineno, line)
                    seq += 1
                elif line.strip():
                    start = start if para else lineno
                    para.append(line)
                elif para:
                    yield Record(seq, label, start, "\n".join(para))
                    seq, para = seq + 1, []
            if para:
                yield Record(seq, label, start, "\n".join(para))
                seq += 1


def contextual(stream, n: int):
    """Hand each record the n records either side of it, from its own file.

    A record is held back until the n after it have arrived, so on `tail -f` a match prints once
    n more lines have come in.
    """
    before: deque = deque(maxlen=n)
    waiting: list[tuple[Record, list[Record], list[Record]]] = []
    current = None

    def ready(force: bool):
        while waiting and (force or len(waiting[0][2]) >= n):
            rec, above, below = waiting.pop(0)
            yield replace(rec, before=tuple(r.text for r in above), after=tuple(r.text for r in below))

    for item in stream:
        if isinstance(item, str):  # a file that could not be read
            yield item
            continue
        if item.file != current:   # context does not reach across files
            yield from ready(True)
            before.clear()
            current = item.file
        for _, _, below in waiting:
            if len(below) < n:
                below.append(item)
        waiting.append((item, list(before), []))
        before.append(item)
        yield from ready(False)
    yield from ready(True)


def marked(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def state(rec: Record, args) -> str:
    """What Jev is shown: the record on its own, or marked with `>` inside its context."""
    if not args.context:
        return rec.text[:args.max_chars]
    window = [marked(t[:args.max_chars], "  ") for t in rec.before]
    window.append(marked(rec.text[:args.max_chars], "> "))
    window += [marked(t[:args.max_chars], "  ") for t in rec.after]
    return "\n".join(window)


def render(rec: Record, p: float, ps: list[float], args, show_file: bool) -> str:
    if args.json:
        obj = {"file": rec.file, "line": rec.lineno, "p": round(p, 4)}
        if len(ps) > 1:
            obj["ps"] = [round(x, 4) for x in ps]
        if not args.whole:
            obj["text"] = rec.text
        return json.dumps(obj, ensure_ascii=False)
    if args.whole:
        body = rec.file
    else:
        body = (f"{rec.file}:" if show_file else "") + (f"{rec.lineno}:" if args.line_number else "") + rec.text
    if args.prob:
        body = f"{p:.3f}\t{body}"
    return body + ("\n" if args.para and not args.whole else "")


async def run(args, descriptions: list[str], files: list[str], jev: Jev, out, err) -> int:
    show_file = not args.no_filename and (args.with_filename or len(files) > 1)
    # A limited file must be able to stop its reader and outstanding requests
    # independently of the next file, including when its reader is a live pipe.
    groups = [[f] for f in (files or ["-"])] if args.max_count is not None else [files]
    seen = matched = errors = 0
    try:
        for group in groups:
            result = await scan(args, descriptions, group, jev, out, err, show_file)
            seen += result["seen"]
            matched += result["matched"]
            errors += result["errors"] + bool(result["fatal"]) + result["over_budget"]
            if result["fatal"] or result["over_budget"] or (args.quiet and matched) or result["broken_pipe"]:
                break
    finally:
        await jev.close()
    args.summary = f"{seen:,} records, {matched:,} matched; {jev.meter.summary()}"
    if args.quiet and matched:
        return 0
    return 2 if errors else 0 if matched else 1


def print_counts(args, files: list[str], counts: dict[str, int], out, show_file: bool) -> None:
    if args.count and not args.quiet:
        names = [STDIN if f == "-" else f for f in (files or ["-"])]
        for name in names:
            print(f"{name}:{counts.get(name, 0)}" if show_file else counts.get(name, 0), file=out)
        out.flush()


async def scan(args, descriptions: list[str], files: list[str], jev: Jev, out, err, show_file: bool) -> dict:
    loop = asyncio.get_running_loop()
    questions = {f"d{i}": question(d, bool(args.context)) for i, d in enumerate(descriptions)}
    queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency)
    sem = asyncio.Semaphore(args.concurrency)
    stop, halt = threading.Event(), asyncio.Event()
    finished: dict[int, tuple] = {}
    tasks: set[asyncio.Task] = set()
    counts: dict[str, int] = {}
    s = {"next": 0, "seen": 0, "matched": 0, "errors": 0, "fatal": None,
         "over_budget": False, "broken_pipe": False}
    feeding = {"put": None}

    def feed() -> None:
        def enqueue(item) -> bool:
            if stop.is_set():
                return False
            put = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            feeding["put"] = put
            # Stop may race with creation of the pending put. Either the consumer
            # or this check must cancel it so a full queue cannot strand a reader.
            if stop.is_set():
                put.cancel()
            put.result()
            return not stop.is_set()

        try:
            stream = records(files, args, stop)
            for item in contextual(stream, args.context) if args.context else stream:
                if not enqueue(item):
                    return
            enqueue(None)
        except BaseException:  # the loop is gone because the run halted early
            pass

    def complain(message: str) -> None:
        s["errors"] += 1
        if s["errors"] <= MAX_ERRORS_SHOWN:
            print(f"jgrep: {message}", file=err)

    def emit(rec: Record, p: float | None, ps: list[float] | None, error: str | None) -> None:
        s["seen"] += 1
        if error:
            return complain(f"{rec.file}:{rec.lineno}: {error}")
        if (p >= args.threshold) == args.invert_match:
            return
        s["matched"] += 1
        counts[rec.file] = counts.get(rec.file, 0) + 1
        if not (args.quiet or args.count):
            try:
                out.write(render(rec, p, ps, args, show_file) + "\n")
                out.flush()
            except BrokenPipeError:
                s["broken_pipe"] = True
                halt.set()
        if args.quiet or (args.max_count and s["matched"] >= args.max_count):
            halt.set()

    def deliver(rec: Record, result: tuple) -> None:
        if args.unordered:
            return None if halt.is_set() else emit(rec, *result)
        finished[rec.seq] = (rec, *result)
        while s["next"] in finished and not halt.is_set():
            emit(*finished.pop(s["next"]))
            s["next"] += 1

    async def judge(rec: Record) -> None:
        try:
            if not rec.text.strip():
                result = (0.0, [0.0] * len(questions), None)
            else:
                answers = await jev.ask(state(rec, args), questions)
                ps = [float(answers[q]["noul"]) for q in questions]
                result = (min(ps) if args.all else max(ps), ps, None)
        except JevError as e:
            result = (None, None, str(e))
        except JevFatal as e:
            s["fatal"] = s["fatal"] or str(e)
            return halt.set()
        except Exception as e:
            # Every record needs a result so one client/cache failure cannot leave
            # a permanent gap in ordered output or masquerade as "no matches".
            result = (None, None, f"{type(e).__name__}: {e}")
        finally:
            sem.release()
        deliver(rec, result)
        if args.budget and jev.meter.cost >= args.budget and not s["over_budget"]:
            s["over_budget"] = True
            halt.set()

    def completed(task: asyncio.Task) -> None:
        tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            s["fatal"] = s["fatal"] or f"{type(error).__name__}: {error}"
            halt.set()

    threading.Thread(target=feed, daemon=True).start()
    halted = asyncio.ensure_future(halt.wait())
    while not halt.is_set():
        get = asyncio.ensure_future(queue.get())
        await asyncio.wait({get, halted}, return_when=asyncio.FIRST_COMPLETED)
        if not get.done():
            get.cancel()
            break
        item = get.result()
        if item is None:
            break
        if isinstance(item, str):
            complain(item)
            continue
        await sem.acquire()
        if halt.is_set():
            break
        task = asyncio.create_task(judge(item))
        tasks.add(task)
        task.add_done_callback(completed)

    stop.set()
    if feeding["put"] is not None:
        feeding["put"].cancel()
    # EOF only stops the reader. Matches, budget limits and fatal errors can
    # still arrive while draining requests, and must interrupt that drain.
    pending = list(tasks)
    drained = asyncio.gather(*pending, return_exceptions=True)
    await asyncio.wait({drained, halted}, return_when=asyncio.FIRST_COMPLETED)
    if halt.is_set():
        for t in pending:
            t.cancel()
    await drained
    halted.cancel()
    await asyncio.gather(halted, return_exceptions=True)

    print_counts(args, files, counts, out, show_file)
    if s["errors"] > MAX_ERRORS_SHOWN:
        print(f"jgrep: and {s['errors'] - MAX_ERRORS_SHOWN:,} more errors", file=err)
    if s["fatal"]:
        print(f"jgrep: {s['fatal']}", file=err)
    if s["over_budget"]:
        print(f"jgrep: stopped at the ${args.budget:.2f} budget after {s['seen']:,} records; raise it with --budget",
              file=err)
    return s


def main(argv: list[str] | None = None, *, transport=None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    ap = parser()
    args = ap.parse_args(argv)
    descriptions, files = (args.descriptions, args.args) if args.descriptions else (args.args[:1], args.args[1:])
    if not descriptions:
        ap.print_usage(err)
        return 2
    if args.concurrency < 1:
        print("jgrep: -j takes 1 or more concurrent calls", file=err)
        return 2
    if args.max_count is not None and args.max_count < 0:
        print("jgrep: -m takes 0 or more matches per file", file=err)
        return 2
    if args.budget is None:
        try:
            args.budget = float(os.environ.get("JGREP_BUDGET") or DEFAULT_BUDGET)
        except ValueError:
            print(f"jgrep: JGREP_BUDGET must be a number of dollars; got {os.environ['JGREP_BUDGET']!r}", file=err)
            return 2
    if args.whole and args.para:
        print("jgrep: --whole and --para cannot be combined", file=err)
        return 2
    if args.context < 0:
        print("jgrep: -C takes 0 or more records", file=err)
        return 2
    if args.whole and args.context:
        print("jgrep: --whole and -C cannot be combined; a whole file has nothing around it", file=err)
        return 2
    if args.max_count == 0:
        show_file = not args.no_filename and (args.with_filename or len(files) > 1)
        print_counts(args, files, {}, out, show_file)
        if args.stats or (args.stats is None and err.isatty()):
            print("jgrep: 0 records, 0 matched; 0 calls, 0 cached; 0.0s", file=err)
        return 1
    try:
        backend, key = resolve_backend(args.api)
    except JevFatal as e:
        print(f"jgrep: {e}", file=err)
        return 2

    jev = Jev(key, backend, model=args.model, timeout=args.timeout, concurrency=args.concurrency,
              cache=None if args.no_cache else Cache(), transport=transport)
    t0 = time.perf_counter()
    try:
        code = asyncio.run(run(args, descriptions, files, jev, out, err))
    except KeyboardInterrupt:
        code, args.summary = 130, f"interrupted; {jev.meter.summary()}"
    if args.stats or (args.stats is None and err.isatty()):
        print(f"jgrep: {args.summary}; {time.perf_counter() - t0:.1f}s", file=err)
    return code


def cli() -> None:
    code = main()
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    sys.stderr.flush()
    # A reader thread may still be blocked on stdin (tail -f); do not wait for it.
    os._exit(code)


if __name__ == "__main__":
    cli()
