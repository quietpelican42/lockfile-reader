"""A reader for the Elixir/Erlang term syntax used by ``mix.lock``.

Terms map to Python as: atom -> :class:`Atom` (a ``str`` subclass), binary
string -> ``str``, integer -> ``int``, float -> ``float``, ``true`` / ``false``
/ ``nil`` -> ``True`` / ``False`` / ``None``, list -> ``list``, tuple ->
``tuple``, map -> ``dict``. A keyword list is a list of ``(Atom, value)``
tuples, exactly as in Elixir.
"""

from __future__ import annotations

import re
from typing import Any

from .model import LockfileError

_MAX_DEPTH = 200
_NUM = re.compile(r"[-+]?[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][-+]?[0-9]+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_@]*[?!]?")
_ATOM_TAIL = re.compile(r"[A-Za-z0-9_@.]*[?!]?")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "e": "\x1b",
        "f": "\f", "s": " ", "v": "\v", "d": "\x7f", "\\": "\\", '"': '"', "'": "'"}  # fmt: skip


class Atom(str):
    """An Erlang/Elixir atom."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f":{str.__str__(self)}"


class _Reader:
    def __init__(self, text: str) -> None:
        self.t = text
        self.i = 0

    def err(self, msg: str) -> LockfileError:
        line = self.t.count("\n", 0, self.i) + 1
        return LockfileError(f"mix.lock line {line}: {msg}")

    def ws(self) -> None:
        t = self.t
        n = len(t)
        while self.i < n:
            c = t[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "#":
                while self.i < n and t[self.i] != "\n":
                    self.i += 1
            else:
                break

    def peek(self) -> str:
        return self.t[self.i] if self.i < len(self.t) else ""

    def expect(self, ch: str) -> None:
        self.ws()
        if self.peek() != ch:
            raise self.err(f"expected {ch!r}")
        self.i += 1

    # -- strings -------------------------------------------------------
    def string(self, quote: str) -> str:
        t = self.t
        self.i += 1
        out: list[str] = []
        while self.i < len(t):
            c = t[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\\":
                self.i += 1
                if self.i >= len(t):
                    break
                e = t[self.i]
                if e == "x":
                    m = re.match(r"\{([0-9a-fA-F]+)\}|([0-9a-fA-F]{1,2})", t[self.i + 1 :])
                    if not m:
                        raise self.err("bad \\x escape")
                    out.append(chr(int(m.group(1) or m.group(2), 16)))
                    self.i += m.end()
                elif e == "u":
                    m = re.match(r"\{([0-9a-fA-F]+)\}|([0-9a-fA-F]{4})", t[self.i + 1 :])
                    if not m:
                        raise self.err("bad \\u escape")
                    out.append(chr(int(m.group(1) or m.group(2), 16)))
                    self.i += m.end()
                elif e == "\n":
                    pass
                elif e == "#":
                    out.append("#")
                else:
                    out.append(_ESC.get(e, e))
                self.i += 1
            else:
                out.append(c)
                self.i += 1
        raise self.err("unterminated string")

    # -- terms ---------------------------------------------------------
    def term(self, depth: int = 0) -> Any:
        if depth > _MAX_DEPTH:
            raise self.err("nesting too deep")
        self.ws()
        t = self.t
        c = self.peek()
        if not c:
            raise self.err("unexpected end of input")
        if c == "%":
            self.i += 1
            self.ws()
            # optional struct name before '{'
            if self.peek() != "{":
                m = _IDENT.match(t, self.i) or re.compile(r"[A-Z][A-Za-z0-9_.]*").match(t, self.i)
                if not m:
                    raise self.err("expected '{' after '%'")
                self.i = m.end()
            return self.map_body(depth)
        if c == "{":
            self.i += 1
            return tuple(self.items("}", depth))
        if c == "[":
            self.i += 1
            return self.items("]", depth, keyword_ok=True)
        if c == '"':
            if t.startswith('"""', self.i):
                raise self.err("heredocs are not supported")
            s = self.string('"')
            return s
        if c == "'":
            return self.string("'")
        if c == ":":
            return self.atom()
        if c == "<" and t.startswith("<<", self.i):
            raise self.err("binaries (<<...>>) are not supported")
        if c.isdigit() or (c in "+-" and self.i + 1 < len(t) and t[self.i + 1].isdigit()):
            m = _NUM.match(t, self.i)
            assert m is not None
            self.i = m.end()
            s = m.group(0).replace("_", "")
            return float(s) if any(x in s for x in ".eE") else int(s)
        m = _IDENT.match(t, self.i)
        if m:
            word = m.group(0)
            self.i = m.end()
            if word == "true":
                return True
            if word == "false":
                return False
            if word == "nil":
                return None
            return Atom(word)
        m = re.compile(r"[A-Z][A-Za-z0-9_.]*").match(t, self.i)
        if m:
            self.i = m.end()
            return Atom(m.group(0))
        raise self.err(f"unexpected character {c!r}")

    def atom(self) -> Atom:
        t = self.t
        self.i += 1
        if self.peek() == '"':
            return Atom(self.string('"'))
        m = _IDENT.match(t, self.i)
        if not m:
            # operator atoms such as :+ are not expected in lockfiles
            m2 = re.compile(r"[A-Z][A-Za-z0-9_.]*").match(t, self.i)
            if not m2:
                raise self.err("bad atom")
            self.i = m2.end()
            return Atom(m2.group(0))
        self.i = m.end()
        # allow dotted module atoms like :Elixir.Foo
        tail = _ATOM_TAIL.match(t, self.i)
        end = tail.end() if tail else self.i
        name = t[m.start() : end]
        self.i = end
        return Atom(name)

    def keyword_key(self) -> str | None:
        """If a ``key:`` or ``"key":`` prefix starts here, consume it and return the key."""
        t = self.t
        start = self.i
        if self.peek() == '"':
            try:
                key = self.string('"')
            except LockfileError:
                self.i = start
                return None
            if self.peek() == ":" and t[self.i + 1 : self.i + 2] != ":":
                self.i += 1
                return key
            self.i = start
            return None
        m = _IDENT.match(t, self.i)
        if m and t[m.end() : m.end() + 1] == ":" and t[m.end() + 1 : m.end() + 2] != ":":
            self.i = m.end() + 1
            return m.group(0)
        return None

    def items(self, close: str, depth: int, keyword_ok: bool = False) -> list[Any]:
        out: list[Any] = []
        while True:
            self.ws()
            if self.peek() == close:
                self.i += 1
                return out
            if not self.peek():
                raise self.err(f"missing {close!r}")
            key = self.keyword_key()
            if key is not None:
                out.append((Atom(key), self.term(depth + 1)))
            else:
                out.append(self.term(depth + 1))
            self.ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != close:
                raise self.err(f"expected ',' or {close!r}")

    def map_body(self, depth: int) -> dict[Any, Any]:
        self.expect("{")
        out: dict[Any, Any] = {}
        while True:
            self.ws()
            if self.peek() == "}":
                self.i += 1
                return out
            if not self.peek():
                raise self.err("missing '}'")
            key = self.keyword_key()
            if key is not None:
                k: Any = key
                val = self.term(depth + 1)
            else:
                k = self.term(depth + 1)
                self.ws()
                if not self.t.startswith("=>", self.i):
                    raise self.err("expected '=>'")
                self.i += 2
                val = self.term(depth + 1)
            try:
                out[k] = val
            except TypeError:
                raise self.err("unhashable map key") from None
            self.ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "}":
                raise self.err("expected ',' or '}'")


def loads(text: str) -> Any:
    """Parse one term (plus optional trailing whitespace/comments)."""
    r = _Reader(text)
    value = r.term()
    r.ws()
    if r.i != len(text):
        raise r.err("unexpected content after term")
    return value
