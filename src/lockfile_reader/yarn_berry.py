"""Yarn Berry (v2+) yarn.lock: YAML with ``__metadata`` and ``resolution`` fields."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import unquote

from . import _yaml
from .model import Dependency, Lockfile, LockfileError, Package, Source
from .yarn_v1 import split_pattern

_PROTO = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _source(resolution: str, version: str | None) -> Source:
    _, ref = split_pattern(resolution)
    proto = ref.split(":", 1)[0] if ":" in ref else ""
    if proto == "npm":
        return Source("registry", None)
    if proto == "workspace":
        return Source("workspace", ref[len("workspace:") :])
    if proto == "patch":
        return Source("patch", ref[len("patch:") :])
    if proto in ("link", "portal"):
        return Source("link", ref)
    if proto in ("file", "exec"):
        return Source("path", ref)
    if proto in ("git", "github", "gitlab", "bitbucket") or ref.startswith(("git@", "git+")):
        base, _, frag = ref.partition("#")
        rev = None
        for part in re.split(r"[&]", frag):
            if part.startswith("commit="):
                rev = part[len("commit=") :]
        return Source("git", base, rev or (frag or None))
    if proto in ("http", "https"):
        if ".git" in ref.split("#", 1)[0] and "commit=" in ref:
            base, _, frag = ref.partition("#")
            return Source("git", base, frag.split("commit=")[-1] or None)
        return Source("tarball", ref)
    return Source("other", ref)


def _str_map(value: Any, what: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LockfileError(f"yarn.lock: {what} must be a mapping")
    out: dict[str, str] = {}
    for k, v in value.items():
        out[str(k)] = "" if v is None else str(v)
    return out


def parse(text: str) -> Lockfile:
    doc = _yaml.loads(text)
    if not isinstance(doc, dict):
        raise LockfileError("yarn.lock: expected a mapping at top level")
    meta = doc.get("__metadata")
    if not isinstance(meta, dict):
        raise LockfileError("yarn.lock: missing __metadata (not a Yarn Berry lockfile)")
    index: dict[str, str] = {}
    packages: list[Package] = []
    for header, entry in doc.items():
        if header == "__metadata":
            continue
        if not isinstance(entry, dict):
            raise LockfileError(f"yarn.lock: entry {header!r} is not a mapping")
        descs = tuple(d for d in re.split(r" *, *", str(header)) if d)
        resolution = entry.get("resolution")
        if not isinstance(resolution, str):
            raise LockfileError(f"yarn.lock: entry {header!r} has no resolution")
        name = split_pattern(resolution)[0]
        version_v = entry.get("version")
        version = None if version_v is None else str(version_v)
        for d in descs:
            if d in index:
                raise LockfileError(f"yarn.lock: duplicate descriptor {d!r}")
            index[d] = resolution
        checksum = entry.get("checksum")
        deps_meta = entry.get("dependenciesMeta") or {}
        if not isinstance(deps_meta, dict):
            raise LockfileError(f"yarn.lock: bad dependenciesMeta in {header!r}")
        deps: list[Dependency] = []
        for dn, spec in _str_map(entry.get("dependencies"), "dependencies").items():
            m = deps_meta.get(dn)
            opt = isinstance(m, dict) and m.get("optional") is True
            deps.append(Dependency(dn, spec, "optional" if opt else "prod"))
        for dn, spec in _str_map(entry.get("peerDependencies"), "peerDependencies").items():
            deps.append(Dependency(dn, spec, "peer"))
        alias = next((split_pattern(d)[0] for d in descs if split_pattern(d)[0] != name), None)
        packages.append(
            Package(
                id=resolution,
                name=unquote(name),
                version=version,
                source=_source(resolution, version),
                integrity=None if checksum is None else str(checksum),
                dependencies=tuple(deps),
                alias=alias,
                specifiers=descs,
                raw=entry,
            )
        )
    out: list[Package] = []
    for p in packages:
        out.append(
            replace(
                p,
                dependencies=tuple(
                    replace(d, target=_lookup(index, d.name, d.spec or "")) if d.kind != "peer" else d
                    for d in p.dependencies
                ),
            )
        )
    return Lockfile(
        "yarn-berry",
        str(meta.get("version")) if meta.get("version") is not None else None,
        tuple(out),
        raw={"__metadata": meta},
    )


def _lookup(index: dict[str, str], name: str, spec: str) -> str | None:
    hit = index.get(f"{name}@{spec}")
    if hit is None and not _PROTO.match(spec):
        hit = index.get(f"{name}@npm:{spec}")
    return hit
