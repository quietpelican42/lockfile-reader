"""A reader for the YAML subset that pnpm and Yarn Berry write.

Supported: block mappings and sequences, plain / single-quoted / double-quoted
scalars, flow sequences and mappings (also spanning lines), literal and folded
block scalars, ``? `` explicit keys, comments, ``---``. Not supported: anchors,
tags, multi-document streams, merge keys. Plain scalars resolve to
``None``/``bool``/``int``/``float`` like the YAML 1.2 core schema; everything
else is a ``str``.
"""

from __future__ import annotations

import re
from typing import Any

from .model import LockfileError

_INT = re.compile(r"^[-+]?[0-9]+$")
_FLOAT = re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][-+]?[0-9]+)?$")
_MAX_DEPTH = 200
_BLOCK_HEADER = re.compile(r"^[|>][+-]?[0-9]?[+-]?$")

_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
    "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
    "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
}  # fmt: skip


def _plain(text: str) -> Any:
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text) and any(c.isdigit() for c in text):
        return float(text)
    return text


class _Line:
    __slots__ = ("indent", "text", "no")

    def __init__(self, indent: int, text: str, no: int) -> None:
        self.indent = indent
        self.text = text
        self.no = no


def _strip_comment(s: str) -> str:
    """Remove a trailing ``# comment`` that is not inside quotes."""
    quote = ""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if quote:
            if quote == "'" and c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 1
                else:
                    quote = ""
            elif quote == '"':
                if c == "\\":
                    i += 1
                elif c == '"':
                    quote = ""
        elif c in "'\"" and (i == 0 or s[i - 1] in " \t[{,:-"):
            quote = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
        i += 1
    return s.rstrip()


