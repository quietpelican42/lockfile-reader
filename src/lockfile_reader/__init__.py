"""Parse package-manager lockfiles into a common model."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from . import bun, cargo, detect, gemfile, gosum, mix, npm, pnpm, yarn_berry, yarn_v1
from .model import Dependency, Lockfile, LockfileError, Package, Source

__all__ = [
    "Dependency",
    "FORMATS",
    "Lockfile",
    "LockfileError",
    "Package",
    "Source",
    "parse",
]

FORMATS = detect.FORMATS
__version__ = "0.1.0"

_PARSERS: dict[str, Callable[[str], Lockfile]] = {
    "yarn-v1": yarn_v1.parse,
    "yarn-berry": yarn_berry.parse,
    "gemfile": gemfile.parse,
    "mix": mix.parse,
    "bun": bun.parse,
    "pnpm": pnpm.parse,
    "npm": npm.parse,
    "cargo": cargo.parse,
    "gosum": gosum.parse,
}


def _read(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as e:
        raise LockfileError(f"cannot read {path.name}: {e.strerror or e}") from None
    return _decode(data)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise LockfileError(f"not valid UTF-8: {e.reason} at byte {e.start}") from None


def parse(source: str | bytes | os.PathLike[str], format: str | None = None) -> Lockfile:
    """Parse a lockfile from a path or from its text.

    ``source`` is a path (``os.PathLike``), a ``str`` that names an existing
    file, or the lockfile text itself (a ``str`` that is multi-line or starts
    with ``{``, ``%{`` or ``[``). ``format`` is one of
    :data:`FORMATS` (``"yarn"`` picks v1 or Berry by content); when omitted the
    format is detected from the file name, then from the content.

    Raises :class:`LockfileError` for any input that is not a valid lockfile.
    """
    name: str | None = None
    if isinstance(source, bytes):
        text = _decode(source)
    elif isinstance(source, str):
        if "\n" in source or "\r" in source or not source or source.lstrip()[:1] in ("{", "%", "["):
            text = source
        else:
            p = Path(source)
            if not p.is_file():
                raise LockfileError(f"no such file: {p.name}")
            name, text = p.name, _read(p)
    else:
        p = Path(source)
        name, text = p.name, _read(p)

    fmt = format
    if fmt is None and name is not None:
        fmt = detect.from_name(name)
    if fmt is None:
        fmt = detect.sniff(text)
    if fmt == "yarn":
        fmt = "yarn-berry" if "__metadata:" in text else "yarn-v1"
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise LockfileError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")
    text = text.lstrip("﻿")
    try:
        return parser(text)
    except LockfileError:
        raise
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, RecursionError, OverflowError) as e:
        raise LockfileError(f"malformed {fmt} lockfile ({type(e).__name__}: {e})") from e
