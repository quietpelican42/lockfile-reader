"""mix.lock: an Elixir map of ``app => tuple`` written by Mix."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import _terms
from .model import Dependency, Lockfile, LockfileError, Package, Source


def _kw(value: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                out[str(item[0])] = item[1]
    return out


def _s(value: Any) -> str | None:
    return value if isinstance(value, str) and not isinstance(value, _terms.Atom) else None


def parse(text: str) -> Lockfile:
    term = _terms.loads(text)
    if not isinstance(term, dict):
        raise LockfileError("mix.lock: expected a map at top level")
    packages: list[Package] = []
    for key, entry in term.items():
        app = str(key)
        if not isinstance(entry, tuple) or not entry or not isinstance(entry[0], _terms.Atom):
            raise LockfileError(f"mix.lock: entry {app!r} is not a tagged tuple")
        tag = str(entry[0])
        if tag == "hex":
            if len(entry) < 3 or not isinstance(entry[1], str):
                raise LockfileError(f"mix.lock: malformed :hex entry {app!r}")
            name = str(entry[1])
            version = _s(entry[2])
            inner = _s(entry[3]) if len(entry) > 3 else None
            managers = entry[4] if len(entry) > 4 else None
            deps_raw = entry[5] if len(entry) > 5 else []
            repo = _s(entry[6]) if len(entry) > 6 else "hexpm"
            outer = _s(entry[7]) if len(entry) > 7 else None
            deps: list[Dependency] = []
            if not isinstance(deps_raw, list):
                raise LockfileError(f"mix.lock: malformed dependency list for {app!r}")
            for d in deps_raw:
                if not (isinstance(d, tuple) and len(d) >= 2 and isinstance(d[0], str)):
                    raise LockfileError(f"mix.lock: malformed dependency in {app!r}")
                opts = _kw(d[2]) if len(d) > 2 else {}
                deps.append(
                    Dependency(str(d[0]), _s(d[1]), "optional" if opts.get("optional") is True else "prod")
                )
            digest = outer or inner
            packages.append(
                Package(
                    id=app,
                    name=name,
                    version=version,
                    source=Source("registry", repo),
                    integrity=None if digest is None else "sha256:" + digest,
                    dependencies=tuple(deps),
                    raw={"app": app, "tuple": entry, "managers": managers, "inner_checksum": inner, "outer_checksum": outer},
                )
            )
        elif tag == "git":
            url = _s(entry[1]) if len(entry) > 1 else None
            rev = _s(entry[2]) if len(entry) > 2 else None
            opts = _kw(entry[3]) if len(entry) > 3 else {}
            ref = None
            for k in ("tag", "branch", "ref"):
                if isinstance(opts.get(k), str):
                    ref = opts[k]
                    break
            packages.append(
                Package(id=app, name=app, version=None, source=Source("git", url, rev, ref), raw={"app": app, "tuple": entry, "opts": opts})
            )
        elif tag == "path":
            url = _s(entry[1]) if len(entry) > 1 else None
            packages.append(Package(id=app, name=app, version=None, source=Source("path", url), raw={"app": app, "tuple": entry}))
        else:
            packages.append(Package(id=app, name=app, version=None, source=Source("other", tag), raw={"app": app, "tuple": entry}))
    ids = {p.id for p in packages}
    out = [
        replace(p, dependencies=tuple(replace(d, target=d.name if d.name in ids else None) for d in p.dependencies))
        for p in packages
    ]
    return Lockfile("mix", None, tuple(out))
