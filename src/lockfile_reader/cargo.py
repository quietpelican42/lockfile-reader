"""Cargo.lock, versions 1 through 4."""

from __future__ import annotations

import re
import tomllib
from dataclasses import replace
from typing import Any

from .model import Dependency, Lockfile, LockfileError, Package, Source

_DEP = re.compile(r"^(\S+)(?: (\S+))?(?: \((.*)\))?$")
_META_CHECKSUM = re.compile(r"^checksum (\S+) (\S+) \((.*)\)$")


def _source(src: Any) -> Source | None:
    if src is None:
        return Source("path", None)
    if not isinstance(src, str):
        raise LockfileError("Cargo.lock: package source must be a string")
    if src.startswith("registry+"):
        return Source("registry", src[len("registry+") :])
    if src.startswith("sparse+"):
        return Source("registry", src)
    if src.startswith("git+"):
        body = src[len("git+") :]
        url, _, rev = body.partition("#")
        base, _, query = url.partition("?")
        ref = None
        for part in query.split("&"):
            k, _, v = part.partition("=")
            if k in ("branch", "tag", "rev") and v:
                ref = v
        return Source("git", base, rev or None, ref)
    if src.startswith("path+"):
        return Source("path", src[len("path+") :])
    return Source("other", src)


def _pid(name: str, version: str | None, source: Any) -> str:
    s = name
    if version is not None:
        s += f" {version}"
    if isinstance(source, str):
        s += f" ({source})"
    return s


def parse(text: str) -> Lockfile:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise LockfileError(f"Cargo.lock: invalid TOML: {e}") from None
    except RecursionError:
        raise LockfileError("Cargo.lock: nesting too deep") from None
    raw_pkgs = doc.get("package", [])
    if not isinstance(raw_pkgs, list):
        raise LockfileError("Cargo.lock: 'package' must be an array of tables")
    root = doc.get("root")
    if isinstance(root, dict):
        raw_pkgs = [root, *raw_pkgs]
    version = doc.get("version")
    meta = doc.get("metadata", {})
    legacy_checksums: dict[tuple[str, str, str], str] = {}
    if isinstance(meta, dict):
        for k, v in meta.items():
            m = _META_CHECKSUM.match(k)
            if m and isinstance(v, str):
                legacy_checksums[(m.group(1), m.group(2), m.group(3))] = v
    if version is None:
        version = "1" if "root" in doc or legacy_checksums or "metadata" in doc else None

    packages: list[Package] = []
    for p in raw_pkgs:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            raise LockfileError("Cargo.lock: package entry without a name")
        name = p["name"]
        ver = p.get("version")
        if ver is not None and not isinstance(ver, str):
            raise LockfileError(f"Cargo.lock: bad version for {name}")
        src = p.get("source")
        checksum = p.get("checksum")
        if checksum is None and isinstance(src, str) and ver is not None:
            src_plain = src
            checksum = legacy_checksums.get((name, ver, src_plain))
        deps_raw = p.get("dependencies", [])
        if not isinstance(deps_raw, list) or not all(isinstance(d, str) for d in deps_raw):
            raise LockfileError(f"Cargo.lock: bad dependencies for {name}")
        packages.append(
            Package(
                id=_pid(name, ver, src),
                name=name,
                version=ver,
                source=_source(src),
                integrity=None if not isinstance(checksum, str) else "sha256:" + checksum,
                dependencies=tuple(Dependency(d, None, "prod") for d in deps_raw),
                raw=p,
            )
        )
    by_name: dict[str, list[Package]] = {}
    for pk in packages:
        by_name.setdefault(pk.name, []).append(pk)

    def target(dep: str) -> tuple[str, str | None, str | None]:
        m = _DEP.match(dep)
        if m is None:
            raise LockfileError(f"Cargo.lock: cannot read dependency {dep!r}")
        name, ver, src = m.groups()
        cands = by_name.get(name, [])
        if ver is not None:
            cands = [c for c in cands if c.version == ver]
        if src is not None:
            cands = [c for c in cands if isinstance(c.raw, dict) and c.raw.get("source") == src]
        return name, ver, cands[0].id if cands else None

    out: list[Package] = []
    for pk in packages:
        deps: list[Dependency] = []
        for d in pk.dependencies:
            name, ver, tid = target(d.name)
            deps.append(Dependency(name, ver, "prod", tid))
        out.append(replace(pk, dependencies=tuple(deps)))
    top = {k: v for k, v in doc.items() if k not in ("package", "root")}
    return Lockfile("cargo", None if version is None else str(version), tuple(out), raw=top)
