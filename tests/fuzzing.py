"""Mutation fuzzing shared by the test suite and ``python tests/fuzzing.py``."""

from __future__ import annotations

import collections
import random
import sys
import time
from collections.abc import Callable

import lockfile_reader
from lockfile_reader import Lockfile, LockfileError

_SNIPPETS = ["{", "}", "[", "]", '"', "'", ":", ",", "\n", "  ", "\t", "\\", "#", "---", "%{", "=>", "(", ")", "@", "<<<<<<<", "\x00", "﻿", "é", "-", "!", "0", "null"]


def mutate(text: str, rng: random.Random) -> str:
    """Apply one to three random edits."""
    for _ in range(rng.randint(1, 3)):
        if not text:
            text = rng.choice(_SNIPPETS)
            continue
        op = rng.randrange(10)
        i = rng.randrange(len(text))
        if op == 0:  # truncate
            text = text[:i]
        elif op == 1:  # delete a span
            text = text[:i] + text[i + rng.randint(1, 40) :]
        elif op == 2:  # insert a snippet
            text = text[:i] + rng.choice(_SNIPPETS) + text[i:]
        elif op == 3:  # replace a character
            text = text[:i] + rng.choice(_SNIPPETS) + text[i + 1 :]
        elif op == 4:  # duplicate a line
            lines = text.split("\n")
            j = rng.randrange(len(lines))
            lines.insert(j, lines[j])
            text = "\n".join(lines)
        elif op == 5:  # drop a line
            lines = text.split("\n")
            del lines[rng.randrange(len(lines))]
            text = "\n".join(lines)
        elif op == 6:  # swap two lines
            lines = text.split("\n")
            a, b = rng.randrange(len(lines)), rng.randrange(len(lines))
            lines[a], lines[b] = lines[b], lines[a]
            text = "\n".join(lines)
        elif op == 7:  # change indentation of a line
            lines = text.split("\n")
            j = rng.randrange(len(lines))
            lines[j] = (" " * rng.randint(0, 6)) + lines[j].lstrip(" ")
            text = "\n".join(lines)
        elif op == 8:  # deep nesting
            text = text[:i] + rng.choice(["[", "{", "(", "%{"]) * rng.choice([50, 500, 5000]) + text[i:]
        else:  # shuffle a token
            words = text.split(" ")
            j = rng.randrange(len(words))
            words[j] = words[j][::-1]
            text = " ".join(words)
    return text


class Stats:
    def __init__(self) -> None:
        self.runs = 0
        self.ok = 0
        self.clean_errors = 0
        self.leaks: collections.Counter[str] = collections.Counter()  # exceptions the parser itself let escape
        self.crashes: list[str] = []  # exceptions that escaped parse()
        self.slowest = 0.0

    def summary(self) -> str:
        return (
            f"runs={self.runs} parsed={self.ok} LockfileError={self.clean_errors} "
            f"internal-leaks-caught-by-parse()={sum(self.leaks.values())} crashes={len(self.crashes)} slowest={self.slowest:.2f}s"
        )


def run(fmt: str, seeds: list[str], count: int, rng_seed: int = 0, direct: bool = True) -> Stats:
    """Mutate each seed text ``count`` times and parse it. ``direct`` also calls the format parser
    without the safety net in ``parse()`` to find exceptions that would otherwise be converted."""
    stats = Stats()
    rng = random.Random(rng_seed)
    parser: Callable[[str], Lockfile] = lockfile_reader._PARSERS[fmt]
    for base in seeds:
        for _ in range(count):
            text = mutate(base, rng)
            stats.runs += 1
            t0 = time.perf_counter()
            if direct:
                try:
                    parser(text.lstrip("﻿"))
                except LockfileError:
                    pass
                except Exception as e:  # noqa: BLE001
                    stats.leaks[type(e).__name__] += 1
            try:
                lockfile_reader.parse(text, format=fmt)
                stats.ok += 1
            except LockfileError:
                stats.clean_errors += 1
            except BaseException as e:  # noqa: BLE001
                stats.crashes.append(f"{fmt}: {type(e).__name__}: {str(e)[:80]}")
            stats.slowest = max(stats.slowest, time.perf_counter() - t0)
    return stats


if __name__ == "__main__":
    # usage: python tests/fuzzing.py [mutations-per-file] [max-files-per-format]
    import corpus

    per_file = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    max_files = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    corpus.fetch()
    total_crashes = 0
    for fmt in lockfile_reader.FORMATS:
        entries = sorted((e for e in corpus.entries(fmt) if e.local.exists()), key=lambda e: e.local.stat().st_size)
        pick = entries[: max_files // 2] + entries[-(max_files - max_files // 2) :] if len(entries) > max_files else entries
        # keep mutation cost bounded: very large files are cut to their first 60 KB
        seeds = [e.local.read_text(encoding="utf-8")[:60_000] for e in pick]
        st = run(fmt, seeds, per_file)
        total_crashes += len(st.crashes)
        print(f"{fmt:11s} files={len(seeds):2d} {st.summary()}  leaks={dict(st.leaks)}")
        for c in st.crashes[:3]:
            print("   CRASH", c)
    sys.exit(1 if total_crashes else 0)