class _Parser:
    def __init__(self, text: str) -> None:
        self.raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.pos = 0

    # -- line handling -------------------------------------------------
    def _err(self, msg: str, no: int) -> LockfileError:
        return LockfileError(f"YAML line {no}: {msg}")

    def _peek(self) -> _Line | None:
        """Next non-blank, non-comment line (without consuming it)."""
        while self.pos < len(self.raw_lines):
            raw = self.raw_lines[self.pos]
            stripped = raw.lstrip(" ")
            if not stripped or stripped.startswith("#") or stripped.strip() == "":
                self.pos += 1
                continue
            if "\t" in raw[: len(raw) - len(stripped)]:
                raise self._err("tab in indentation", self.pos + 1)
            if raw == "---" or raw.startswith("--- "):
                self.pos += 1
                continue
            if raw == "...":
                self.pos = len(self.raw_lines)
                return None
            return _Line(len(raw) - len(stripped), _strip_comment(stripped), self.pos + 1)
        return None

    # -- entry ---------------------------------------------------------
    def parse(self) -> Any:
        first = self._peek()
        if first is None:
            return None
        value = self._block(first.indent, 0)
        rest = self._peek()
        if rest is not None:
            raise self._err("unexpected content after document", rest.no)
        return value

    def _block(self, indent: int, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            raise LockfileError("YAML nesting too deep")
        line = self._peek()
        assert line is not None
        text = line.text
        if text == "-" or text.startswith("- "):
            return self._sequence(line.indent, depth)
        if _find_key_end(text) >= 0 or text.startswith("? "):
            return self._mapping(line.indent, depth)
        # a lone scalar (or flow collection) document / value
        self.pos += 1
        return self._inline_value(text, line.no, line.indent, depth)

    # -- sequences -----------------------------------------------------
    def _sequence(self, indent: int, depth: int) -> list[Any]:
        out: list[Any] = []
        while True:
            line = self._peek()
            if line is None or line.indent != indent:
                if line is not None and line.indent > indent:
                    raise self._err("bad indentation in sequence", line.no)
                return out
            text = line.text
            if not (text == "-" or text.startswith("- ")):
                return out
            rest = text[1:].lstrip(" ")
            if not rest:
                self.pos += 1
                nxt = self._peek()
                if nxt is not None and nxt.indent > indent:
                    out.append(self._block(nxt.indent, depth + 1))
                else:
                    out.append(None)
                continue
            gap = len(text) - len(rest)
            if _find_key_end(rest) >= 0:
                # inline mapping start: re-interpret the rest as a line at deeper indent
                self.raw_lines[self.pos] = " " * (indent + gap) + rest
                out.append(self._mapping(indent + gap, depth + 1))
            elif rest.startswith("- "):
                self.raw_lines[self.pos] = " " * (indent + gap) + rest
                out.append(self._sequence(indent + gap, depth + 1))
            else:
                self.pos += 1
                out.append(self._inline_value(rest, line.no, indent, depth))

    # -- mappings ------------------------------------------------------
    def _mapping(self, indent: int, depth: int) -> dict[Any, Any]:
        out: dict[Any, Any] = {}
        while True:
            line = self._peek()
            if line is None or line.indent != indent:
                if line is not None and line.indent > indent:
                    raise self._err("bad indentation in mapping", line.no)
                return out
            text = line.text
            if text.startswith("? "):
                key_text = text[2:].strip()
                self.pos += 1
                key: Any = self._scalar_text(key_text, line.no)
                nxt = self._peek()
                if nxt is not None and nxt.indent == indent and nxt.text.startswith(":"):
                    text = nxt.text
                    self.pos += 1
                    rest = text[1:].strip()
                    out[key] = self._value_after_key(rest, nxt.no, indent, depth)
                else:
                    out[key] = None
                continue
            end = _find_key_end(text)
            if end < 0:
                raise self._err("expected 'key: value'", line.no)
            key = self._scalar_text(text[:end].strip(), line.no)
            if not isinstance(key, str):
                key = str(key) if key is not None else "null"
            rest = text[end + 1 :].strip()
            self.pos += 1
            if key in out:
                raise self._err(f"duplicate key {key!r}", line.no)
            out[key] = self._value_after_key(rest, line.no, indent, depth)

    def _value_after_key(self, rest: str, no: int, indent: int, depth: int) -> Any:
        if rest:
            return self._inline_value(rest, no, indent, depth)
        nxt = self._peek()
        if nxt is None:
            return None
        if nxt.indent > indent:
            return self._block(nxt.indent, depth + 1)
        if nxt.indent == indent and (nxt.text == "-" or nxt.text.startswith("- ")):
            return self._sequence(indent, depth + 1)
        return None

    # -- values --------------------------------------------------------
    def _inline_value(self, text: str, no: int, indent: int, depth: int) -> Any:
        if _BLOCK_HEADER.match(text):
            return self._block_scalar(text, indent, no)
        if text[0] in "[{":
            # gather continuation lines until brackets balance
            buf = text
            while not _balanced(buf):
                if self.pos >= len(self.raw_lines):
                    raise self._err("unterminated flow collection", no)
                buf += " " + _strip_comment(self.raw_lines[self.pos].strip())
                self.pos += 1
            value, end = _Flow(buf, no).parse()
            if buf[end:].strip():
                raise self._err("trailing content after flow collection", no)
            return value
        if text[0] in "&*!":
            raise self._err("anchors, aliases and tags are not supported", no)
        if text[0] == '"' and not _quote_closed(text):
            # multi-line double-quoted scalar
            buf = text
            while not _quote_closed(buf):
                if self.pos >= len(self.raw_lines):
                    raise self._err("unterminated quoted scalar", no)
                buf += "\n" + self.raw_lines[self.pos].strip()
                self.pos += 1
            return self._scalar_text(_fold(buf), no)
        if text[0] == "'" and not _quote_closed(text):
            buf = text
            while not _quote_closed(buf):
                if self.pos >= len(self.raw_lines):
                    raise self._err("unterminated quoted scalar", no)
                buf += "\n" + self.raw_lines[self.pos].strip()
                self.pos += 1
            return self._scalar_text(_fold(buf), no)
        # plain multi-line scalars: continuation lines indented deeper than the key
        if text[0] not in "\"'":
            parts = [text]
            while self.pos < len(self.raw_lines):
                raw = self.raw_lines[self.pos]
                stripped = raw.strip()
                if stripped and not stripped.startswith("#") and len(raw) - len(raw.lstrip(" ")) > indent:
                    if _find_key_end(_strip_comment(stripped)) >= 0:
                        break
                    parts.append(_strip_comment(stripped))
                    self.pos += 1
                else:
                    break
            text = " ".join(parts)
        return self._scalar_text(text, no)

    def _scalar_text(self, text: str, no: int) -> Any:
        if not text:
            return None
        c = text[0]
        if c == '"':
            value, end = _double_quoted(text, no)
            if text[end:].strip():
                raise self._err("content after closing quote", no)
            return value
        if c == "'":
            value, end = _single_quoted(text, no)
            if text[end:].strip():
                raise self._err("content after closing quote", no)
            return value
        if c in "[{":
            value2, end = _Flow(text, no).parse()
            return value2
        return _plain(text)

    def _block_scalar(self, header: str, indent: int, no: int) -> str:
        style = header[0]
        chomp = "clip"
        if "-" in header[1:]:
            chomp = "strip"
        elif "+" in header[1:]:
            chomp = "keep"
        lines: list[str] = []
        block_indent = -1
        while self.pos < len(self.raw_lines):
            raw = self.raw_lines[self.pos]
            if raw.strip() == "":
                lines.append("")
                self.pos += 1
                continue
            cur = len(raw) - len(raw.lstrip(" "))
            if cur <= indent:
                break
            if block_indent < 0:
                block_indent = cur
            if cur < block_indent:
                break
            lines.append(raw[block_indent:])
            self.pos += 1
        trailing = 0
        while lines and lines[-1] == "":
            lines.pop()
            trailing += 1
        if style == "|":
            body = "\n".join(lines)
        else:
            body = ""
            for i, ln in enumerate(lines):
                if i == 0:
                    body = ln
                elif ln == "" or lines[i - 1] == "":
                    body += "\n" + ln if ln == "" else ln
                else:
                    body += " " + ln
        if chomp == "strip" or not lines:
            return body
        if chomp == "keep":
            return body + "\n" * (trailing + 1)
        return body + "\n"


def _fold(buf: str) -> str:
    """Fold line breaks inside a multi-line quoted scalar into spaces."""
    out = []
    for i, part in enumerate(buf.split("\n")):
        if i == 0:
            out.append(part)
        elif part == "":
            out.append("\n")
        else:
            out.append(part if out and out[-1].endswith("\n") else " " + part)
    return "".join(out)


def _quote_closed(text: str) -> bool:
    q = text[0]
    i = 1
    n = len(text)
    while i < n:
        c = text[i]
        if q == "'":
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                return True
        else:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return True
        i += 1
    return False


def _balanced(text: str) -> bool:
    depth = 0
    quote = ""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if quote == "'" and c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 1
                else:
                    quote = ""
            elif quote == '"':
                if c == "\\":
                    i += 1
                elif c == '"':
                    quote = ""
        elif c in "'\"":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth <= 0 and not quote


def _find_key_end(text: str) -> int:
    """Index of the ``:`` that ends a mapping key, or -1 if ``text`` is not ``key: ...``."""
    if not text:
        return -1
    if text[0] in "\"'":
        q = text[0]
        i = 1
        n = len(text)
        while i < n:
            c = text[i]
            if q == "'" and c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                break
            if q == '"':
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    break
            i += 1
        else:
            return -1
        j = i + 1
        while j < n and text[j] == " ":
            j += 1
        if j < n and text[j] == ":" and (j + 1 == n or text[j + 1] == " "):
            return j
        return -1
    if text[0] in "[{":
        return -1
    i = text.find(":")
    while i >= 0:
        if i + 1 == len(text) or text[i + 1] == " ":
            return i
        i = text.find(":", i + 1)
    return -1


def _double_quoted(text: str, no: int) -> tuple[str, int]:
    out: list[str] = []
    i = 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\":
            i += 1
            if i >= n:
                break
            e = text[i]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
            elif e in "xuU":
                width = {"x": 2, "u": 4, "U": 8}[e]
                digits = text[i + 1 : i + 1 + width]
                try:
                    out.append(chr(int(digits, 16)))
                except (ValueError, OverflowError):
                    raise LockfileError(f"YAML line {no}: bad escape \\{e}{digits}") from None
                i += width
            elif e == "\n":
                pass
            else:
                raise LockfileError(f"YAML line {no}: unknown escape \\{e}")
        else:
            out.append(c)
        i += 1
    raise LockfileError(f"YAML line {no}: unterminated double-quoted string")


def _single_quoted(text: str, no: int) -> tuple[str, int]:
    out: list[str] = []
    i = 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            if i + 1 < n and text[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise LockfileError(f"YAML line {no}: unterminated single-quoted string")


class _Flow:
    def __init__(self, text: str, no: int) -> None:
        self.t = text
        self.i = 0
        self.no = no

    def _err(self, msg: str) -> LockfileError:
        return LockfileError(f"YAML line {self.no}: {msg}")

    def parse(self) -> tuple[Any, int]:
        v = self._value(0)
        return v, self.i

    def _ws(self) -> None:
        while self.i < len(self.t) and self.t[self.i] in " \t\n":
            self.i += 1

    def _value(self, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            raise self._err("nesting too deep")
        self._ws()
        if self.i >= len(self.t):
            raise self._err("unexpected end of flow collection")
        c = self.t[self.i]
        if c == "[":
            self.i += 1
            seq: list[Any] = []
            while True:
                self._ws()
                if self.i >= len(self.t):
                    raise self._err("unterminated flow sequence")
                if self.t[self.i] == "]":
                    self.i += 1
                    return seq
                seq.append(self._value(depth + 1))
                self._ws()
                if self.i < len(self.t) and self.t[self.i] == ",":
                    self.i += 1
                elif self.i < len(self.t) and self.t[self.i] != "]":
                    raise self._err("expected ',' or ']' in flow sequence")
        if c == "{":
            self.i += 1
            mp: dict[Any, Any] = {}
            while True:
                self._ws()
                if self.i >= len(self.t):
                    raise self._err("unterminated flow mapping")
                if self.t[self.i] == "}":
                    self.i += 1
                    return mp
                key = self._scalar(colon_ends=True)
                if key is None and self.i < len(self.t) and self.t[self.i] in "]:":
                    raise self._err("expected a key in flow mapping")
                self._ws()
                val: Any = None
                if self.i < len(self.t) and self.t[self.i] == ":":
                    self.i += 1
                    self._ws()
                    if self.i < len(self.t) and self.t[self.i] not in ",}":
                        val = self._value(depth + 1)
                if not isinstance(key, str):
                    key = str(key)
                mp[key] = val
                self._ws()
                if self.i < len(self.t) and self.t[self.i] == ",":
                    self.i += 1
                elif self.i < len(self.t) and self.t[self.i] != "}":
                    raise self._err("expected ',' or '}' in flow mapping")
        return self._scalar(colon_ends=False)

    def _scalar(self, colon_ends: bool) -> Any:
        self._ws()
        t = self.t
        c = t[self.i] if self.i < len(t) else ""
        if c == '"':
            v, end = _double_quoted(t[self.i :], self.no)
            self.i += end
            return v
        if c == "'":
            v, end = _single_quoted(t[self.i :], self.no)
            self.i += end
            return v
        start = self.i
        while self.i < len(t):
            ch = t[self.i]
            if ch in ",]}":
                break
            if ch == ":" and (self.i + 1 >= len(t) or t[self.i + 1] in " ,]}") and colon_ends:
                break
            if ch == ":" and (self.i + 1 >= len(t) or t[self.i + 1] == " "):
                break
            self.i += 1
        return _plain(t[start : self.i].strip())


def loads(text: str) -> Any:
    """Parse the supported YAML subset into Python values."""
    return _Parser(text).parse()
