"""bun.lock (text lockfile, JSONC)."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from . import _jsonc
from .model import Dependency, Lockfile, LockfileError, Package, Source, make_source_from_url
from .yarn_v1 import split_pattern

_SRI = re.compile(r"^(?:sha\d+|md5)-")
_DEP_FIELDS = (
    ("dependencies", "prod"),
    ("devDependencies", "dev"),
    ("optionalDependencies", "optional"),
    ("peerDependencies", "peer"),
)


def _deps(info: dict[str, Any]) -> list[Dependency]:
    out: list[Dependency] = []
    for field, kind in _DEP_FIELDS:
        sub = info.get(field)
        if sub is None:
            continue
        if not isinstance(sub, dict):
            raise LockfileError(f"bun.lock: {field} must be an object")
        for name, spec in sub.items():
            out.append(Dependency(str(name), None if spec is None else str(spec), kind))
    return out


def _classify(rest: str) -> tuple[Source | None, str | None, str | None]:
    """Return ``(source, version, alias_of)`` for the part of an ident after ``name@``."""
    if rest.startswith("npm:"):
        real, ver = split_pattern(rest[4:])
        return Source("registry", None), ver, real
    if rest.startswith("workspace:"):
        return Source("workspace", rest[len("workspace:") :]), None, None
    if rest.startswith(("file:", "folder:")):
        return Source("path", rest), None, None
    if rest.startswith("link:"):
        return Source("link", rest), None, None
    if rest.startswith(("github:", "git+", "git:", "gitlab:", "bitbucket:", "git@")):
        base, _, rev = rest.partition("#")
        return Source("git", base, rev or None), None, None
    if rest.startswith(("http://", "https://")):
        return make_source_from_url(rest), None, None
    return Source("registry", None), rest, None


def _key_name(key: str) -> str:
    """The name a package is installed under: the last one or two path segments of its key."""
    segs = key.split("/")
    if len(segs) >= 2 and segs[-2].startswith("@"):
        return "/".join(segs[-2:])
    return segs[-1]


def parse(text: str) -> Lockfile:
    doc = _jsonc.loads(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("packages"), dict):
        raise LockfileError("bun.lock: missing 'packages' object")
    packages: list[Package] = []
    for key, tup in doc["packages"].items():
        if not isinstance(tup, list) or not tup or not isinstance(tup[0], str):
            raise LockfileError(f"bun.lock: package {key!r} is not an ident tuple")
        ident = tup[0]
        at = ident.find("@", 1)
        name, rest = (ident, "") if at < 0 else (ident[:at], ident[at + 1 :])
        source, version, npm_real = _classify(rest)
        real = npm_real or name
        installed = _key_name(str(key))
        alias = installed if installed != real else None
        info: dict[str, Any] = {}
        integrity: str | None = None
        registry: str | None = None
        for i, el in enumerate(tup[1:], start=1):
            if isinstance(el, dict) and not info:
                info = el
            elif isinstance(el, str):
                if _SRI.match(el):
                    integrity = el
                elif i == 1 and source is not None and source.kind == "registry":
                    registry = el or None
        if source is not None and source.kind == "registry" and registry:
            source = Source("registry", registry)
        packages.append(
            Package(
                id=str(key),
                name=real,
                version=version,
                source=source,
                integrity=integrity,
                dependencies=tuple(_deps(info)),
                alias=alias,
                raw=tup,
            )
        )
    ids = {p.id for p in packages}

    def resolve(owner: str, dep: str) -> str | None:
        segs = owner.split("/") if owner else []
        while True:
            cand = "/".join([*segs, dep])
            if cand in ids:
                return cand
            if not segs:
                return None
            segs.pop()
            if segs and len(segs) >= 1 and segs[-1].startswith("@"):
                segs.pop()

    out = [
        replace(p, dependencies=tuple(replace(d, target=resolve(p.id, d.name)) for d in p.dependencies))
        for p in packages
    ]
    roots: dict[str, tuple[Dependency, ...]] = {}
    ws = doc.get("workspaces")
    if isinstance(ws, dict):
        for path, info2 in ws.items():
            if isinstance(info2, dict):
                roots[str(path)] = tuple(replace(d, target=resolve("", d.name)) for d in _deps(info2))
    ver = doc.get("lockfileVersion")
    meta = {k: v for k, v in doc.items() if k not in ("packages", "workspaces", "lockfileVersion")}
    return Lockfile("bun", None if ver is None else str(ver), tuple(out), roots=roots, raw=meta)
