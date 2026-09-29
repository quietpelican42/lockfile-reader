"""JSON with comments and trailing commas (what ``bun.lock`` is)."""

from __future__ import annotations

import json
from typing import Any

from .model import LockfileError


def strip(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments and trailing commas outside strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i : j + 1])
            i = j + 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise LockfileError("unterminated /* comment")
            i = j + 2
        elif c == ",":
            j = i + 1
            while j < n:
                if text[j] in " \t\r\n":
                    j += 1
                elif text.startswith("//", j):
                    k = text.find("\n", j)
                    j = n if k < 0 else k
                elif text.startswith("/*", j):
                    k = text.find("*/", j + 2)
                    if k < 0:
                        raise LockfileError("unterminated /* comment")
                    j = k + 2
                else:
                    break
            if j < n and text[j] in "}]":
                i += 1
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def loads(text: str) -> Any:
    try:
        return json.loads(strip(text.lstrip("﻿")))
    except json.JSONDecodeError as e:
        raise LockfileError(f"invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    except RecursionError:
        raise LockfileError("JSON nesting too deep") from None
