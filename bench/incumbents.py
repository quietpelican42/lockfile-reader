"""Measure Python lockfile parsers against the oracle output on the pinned corpus.

For each file the question is: does the tool return exactly the oracle's set of
(name, version) pairs? Names for which the oracle has no version (git and file
dependencies) or that are npm aliases are removed from every tool's set before
comparing, so no tool is penalized for information the oracle does not carry.
Files that no reference parser accepts (unresolved merge conflicts) are excluded. Run the corpus and oracle tests first so that
``.corpus/`` holds the files and ``.corpus/oracle/`` holds the oracle output:

    python -m pytest tests/test_oracle.py
    python -m venv /path/to/venv
    /path/to/venv/bin/pip install scancode-toolkit yarnlock pyarn lockfile-parser -e .
    /path/to/venv/bin/python bench/incumbents.py

scancode-toolkit imports libmagic at load time and no libmagic wheel exists for
macOS, so this script replaces ``typecode.magic2`` with a stub. The lockfile
handlers do not use it.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import sys
import tempfile
import types
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import corpus  # noqa: E402
import lockfile_reader  # noqa: E402

NV = set[tuple[str, str | None]]


class _Stub(types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def _scancode() -> dict[str, Callable[[Path], NV]]:
    sys.modules["typecode.magic2"] = _Stub("typecode.magic2")
    from packageurl import PackageURL
    from packagedcode import cargo, golang, npm, rubygems

    def get(o: Any, k: str) -> Any:
        return o.get(k) if isinstance(o, dict) else getattr(o, k, None)

    def run(handler: Any, fname: str, include_top: bool = False) -> Callable[[Path], NV]:
        def go(src: Path) -> NV:
            d = tempfile.mkdtemp()
            try:
                p = os.path.join(d, fname)
                shutil.copy(src, p)
                out: NV = set()
                for pd in handler.parse(p):
                    if include_top and get(pd, "name") and get(pd, "version"):
                        out.add((get(pd, "name"), get(pd, "version")))
                    for dep in get(pd, "dependencies") or []:
                        purl = get(dep, "purl")
                        if purl:
                            u = PackageURL.from_string(purl)
                            name = f"{u.namespace}/{u.name}" if u.namespace else u.name
                            if u.version is not None:  # an unresolved requirement is not a resolved package
                                out.add((name, u.version))
                return out
            finally:
                shutil.rmtree(d)

        return go

    return {
        "yarn-v1": run(npm.YarnLockV1Handler, "yarn.lock"),
        "yarn-berry": run(npm.YarnLockV2Handler, "yarn.lock"),
        "gemfile": run(rubygems.GemfileLockHandler, "Gemfile.lock", include_top=True),
        "npm": run(npm.NpmPackageLockJsonHandler, "package-lock.json"),
        "pnpm": run(npm.PnpmLockYamlHandler, "pnpm-lock.yaml"),
        "cargo": run(cargo.CargoLockHandler, "Cargo.lock"),
        "gosum": run(golang.GoSumHandler, "go.sum"),
    }


def split_pat(pat: str) -> tuple[str, str]:
    scoped = pat.startswith("@")
    body = pat[1:] if scoped else pat
    n, _, r = body.partition("@")
    return ("@" if scoped else "") + n, r


def _yarnlock(src: Path) -> NV:
    import yarnlock

    out: NV = set()
    for k, v in yarnlock.yarnlock_parse(src.read_text()).items():
        for part in re.split(r",\s*", k):  # yarnlock returns the header line as one key
            n, r = split_pat(part.strip().strip('"'))
            if not r.startswith("npm:"):
                out.add((n, v.get("version")))
    return out


def _yarnlock_berry(src: Path) -> NV:
    import yarnlock

    return {(re.split(r",\s*", k)[0].strip('"'), v.get("version")) for k, v in yarnlock.yarnlock_parse(src.read_text()).items()}


def _pyarn(src: Path) -> NV:
    from pyarn import lockfile

    out: NV = set()
    for p in lockfile.Lockfile.from_file(str(src)).packages():
        if not split_pat(p.name)[1].startswith("npm:"):
            out.add((p.name, p.version))
    return out


def _lockfile_parser_cargo(src: Path) -> NV:
    from lockfile_parser import CargoLockParser

    return {(n, p.get("version")) for n, p in CargoLockParser(str(src)).package_map.items()}


def oracle_set(fmt: str, o: Any) -> NV:
    if fmt == "yarn-v1":
        return {(split_pat(k)[0], v.get("version")) for k, v in o.items() if not split_pat(k)[1].startswith("npm:")}
    if fmt == "yarn-berry":
        return {(split_pat(v["resolution"])[0], str(v.get("version"))) for k, v in o.items() if k != "__metadata"}
    if fmt == "gemfile":
        return {(s["name"], s["version"]) for s in o["specs"]}
    if fmt == "npm":
        return {(n["packageName"], n["version"]) for n in o["nodes"] if not n["isLink"]}
    if fmt == "pnpm":
        out: NV = set()
        for k, p in o["parsed"].items():
            w = o["lf"]["packages"][k]
            nm, ver = p.get("name"), p.get("version")
            if nm is None:
                nm, ver = w.get("name"), w.get("version")
            out.add((nm, None if ver is None else str(ver)))
        return out
    if fmt == "cargo":
        return {(p["name"], p.get("version")) for p in list(o.get("root", {}) and [o["root"]]) + list(o.get("package", []))}
    if fmt == "gosum":
        out = set()
        for k in o:
            path, ver = k.split(" ", 1)
            out.add((path, ver[:-7] if ver.endswith("/go.mod") else ver))
        return out
    raise KeyError(fmt)


def _unknown_names(fmt: str, o: Any, want: NV) -> set[str]:
    names = {n for n, v in want if v in (None, "")}
    if fmt == "yarn-v1":  # alias entries: the key names an alias, the resolved package has another name
        for k in o:
            n, r = split_pat(k)
            if r.startswith("npm:"):
                names.add(n)
                names.add(split_pat(r[4:])[0])
    if fmt == "yarn-berry":
        for k in o:
            if k != "__metadata":
                for d in k.split(","):
                    n, r = split_pat(d.strip())
                    if r.startswith("npm:") and "@" in r[4:]:
                        names.add(n)
                        names.add(split_pat(r[4:])[0])
    if fmt == "npm":
        for n in o["nodes"]:
            if n["name"] != n["packageName"]:
                names.add(n["name"])
                names.add(n["packageName"])
    return names


def main() -> None:
    corpus.fetch()
    sc = _scancode()
    tools: dict[str, dict[str, Callable[[Path], NV]]] = {
        "yarn-v1": {"scancode-toolkit": sc["yarn-v1"], "yarnlock": _yarnlock, "pyarn": _pyarn},
        "yarn-berry": {"scancode-toolkit": sc["yarn-berry"], "yarnlock": _yarnlock_berry, "pyarn": _pyarn},
        "gemfile": {"scancode-toolkit": sc["gemfile"]},
        "npm": {"scancode-toolkit": sc["npm"]},
        "pnpm": {"scancode-toolkit": sc["pnpm"]},
        "cargo": {"scancode-toolkit": sc["cargo"], "lockfile-parser": _lockfile_parser_cargo},
        "gosum": {"scancode-toolkit": sc["gosum"]},
    }
    for fmt in ("yarn-v1", "yarn-berry", "gemfile", "npm", "pnpm", "cargo", "gosum"):
        tools[fmt]["lockfile-reader"] = lambda src, fmt=fmt: {(p.name, p.version) for p in lockfile_reader.parse(src.read_text(), format=fmt)}  # type: ignore[misc]
    print("| format | files | " + " | ".join(["scancode-toolkit", "yarnlock", "pyarn", "lockfile-parser", "lockfile-reader"]) + " |")
    for fmt, ts in tools.items():
        entries = [e for e in corpus.entries(fmt) if e.local.exists() and "merge-conflict" not in e.note]
        score: dict[str, collections.Counter[str]] = {t: collections.Counter() for t in ts}
        for e in entries:
            f = corpus.CACHE / "oracle" / fmt / fmt / (e.local.name + ".json")
            if not f.exists():
                continue
            o = json.loads(f.read_text())
            want = oracle_set(fmt, o)
            skip = _unknown_names(fmt, o, want)
            want = {(n, v) for n, v in want if n not in skip}
            for t, fn in ts.items():
                try:
                    got = {(n, v) for n, v in fn(e.local) if n not in skip}
                except Exception as exc:  # noqa: BLE001
                    score[t]["error"] += 1
                    score[t]["e:" + type(exc).__name__] += 1
                    continue
                score[t]["exact" if got == want else "wrong"] += 1
        n = len(entries)
        cells = []
        for t in ["scancode-toolkit", "yarnlock", "pyarn", "lockfile-parser", "lockfile-reader"]:
            if t in ts:
                c = score[t]
                cells.append(f"{c['exact']}/{n}" + (f" ({c['error']} errors)" if c["error"] else ""))
            else:
                cells.append("n/a")
        print(f"| {fmt} | {n} | " + " | ".join(cells) + " |")
    print()
    # The (name, version) sets above re-split yarnlock's header keys. Without that step:
    import yarnlock
    from pyarn import lockfile

    n = ok_yl = ok_pa = ok_me = 0
    for e in corpus.entries("yarn-v1"):
        f = corpus.CACHE / "oracle" / "yarn-v1" / "yarn-v1" / (e.local.name + ".json")
        if not e.local.exists() or not f.exists():
            continue
        want_keys = set(json.loads(f.read_text()))
        n += 1
        ok_yl += set(yarnlock.yarnlock_parse(e.local.read_text())) == want_keys
        ok_pa += set(lockfile.Lockfile.from_file(str(e.local)).data) == want_keys
        ok_me += {s for p in lockfile_reader.parse(e.local.read_text(), format="yarn-v1") for s in p.specifiers} == want_keys
    print(f"yarn-v1 files whose specifier keys equal @yarnpkg/lockfile's keys as returned: yarnlock {ok_yl}/{n}, pyarn {ok_pa}/{n}, lockfile-reader {ok_me}/{n}")


if __name__ == "__main__":
    main()
