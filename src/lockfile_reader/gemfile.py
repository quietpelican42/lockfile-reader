"""Gemfile.lock as written by Bundler 1.x and 2.x."""

from __future__ import annotations

import re
from dataclasses import replace

from .model import Dependency, Lockfile, LockfileError, Package, Source

_SOURCE_HEADERS = {"GEM": "registry", "GIT": "git", "PATH": "path", "SVN": "svn", "PLUGIN SOURCE": "plugin"}
_TOP = {"DEPENDENCIES", "PLATFORMS", "RUBY VERSION", "BUNDLED WITH", "CHECKSUMS", "SOURCES"}
_NAME_VERSION = re.compile(r"^(?! )(.*?)(?: \(([^-]*?)(?:-(.*?))?\))?(!)?$")
_CHECKSUM = re.compile(r"^(\S+) \(([^)]*)\)(?: (.+))?$")


def _split_name_version(s: str, no: int) -> tuple[str, str | None, str | None, bool]:
    m = _NAME_VERSION.match(s)
    if m is None:
        raise LockfileError(f"Gemfile.lock line {no}: cannot read {s!r}")
    name, version, platform, bang = m.groups()
    return name, version, platform, bool(bang)


def _first(opts: dict[str, list[str]], key: str) -> str | None:
    vals = opts.get(key)
    return vals[0] if vals else None


def _section_source(kind: str, opts: dict[str, list[str]]) -> Source:
    remote = _first(opts, "remote")
    rev = _first(opts, "revision")
    ref = _first(opts, "tag") or _first(opts, "branch") or _first(opts, "ref")
    if kind == "registry":
        return Source("registry", remote)
    if kind == "git":
        return Source("git", remote, rev, ref)
    if kind == "path":
        return Source("path", remote)
    if kind == "svn":
        return Source("other", remote, rev)
    return Source("other", remote)


def parse(text: str) -> Lockfile:
    lines = text.replace("\r\n", "\n").split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(("<<<<<<<", "=======", ">>>>>>>")):
            raise LockfileError(f"Gemfile.lock line {i + 1}: unresolved merge conflict marker")

    packages: list[Package] = []
    raw_deps: list[list[tuple[str, str | None]]] = []
    root_deps: list[Dependency] = []
    pinned: list[str] = []
    platforms: list[str] = []
    ruby_version: str | None = None
    bundled_with: str | None = None
    checksums: dict[tuple[str, str], str] = {}
    sections: list[dict[str, list[str]]] = []

    mode = ""
    opts: dict[str, list[str]] = {}
    src_kind = ""
    in_specs = False
    cur_pkg_idx = -1
    seen_header = False

    for idx, line in enumerate(lines):
        no = idx + 1
        if not line.strip():
            continue
        if not line.startswith(" "):
            seen_header = True
            header = line.rstrip()
            if header in _SOURCE_HEADERS:
                mode = "source"
                src_kind = _SOURCE_HEADERS[header]
                opts = {}
                sections.append(opts)
                opts["_kind"] = [header]
                in_specs = False
            elif header in _TOP:
                mode = header
            else:
                raise LockfileError(f"Gemfile.lock line {no}: unknown section {header!r}")
            continue
        if not seen_header:
            raise LockfileError(f"Gemfile.lock line {no}: content before first section")
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if mode == "source":
            if indent == 2:
                if body == "specs:":
                    in_specs = True
                    continue
                key, sep, val = body.partition(": ")
                if not sep:
                    key = body.rstrip(":")
                    val = ""
                if in_specs and key != "specs":
                    in_specs = False
                opts.setdefault(key, []).append(val)
            elif in_specs and indent == 4:
                name, version, platform, _ = _split_name_version(line[4:], no)
                pid = f"{name}@{version}" + (f"-{platform}" if platform else "")
                src = _section_source(src_kind, opts)
                packages.append(
                    Package(
                        id=pid + f"#{len(packages)}" if any(p.id == pid for p in packages) else pid,
                        name=name,
                        version=version,
                        source=src,
                        platform=platform,
                        raw={"section": opts["_kind"][0], "remotes": tuple(opts.get("remote", ()))},
                    )
                )
                raw_deps.append([])
                cur_pkg_idx = len(packages) - 1
            elif in_specs and indent == 6:
                if cur_pkg_idx < 0:
                    raise LockfileError(f"Gemfile.lock line {no}: dependency before any gem")
                m = re.match(r"^(\S+)(?: \((.*)\))?$", body)
                if m is None:
                    raise LockfileError(f"Gemfile.lock line {no}: cannot read dependency {body!r}")
                dname, req = m.group(1), m.group(2)
                raw_deps[cur_pkg_idx].append((dname, req))
            else:
                raise LockfileError(f"Gemfile.lock line {no}: unexpected indentation")
        elif mode == "DEPENDENCIES":
            if indent != 2:
                raise LockfileError(f"Gemfile.lock line {no}: unexpected indentation in DEPENDENCIES")
            m2 = re.match(r"^(\S+?)(?: \((.*)\))?(!)?$", body)
            if m2 is None:
                raise LockfileError(f"Gemfile.lock line {no}: cannot read dependency {body!r}")
            root_deps.append(Dependency(m2.group(1), m2.group(2), "prod"))
            if m2.group(3):
                pinned.append(m2.group(1))
        elif mode == "PLATFORMS":
            platforms.append(body)
        elif mode == "RUBY VERSION":
            if body.startswith("ruby "):
                ruby_version = body[5:]
            elif ruby_version is None:
                ruby_version = body
        elif mode == "BUNDLED WITH":
            bundled_with = body
        elif mode == "CHECKSUMS":
            m3 = _CHECKSUM.match(body)
            if m3 is None:
                raise LockfileError(f"Gemfile.lock line {no}: bad checksum line")
            if m3.group(3):
                checksums[(m3.group(1), m3.group(2))] = m3.group(3)
        elif mode == "SOURCES":
            pass

    if not packages and not root_deps and not seen_header:
        raise LockfileError("Gemfile.lock: no sections found")

    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(packages):
        by_name.setdefault(p.name, []).append(i)
    out: list[Package] = []
    for i, p in enumerate(packages):
        deps = []
        for dn, req in raw_deps[i]:
            deps.append(Dependency(dn, req, "prod", _pick(packages, by_name.get(dn, []), p.platform)))
        cs = checksums.get((p.name, f"{p.version}" + (f"-{p.platform}" if p.platform else "")))
        out.append(
            replace(
                p,
                dependencies=tuple(deps),
                integrity=None if cs is None else cs.replace("=", ":", 1),
            )
        )
    roots = tuple(replace(d, target=_pick(packages, by_name.get(d.name, []), None)) for d in root_deps)
    return Lockfile(
        "gemfile",
        bundled_with,
        tuple(out),
        roots={"": roots},
        raw={
            "platforms": tuple(platforms),
            "source_pinned": tuple(pinned),
            "checksums": {f"{n} ({v})": c.replace("=", ":", 1) for (n, v), c in checksums.items()},
            "ruby_version": ruby_version,
            "bundled_with": bundled_with,
            "sources": tuple({k: tuple(v) for k, v in s.items()} for s in sections),
        },
    )


def _pick(packages: list[Package], idxs: list[int], platform: str | None) -> str | None:
    if not idxs:
        return None
    for i in idxs:
        if packages[i].platform == platform:
            return packages[i].id
    for i in idxs:
        if packages[i].platform is None:
            return packages[i].id
    return packages[idxs[0]].id
