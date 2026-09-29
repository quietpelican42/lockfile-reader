"""yarn.lock v1 (the custom "yarn lockfile v1" format)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .model import Dependency, Lockfile, LockfileError, Package, Source, make_source_from_url

_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def split_pattern(pattern: str) -> tuple[str, str]:
    """Split ``name@range`` into ``(name, range)`` the way Yarn v1 does."""
    scoped = pattern.startswith("@")
    body = pattern[1:] if scoped else pattern
    name, sep, rng = body.partition("@")
    if scoped:
        name = "@" + name
    if not sep:
        return name, "latest"
    return name, rng or "*"


def _entry_name(specs: tuple[str, ...]) -> tuple[str, str | None]:
    """Real package name of an entry, and the alias it is requested under (if any)."""
    real: str | None = None
    keyed = split_pattern(specs[0])[0]
    for s in specs:
        key_name, rng = split_pattern(s)
        cand = split_pattern(rng[4:])[0] if rng.startswith("npm:") else key_name
        if real is None or (cand == key_name and real != key_name):
            real = cand
    assert real is not None
    alias = next((split_pattern(s)[0] for s in specs if split_pattern(s)[0] != real), None)
    del keyed
    return real, alias


def _unquote(tok: str, no: int) -> str:
    if tok.startswith('"'):
        try:
            v = json.loads(tok)
        except ValueError:
            raise LockfileError(f"yarn.lock line {no}: bad quoted string {tok[:40]!r}") from None
        return str(v)
    return tok


def _split_quoted_commas(s: str, no: int) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    in_q = False
    i = 0
    while i < len(s):
        c = s[i]
        if in_q:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 1
            elif c == '"':
                in_q = False
        elif c == '"':
            in_q = True
            cur.append(c)
        elif c == ",":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if in_q:
        raise LockfileError(f"yarn.lock line {no}: unterminated quote")
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _read_token(s: str, no: int) -> tuple[str, str]:
    """Read one (possibly quoted) token; return (token_text, rest)."""
    s = s.lstrip(" ")
    if s.startswith('"'):
        i = 1
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return s[: i + 1], s[i + 1 :]
            i += 1
        raise LockfileError(f"yarn.lock line {no}: unterminated quote")
    i = s.find(" ")
    if i < 0:
        return s, ""
    return s[:i], s[i:]


def parse_raw(text: str) -> dict[str, dict[str, Any]]:
    """Parse into ``{header_key_line: {field: value | {name: spec}}}``."""
    entries: dict[str, dict[str, Any]] = {}
    lines = text.replace("\r\n", "\n").split("\n")
    stack: list[tuple[int, dict[str, Any]]] = []
    for idx, raw in enumerate(lines):
        no = idx + 1
        if raw.startswith(_MARKERS):
            raise LockfileError(f"yarn.lock line {no}: unresolved merge conflict marker")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent] or raw.startswith("\t"):
            raise LockfileError(f"yarn.lock line {no}: tab indentation")
        if indent % 2:
            raise LockfileError(f"yarn.lock line {no}: odd indentation")
        if indent == 0:
            if not stripped.endswith(":"):
                raise LockfileError(f"yarn.lock line {no}: expected entry header ending in ':'")
            header = stripped[:-1]
            entry: dict[str, Any] = {}
            entries[header] = entry  # duplicates are merged by the caller's check
            stack = [(0, entry)]
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack or stack[-1][0] != indent - 2:
            raise LockfileError(f"yarn.lock line {no}: bad indentation")
        parent = stack[-1][1]
        key_tok, rest = _read_token(stripped, no)
        rest = rest.strip()
        if key_tok.endswith(":") and not key_tok.startswith('"'):
            key_tok = key_tok[:-1]
            if rest == "":
                key = _unquote(key_tok, no)
                child: dict[str, Any] = {}
                parent[key] = child
                stack.append((indent, child))
                continue
        elif rest == "" and key_tok.startswith('"') and key_tok.endswith('":'):
            pass
        key = _unquote(key_tok, no)
        if rest == "":
            raise LockfileError(f"yarn.lock line {no}: property {key!r} has no value")
        if rest.startswith('"'):
            val_tok, tail = _read_token(rest, no)
            if tail.strip():
                raise LockfileError(f"yarn.lock line {no}: trailing content after value")
            parent[key] = _unquote(val_tok, no)
        else:
            parent[key] = rest
    return entries


def parse(text: str) -> Lockfile:
    entries = parse_raw(text)
    packages: list[Package] = []
    index: dict[str, str] = {}
    for header, entry in entries.items():
        specs = tuple(_unquote(p, 0) for p in _split_quoted_commas(header, 0))
        if not specs:
            raise LockfileError("yarn.lock: entry without a specifier")
        name, alias = _entry_name(specs)
        for s in specs:
            if s in index:
                raise LockfileError(f"yarn.lock: duplicate specifier {s!r}")
            index[s] = specs[0]
        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            raise LockfileError(f"yarn.lock: bad version in {header!r}")
        resolved = entry.get("resolved")
        source: Source | None = None
        if isinstance(resolved, str):
            source = make_source_from_url(resolved)
            if source.kind in ("registry", "tarball"):
                source = Source(source.kind, resolved.split("#", 1)[0], None)
        integ = entry.get("integrity")
        deps: list[Dependency] = []
        for field_name, kind in (("dependencies", "prod"), ("optionalDependencies", "optional")):
            sub = entry.get(field_name)
            if isinstance(sub, dict):
                for dn, dspec in sub.items():
                    if not isinstance(dspec, str):
                        raise LockfileError(f"yarn.lock: bad dependency {dn!r} in {header!r}")
                    deps.append(Dependency(dn, dspec, kind))
            elif sub is not None:
                raise LockfileError(f"yarn.lock: bad {field_name} in {header!r}")
        packages.append(
            Package(
                id=specs[0],
                name=name,
                version=version,
                source=source,
                integrity=integ if isinstance(integ, str) else None,
                dependencies=tuple(deps),
                optional=False,
                alias=alias,
                specifiers=specs,
                raw=entry,
            )
        )
    resolved_pkgs = [
        replace(
            p,
            dependencies=tuple(
                Dependency(d.name, d.spec, d.kind, index.get(f"{d.name}@{d.spec}"))
                for d in p.dependencies
            ),
        )
        for p in packages
    ]
    first = text.split("\n", 2)[0] if text else ""
    version = "1" if "yarn lockfile v1" in text[:200] or first.startswith("#") else None
    return Lockfile("yarn-v1", version, tuple(resolved_pkgs))
