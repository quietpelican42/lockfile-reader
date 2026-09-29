"""package-lock.json / npm-shrinkwrap.json, lockfileVersion 1, 2 and 3."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .model import Dependency, Lockfile, LockfileError, Package, Source, make_source_from_url

_NM = "node_modules/"


def _name_from_path(path: str) -> str:
    idx = path.rfind(_NM)
    if idx >= 0:
        return path[idx + len(_NM) :]
    return path.rsplit("/", 1)[-1]


def _source(entry: dict[str, Any]) -> Source | None:
    resolved = entry.get("resolved")
    if entry.get("link") is True:
        return Source("link", resolved if isinstance(resolved, str) else None)
    if isinstance(resolved, str):
        src = make_source_from_url(resolved)
        if src.kind in ("registry", "tarball"):
            return Source(src.kind, resolved)
        return src
    if entry.get("link") is True:
        return Source("link", None)
    ver = entry.get("version")
    if isinstance(ver, str):
        if ver.startswith(("file:", "link:")) or ver.startswith(("git+", "github:", "git:", "http")):
            return make_source_from_url(ver)
    return None


def _obj(value: Any, what: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LockfileError(f"package-lock.json: {what} must be an object")
    return value


def _edges(entry: dict[str, Any]) -> list[Dependency]:
    out: list[Dependency] = []
    for f, kind in (
        ("dependencies", "prod"),
        ("devDependencies", "dev"),
        ("optionalDependencies", "optional"),
        ("peerDependencies", "peer"),
    ):
        for n, spec in _obj(entry.get(f), f).items():
            out.append(Dependency(str(n), None if spec is None else str(spec), kind))
    return out


def _parent(path: str) -> str | None:
    """Next directory up from ``path`` in Node's module lookup (``''`` is the project root)."""
    if not path or path == "..":
        return None
    return path.rsplit("/", 1)[0] if "/" in path else ""


def parse(text: str) -> Lockfile:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise LockfileError(f"package-lock.json: invalid JSON at line {e.lineno}: {e.msg}") from None
    except RecursionError:
        raise LockfileError("package-lock.json: nesting too deep") from None
    if not isinstance(doc, dict):
        raise LockfileError("package-lock.json: expected an object")
    ver = doc.get("lockfileVersion")
    if isinstance(doc.get("packages"), dict):
        return _parse_flat(doc, ver)
    if isinstance(doc.get("dependencies"), dict) or "lockfileVersion" in doc:
        return _parse_nested(doc, ver)
    raise LockfileError("package-lock.json: neither 'packages' nor 'dependencies' found")


def _listed_as_bundled(raw_pkgs: dict[str, Any], path: str) -> bool:
    """True when the package that owns ``path``'s node_modules folder lists it in ``bundleDependencies``."""
    idx = path.rfind("/" + _NM)
    if idx < 0:
        return False
    owner = raw_pkgs.get(path[:idx])
    if not isinstance(owner, dict):
        return False
    listed = owner.get("bundleDependencies", owner.get("bundledDependencies"))
    if listed is True:
        return True
    return isinstance(listed, list) and path[idx + 1 + len(_NM) :] in listed


