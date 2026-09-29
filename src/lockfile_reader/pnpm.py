"""pnpm-lock.yaml, lockfileVersion 5 through 9."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import _yaml
from .model import Dependency, Lockfile, LockfileError, Package, Source


def _major(v: Any) -> int:
    try:
        return int(str(v).split(".", 1)[0])
    except ValueError:
        raise LockfileError(f"pnpm-lock.yaml: bad lockfileVersion {v!r}") from None


def _map(value: Any, what: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LockfileError(f"pnpm-lock.yaml: {what} must be a mapping")
    return value


def _split_key(key: str, major: int, entry: dict[str, Any]) -> tuple[str, str | None, str]:
    """Return ``(name, version, peer_suffix)`` for a package key."""
    name_field = entry.get("name")
    ver_field = entry.get("version")
    if major >= 9 or (major >= 6 and key.startswith("/")):
        body = key[1:] if key.startswith("/") else key
        base, paren, suffix = body.partition("(")
        suffix = paren + suffix
        at = base.find("@", 1)
        if at < 0:
            name, version = base, None
        else:
            name, version = base[:at], base[at + 1 :]
        if isinstance(ver_field, (str, int, float)):
            version = str(ver_field)
        if isinstance(name_field, str):
            name = name_field
        return name, version, suffix
    # v5, or non-registry keys in v6/v7/v8 (host/path/commit)
    if isinstance(name_field, str) and (ver_field is not None):
        return name_field, str(ver_field), ""
    body = key
    from_registry = body.startswith("/")
    body = body.lstrip("/")
    segs = body.split("/")
    if not from_registry:
        if isinstance(name_field, str):
            return name_field, None if ver_field is None else str(ver_field), ""
        if len(segs) > 1:
            segs = segs[1:]
    if major >= 6:
        base, paren, suffix = body.partition("(")
        at = base.find("@", 1)
        if at > 0:
            return base[:at], base[at + 1 :], paren + suffix
    if segs and segs[0].startswith("@") and len(segs) >= 3:
        name, rest = segs[0] + "/" + segs[1], "/".join(segs[2:])
    elif len(segs) >= 2:
        name, rest = segs[0], "/".join(segs[1:])
    else:
        raise LockfileError(f"pnpm-lock.yaml: cannot read package key {key!r}")
    version, sep, suffix = rest.partition("_")
    return name, version, sep + suffix


def _source(res: Any) -> tuple[Source | None, str | None]:
    if not isinstance(res, dict):
        return None, None
    integrity = res.get("integrity")
    integ = str(integrity) if integrity is not None else None
    typ = res.get("type")
    if typ == "git":
        return Source("git", _s(res.get("repo")), _s(res.get("commit"))), integ
    if typ == "directory":
        return Source("path", _s(res.get("directory"))), integ
    tarball = _s(res.get("tarball"))
    if tarball is not None:
        return Source("registry" if integ else "tarball", tarball), integ
    if integ is not None:
        return Source("registry", None), integ
    return None, integ


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _documents(text: str) -> list[str]:
    docs: list[list[str]] = [[]]
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.rstrip() == "---":
            docs.append([])
        else:
            docs[-1].append(line)
    out = ["\n".join(d) for d in docs if any(x.strip() and not x.lstrip().startswith("#") for x in d)]
    return out or [text]


def parse(text: str) -> Lockfile:
    """Parse a pnpm lockfile. With several YAML documents (pnpm 10+), the last is the project."""
    docs = _documents(text)
    main = _parse_document(docs[-1])
    if len(docs) == 1:
        return main
    env = tuple(_parse_document(d) for d in docs[:-1])
    return replace(main, raw={**main.raw, "env_lockfiles": env})


def _parse_document(text: str) -> Lockfile:
    doc = _yaml.loads(text)
    if not isinstance(doc, dict) or "lockfileVersion" not in doc:
        raise LockfileError("pnpm-lock.yaml: missing lockfileVersion")
    version = str(doc["lockfileVersion"])
    major = _major(version)
    pkgs = _map(doc.get("packages"), "packages")
    snaps = _map(doc.get("snapshots"), "snapshots") if major >= 9 else {}

    entries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []  # (id, meta, install-entry)
    if major >= 9:
        seen = set()
        for sk, sv in snaps.items():
            base = str(sk).split("(", 1)[0]
            meta = pkgs.get(base)
            meta_d = meta if isinstance(meta, dict) else {}
            entries.append((str(sk), meta_d, _map(sv, f"snapshot {sk}")))
            seen.add(base)
        for pk, pv in pkgs.items():
            if pk not in seen:
                entries.append((str(pk), _map(pv, f"package {pk}"), {}))
    else:
        for pk, pv in pkgs.items():
            d = _map(pv, f"package {pk}")
            entries.append((str(pk), d, d))

    ids = {e[0] for e in entries}

    def resolve(name: str, value: Any) -> str | None:
        if value is None:
            return None
        v = str(value)
        if v.startswith("link:") and v not in ids:
            return None
        for cand in (v, f"/{name}/{v}", f"/{name}@{v}", f"{name}@{v}"):
            if cand in ids:
                return cand
        return None

    packages: list[Package] = []
    for pid, meta, inst in entries:
        merged = {**meta, **inst}
        name, ver, suffix = _split_key(pid, major, {**meta} if major >= 9 else meta)
        source, integ = _source(meta.get("resolution"))
        deps: list[Dependency] = []
        for field, kind in (("dependencies", "prod"), ("optionalDependencies", "optional")):
            for dn, dv in _map(inst.get(field), field).items():
                deps.append(Dependency(str(dn), _s(dv), kind, resolve(str(dn), dv)))
        for dn, dv in _map(meta.get("peerDependencies"), "peerDependencies").items():
            deps.append(Dependency(str(dn), _s(dv), "peer"))
        packages.append(
            Package(
                id=pid,
                name=name,
                version=ver,
                source=source,
                integrity=integ,
                dependencies=tuple(deps),
                dev=merged.get("dev") is True,
                optional=merged.get("optional") is True,
                bundled=bool(meta.get("bundledDependencies")),
                raw={"meta": meta, "install": inst, "peer_suffix": suffix} if major >= 9 else meta,
            )
        )

    roots: dict[str, tuple[Dependency, ...]] = {}
    importers = doc.get("importers")
    if importers is None and ("dependencies" in doc or "devDependencies" in doc or "optionalDependencies" in doc):
        importers = {".": {k: doc[k] for k in ("specifiers", "dependencies", "devDependencies", "optionalDependencies") if k in doc}}
    for path, imp in _map(importers, "importers").items():
        imp_d = _map(imp, f"importer {path}")
        specifiers = _map(imp_d.get("specifiers"), "specifiers")
        rdeps: list[Dependency] = []
        for field, kind in (("dependencies", "prod"), ("devDependencies", "dev"), ("optionalDependencies", "optional")):
            for dn, dv in _map(imp_d.get(field), field).items():
                if isinstance(dv, dict):
                    spec, ref = _s(dv.get("specifier")), dv.get("version")
                else:
                    spec, ref = _s(specifiers.get(dn)), dv
                rdeps.append(Dependency(str(dn), spec, kind, resolve(str(dn), ref)))
        roots[str(path)] = tuple(rdeps)

    top = {k: v for k, v in doc.items() if k not in ("packages", "snapshots", "importers", "lockfileVersion")}
    return Lockfile("pnpm", version, tuple(packages), roots=roots, raw=top)