def _parse_flat(doc: dict[str, Any], ver: Any) -> Lockfile:
    raw_pkgs: dict[str, Any] = doc["packages"]
    ids = set(raw_pkgs)
    packages: list[Package] = []
    roots: dict[str, tuple[Dependency, ...]] = {}

    def resolve(owner: str, dep: str) -> str | None:
        base: str | None = owner
        while base is not None:
            cand = f"{base}/{_NM}{dep}" if base else f"{_NM}{dep}"
            if cand in ids:
                return cand
            base = _parent(base)
        return None

    link_targets: dict[str, str] = {}
    for path, entry in raw_pkgs.items():
        if not isinstance(entry, dict):
            raise LockfileError(f"package-lock.json: entry {path!r} is not an object")
        if entry.get("link") is True and isinstance(entry.get("resolved"), str):
            link_targets[path] = entry["resolved"]

    for path, entry in raw_pkgs.items():
        if path == "":
            roots[""] = tuple(replace(d, target=resolve("", d.name)) for d in _edges(entry))
            continue
        name = _name_from_path(path)
        alias: str | None = None
        real = entry.get("name")
        if isinstance(real, str) and real != name:
            alias = name if _NM in path else None
            name = real
        version = entry.get("version")
        if version is None and path in link_targets:
            tgt = raw_pkgs.get(link_targets[path])
            if isinstance(tgt, dict):
                version = tgt.get("version")
        deps = tuple(replace(d, target=resolve(path, d.name)) for d in _edges(entry))
        integ = entry.get("integrity")
        packages.append(
            Package(
                id=path,
                name=name,
                version=None if version is None else str(version),
                source=_source(entry),
                integrity=integ if isinstance(integ, str) else None,
                dependencies=deps,
                dev=entry.get("dev") is True or entry.get("devOptional") is True,
                optional=entry.get("optional") is True or entry.get("devOptional") is True,
                bundled=entry.get("inBundle") is True or _listed_as_bundled(raw_pkgs, path),
                alias=alias,
                raw=entry,
            )
        )
    meta = {k: v for k, v in doc.items() if k != "packages"}
    return Lockfile("npm", None if ver is None else str(ver), tuple(packages), roots=roots, raw=meta)


def _parse_nested(doc: dict[str, Any], ver: Any) -> Lockfile:
    packages: list[Package] = []
    pending: list[tuple[str, dict[str, Any], list[Dependency]]] = []
    ids: set[str] = set()

    def walk(deps: dict[str, Any], base: str, depth: int) -> None:
        if depth > 400:
            raise LockfileError("package-lock.json: dependency tree too deep")
        for name, entry in deps.items():
            if not isinstance(entry, dict):
                raise LockfileError(f"package-lock.json: entry {name!r} is not an object")
            pid = f"{base}{_NM}{name}"
            ids.add(pid)
            pending.append((pid, entry, []))
            walk(_obj(entry.get("dependencies"), "dependencies"), pid + "/", depth + 1)

    walk(_obj(doc.get("dependencies"), "dependencies"), "", 0)

    def resolve(owner: str, dep: str) -> str | None:
        base: str | None = owner
        while base is not None:
            cand = f"{base}/{_NM}{dep}" if base else f"{_NM}{dep}"
            if cand in ids:
                return cand
            base = _parent(base)
        return None

    optional_ids = {pid for pid, entry, _ in pending if entry.get("optional") is True}
    for pid, entry, _ in pending:
        name = _name_from_path(pid)
        version = entry.get("version")
        alias: str | None = None
        vs = None if version is None else str(version)
        if vs is not None and vs.startswith(("git+", "git:", "github:", "gitlab:", "bitbucket:", "http:", "https:", "file:", "link:", "git@", "ssh:")):
            vs = None  # v1 stores the source URL in "version" for non-registry packages
        elif vs is not None and vs.startswith("npm:"):
            at = vs.find("@", 5)
            real_name = vs[4:at] if at > 0 else vs[4:]
            vs = vs[at + 1 :] if at > 0 else None
            if real_name != name:
                alias, name = name, real_name
        integ = entry.get("integrity")
        requires = _obj(entry.get("requires"), "requires")
        deps = []
        for n, s in requires.items():
            tgt = resolve(pid, str(n))
            # v1 records no edge kind. An edge from a non-optional package into an optional one is optional.
            deps.append(Dependency(str(n), None if s is None else str(s), "optional" if tgt in optional_ids and entry.get("optional") is not True else "prod", tgt))
        packages.append(
            Package(
                id=pid,
                name=name,
                version=vs,
                source=_source(entry),
                integrity=integ if isinstance(integ, str) else None,
                dependencies=tuple(deps),
                dev=entry.get("dev") is True,
                optional=entry.get("optional") is True,
                bundled=entry.get("bundled") is True,
                alias=alias,
                raw=entry,
            )
        )
    meta = {k: v for k, v in doc.items() if k != "dependencies"}
    return Lockfile("npm", None if ver is None else str(ver), tuple(packages), raw=meta)
