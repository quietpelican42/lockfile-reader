"""Compare lockfile_reader output with the JSON produced by each format's oracle.

Each function returns a list of human-readable differences (empty means identical on
everything compared). Oracle output comes from the scripts in ``tests/oracle/``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lockfile_reader import Lockfile, Package

Diff = list[str]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


# -- yarn v1 (oracle: @yarnpkg/lockfile) -------------------------------------------------
def yarn_v1(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    mine: dict[str, Package] = {}
    for p in lf.packages:
        for spec in p.specifiers:
            mine[spec] = p
    if set(mine) != set(oracle):
        diffs.append(f"specifier keys differ: only-mine={sorted(set(mine) - set(oracle))[:3]} only-oracle={sorted(set(oracle) - set(mine))[:3]}")
        return diffs
    for spec, want in oracle.items():
        p = mine[spec]
        if p.version != _s(want.get("version")):
            diffs.append(f"{spec}: version {p.version!r} != {want.get('version')!r}")
        if p.integrity != want.get("integrity"):
            diffs.append(f"{spec}: integrity differs")
        if (p.raw or {}).get("resolved") != want.get("resolved"):
            diffs.append(f"{spec}: resolved differs")
        if (p.source is None) != (want.get("resolved") is None):
            diffs.append(f"{spec}: source presence differs")
        for kind, field in (("prod", "dependencies"), ("optional", "optionalDependencies")):
            got = {d.name: d.spec for d in p.dependencies if d.kind == kind}
            if got != {k: str(v) for k, v in (want.get(field) or {}).items()}:
                diffs.append(f"{spec}: {field} differ")
        for d in p.dependencies:
            if d.target is not None and d.spec is not None:
                if f"{d.name}@{d.spec}" not in mine[d.target].specifiers:
                    diffs.append(f"{spec}: edge {d.name}@{d.spec} resolved to wrong entry")
    return diffs


# -- yarn berry (oracle: @yarnpkg/parsers parseSyml) ----------------------------------------
def yarn_berry(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    want_entries = {k: v for k, v in oracle.items() if k != "__metadata"}
    mine = {", ".join(p.specifiers): p for p in lf.packages}
    if set(mine) != set(want_entries):
        return [f"entry headers differ: only-mine={sorted(set(mine) - set(want_entries))[:3]} only-oracle={sorted(set(want_entries) - set(mine))[:3]}"]
    meta_version = str(oracle["__metadata"].get("version"))
    if lf.version != meta_version:
        diffs.append(f"metadata version {lf.version} != {meta_version}")
    index = {s: p.id for p in lf.packages for s in p.specifiers}
    for header, want in want_entries.items():
        p = mine[header]
        if p.id != want["resolution"]:
            diffs.append(f"{header}: resolution differs")
        if p.version != _s(want.get("version")):
            diffs.append(f"{header}: version {p.version!r} != {want.get('version')!r}")
        if p.integrity != want.get("checksum"):
            diffs.append(f"{header}: checksum differs")
        raw = p.raw or {}
        for field in ("languageName", "linkType"):
            if raw.get(field) != want.get(field):
                diffs.append(f"{header}: {field} differs")
        deps = {d.name: d.spec for d in p.dependencies if d.kind in ("prod", "optional")}
        if deps != {k: str(v) for k, v in (want.get("dependencies") or {}).items()}:
            diffs.append(f"{header}: dependencies differ")
        peers = {d.name: d.spec for d in p.dependencies if d.kind == "peer"}
        if peers != {k: str(v) for k, v in (want.get("peerDependencies") or {}).items()}:
            diffs.append(f"{header}: peerDependencies differ")
        meta = want.get("dependenciesMeta") or {}
        opt = {d.name for d in p.dependencies if d.kind == "optional"}
        want_opt = {k for k, v in meta.items() if isinstance(v, dict) and v.get("optional") in (True, "true")} & set(deps)
        if opt != want_opt:
            diffs.append(f"{header}: optional flags differ")
        for d in p.dependencies:
            if d.kind == "peer":
                continue
            hit = index.get(f"{d.name}@{d.spec}") or index.get(f"{d.name}@npm:{d.spec}")
            if d.target != hit:
                diffs.append(f"{header}: edge {d.name}@{d.spec} target differs")
    return diffs


# -- Gemfile.lock (oracle: Bundler::LockfileParser 1.17.2) --------------------------------
def _norm_req(req: str | None) -> tuple[str, ...]:
    if not req:
        return (">= 0",)
    return tuple(sorted(r.strip() for r in req.split(",")))


def gemfile(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    pmap: dict[str, str] = oracle.get("platform_map", {})
    want = {(s["name"], s["version"], s["platform"]): s for s in oracle["specs"]}
    # Bundler runs every platform string through Gem::Platform.new; the oracle recorded that mapping
    mine = {(p.name, p.version, pmap.get(p.platform or "ruby", p.platform or "ruby")): p for p in lf.packages}
    if set(mine) != set(want):
        return [f"spec sets differ: only-mine={sorted(set(mine) - set(want))[:3]} only-oracle={sorted(set(want) - set(mine))[:3]}"]
    for key, w in want.items():
        p = mine[key]
        got_deps = sorted((d.name, _norm_req(d.spec)) for d in p.dependencies)
        want_deps = sorted((n, _norm_req(r)) for n, r in w["deps"])
        if got_deps != want_deps:
            diffs.append(f"{key}: dependency edges differ")
        src = w["source"]
        s = p.source
        assert s is not None
        kind = {"Bundler::Source::Rubygems": "registry", "Bundler::Source::Git": "git", "Bundler::Source::Path": "path"}.get(src["class"], "other")
        if s.kind != kind:
            diffs.append(f"{key}: source kind {s.kind} != {kind}")
        if kind == "git":
            if s.url != src.get("uri") or s.revision != src.get("revision"):
                diffs.append(f"{key}: git source differs")
            if s.ref != (src.get("tag") or src.get("branch") or src.get("ref")):
                diffs.append(f"{key}: git ref differs")
        if kind == "registry":
            # Bundler merges every GEM section into one source and normalises remotes (trailing slash)
            mine_remotes = {r.rstrip("/") for r in (p.raw or {})["remotes"]}
            if not mine_remotes <= {r.rstrip("/") for r in src["remotes"]}:
                diffs.append(f"{key}: remotes differ")
        for d in p.dependencies:
            if d.target is None and any(q.name == d.name for q in lf.packages):
                diffs.append(f"{key}: edge {d.name} not resolved")
    want_roots = sorted((n, _norm_req(r)) for n, r, _ in oracle["roots"])
    got_roots = sorted((d.name, _norm_req(d.spec)) for d in lf.roots[""])
    if want_roots != got_roots:
        diffs.append("DEPENDENCIES differ")
    if sorted(oracle["platforms"]) != sorted(pmap.get(x, x) for x in lf.raw["platforms"]):
        diffs.append("PLATFORMS differ")
    return diffs


# -- package-lock (oracle: @npmcli/arborist loadVirtual) -------------------------------------
_KIND = {"prod": "prod", "dev": "dev", "optional": "optional", "peer": "peer", "peerOptional": "peer", "workspace": "prod"}


def npm(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    want = {n["location"]: n for n in oracle["nodes"]}
    mine = {p.id: p for p in lf.packages}
    if set(mine) != set(want):
        return [f"locations differ: only-mine={sorted(set(mine) - set(want))[:3]} only-oracle={sorted(set(want) - set(mine))[:3]}"]
    for loc, w in want.items():
        p = mine[loc]
        if p.name != w["packageName"] and p.name != w["name"]:
            diffs.append(f"{loc}: name {p.name!r} not in {w['packageName']!r}/{w['name']!r}")
        if p.version != (_s(w["version"]) or None):
            diffs.append(f"{loc}: version {p.version!r} != {w['version']!r}")
        if p.bundled != bool(w["inBundle"]):
            diffs.append(f"{loc}: inBundle differs")
        if not w["isLink"]:
            if p.integrity != w.get("integrity"):
                diffs.append(f"{loc}: integrity differs")
            if p.source is not None and p.source.kind in ("registry", "tarball") and p.source.url != w.get("resolved"):
                diffs.append(f"{loc}: resolved differs")
        else:
            if (p.source is None) or p.source.kind != "link":
                diffs.append(f"{loc}: link not recognised")
        declared = {d.name for d in p.dependencies if d.kind != "peer"}
        # Arborist keeps one edge per name: a peer entry that is also a real dependency is dropped
        got = sorted((d.name, _KIND[d.kind], d.target) for d in p.dependencies if d.kind != "peer" or d.name not in declared)
        exp = sorted((e["name"], _KIND[e["type"]], e["to"]) for e in w["edges"])
        # Arborist keeps one edge per name; where the lockfile lists a name under two kinds, compare targets only
        dup = {n for n, *_ in got if sum(1 for g in got if g[0] == n) > 1}
        if dup:
            got = [(n, "*" if n in dup else k, t) for n, k, t in got]
            exp = [(n, "*" if n in dup else k, t) for n, k, t in exp]
            got = sorted(set(got))
        if lf.version in ("1", None) and not exp:
            continue
        if got != exp:
            gs, es = set(got), set(exp)
            diffs.append(f"{loc}: edges differ mine-only={sorted(gs - es, key=str)[:2]} oracle-only={sorted(es - gs, key=str)[:2]}")
    return diffs


# -- Cargo.lock (oracle: smol-toml, an independent TOML parser) -----------------------------
_DEPRE = re.compile(r"^(\S+)(?: (\S+))?(?: \((.*)\))?$")


def cargo(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    pk = list(oracle.get("package", []))
    if "root" in oracle:
        pk = [oracle["root"], *pk]
    if len(pk) != len(lf.packages):
        return [f"package count {len(lf.packages)} != {len(pk)}"]
    legacy = {}
    for k, v in (oracle.get("metadata") or {}).items():
        m = re.match(r"^checksum (\S+) (\S+) \((.*)\)$", k)
        if m:
            legacy[m.groups()] = v
    for mine_p, w in zip(lf.packages, pk):
        if (mine_p.name, mine_p.version) != (w["name"], w.get("version")):
            diffs.append(f"{mine_p.id}: name/version differ")
            continue
        src = w.get("source")
        chk = w.get("checksum") or (legacy.get((w["name"], w.get("version"), src)) if src else None)
        if (mine_p.integrity or None) != (("sha256:" + chk) if chk else None):
            diffs.append(f"{mine_p.id}: checksum differs")
        if (mine_p.source.kind == "path") != (src is None):  # type: ignore[union-attr]
            diffs.append(f"{mine_p.id}: path-vs-remote differs")
        want_deps = w.get("dependencies", [])
        if len(want_deps) != len(mine_p.dependencies):
            diffs.append(f"{mine_p.id}: dependency count differs")
            continue
        for ds, d in zip(want_deps, mine_p.dependencies):
            m = _DEPRE.match(ds)
            assert m
            cands = [i for i, q in enumerate(pk) if q["name"] == m.group(1) and (m.group(2) is None or q.get("version") == m.group(2)) and (m.group(3) is None or q.get("source") == m.group(3))]
            exp = lf.packages[cands[0]].id if cands else None
            if len(cands) > 1 and m.group(2) is not None and m.group(3) is not None:
                diffs.append(f"{mine_p.id}: ambiguous dep {ds}")
            if d.target != exp or d.name != m.group(1):
                diffs.append(f"{mine_p.id}: dep {ds} -> {d.target} expected {exp}")
    return diffs


# -- go.sum (oracle: readGoSum copied from cmd/go) --------------------------------------------
def gosum(lf: Lockfile, oracle: dict[str, list[str]]) -> Diff:
    zips: dict[tuple[str, str], list[str]] = {}
    mods: dict[tuple[str, str], list[str]] = {}
    for key, hashes in oracle.items():
        path, ver = key.split(" ", 1)
        if ver.endswith("/go.mod"):
            mods[(path, ver[:-7])] = list(dict.fromkeys(hashes))
        else:
            zips[(path, ver)] = list(dict.fromkeys(hashes))
    want = set(zips) | set(mods)
    got = {(p.name, p.version) for p in lf.packages}
    diffs: Diff = []
    if want != got or len(lf.packages) != len(got):
        diffs.append(f"module sets differ: only-mine={sorted(got - want)[:3]} only-oracle={sorted(want - got)[:3]}")
        return diffs
    for p in lf.packages:
        mod = (p.name, p.version or "")
        if list((p.raw or {})["h1"]) != zips.get(mod, []) or list((p.raw or {})["go_mod_h1"]) != mods.get(mod, []):
            diffs.append(f"{mod}: hashes differ")
    return diffs


# -- bun.lock (oracle: jsonc-parser, then an independent decode of the ident tuples) ----------------
def bun(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    pk = oracle["packages"]
    if set(p.id for p in lf.packages) != set(pk):
        return ["package keys differ"]
    for p in lf.packages:
        tup = pk[p.id]
        ident = tup[0]
        m = re.match(r"^(@?[^@]+)@(.*)$", ident)
        if not m:
            diffs.append(f"{p.id}: cannot decode ident {ident}")
            continue
        name, rest = m.groups()
        if rest.startswith("npm:"):
            name, rest = re.match(r"^npm:(@?[^@]+)@(.*)$", rest).groups()  # type: ignore[union-attr]
        if p.name != name:
            diffs.append(f"{p.id}: name {p.name} != {name}")
        if re.match(r"^\d", rest) or re.match(r"^[0-9a-z]", rest) and ":" not in rest and "/" not in rest:
            if p.version != rest:
                diffs.append(f"{p.id}: version {p.version} != {rest}")
        for d in p.dependencies:
            if d.target is not None:
                t = next(q for q in lf.packages if q.id == d.target)
                if d.name not in (t.name, t.alias):
                    diffs.append(f"{p.id}: edge {d.name} resolved to package {t.id}")
        infos = [e for e in tup[1:] if isinstance(e, dict)]
        info = infos[0] if infos else {}
        want_deps = sorted((n, k) for f, k in (("dependencies", "prod"), ("devDependencies", "dev"), ("optionalDependencies", "optional"), ("peerDependencies", "peer")) for n in info.get(f, {}))
        if sorted((d.name, d.kind) for d in p.dependencies) != want_deps:
            diffs.append(f"{p.id}: dependencies differ")
        ints = [e for e in tup[1:] if isinstance(e, str) and re.match(r"^(sha\d+|md5)-", e)]
        if p.integrity != (ints[0] if ints else None):
            diffs.append(f"{p.id}: integrity differs")
    return diffs


def norm(key: str) -> str:
    return key.lstrip("/")


# -- pnpm-lock.yaml (oracle: @pnpm/lockfile-file of the matching pnpm generation + @pnpm/dependency-path) ----
def pnpm(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    want_pk: dict[str, Any] = oracle["lf"].get("packages") or {}
    want = {norm(k): (k, v) for k, v in want_pk.items()}
    mine = {norm(p.id): p for p in lf.packages}
    # pnpm 9's reader rewrites non-registry keys (`github.com/o/r/sha` -> `name@https://...`), so leftover
    # keys are paired by their resolution and peer suffix instead of by text
    only_mine, only_want = set(mine) - set(want), set(want) - set(mine)

    def fp(key: str, res: Any) -> tuple[str, str]:
        return json.dumps(res, sort_keys=True), key[key.find("(") :] if "(" in key else ""

    pair: dict[str, str] = {k: k for k in set(mine) & set(want)}
    if only_mine or only_want:
        mfp: dict[tuple[str, str], list[str]] = {}
        for k in only_mine:
            raw = mine[k].raw
            res = (raw.get("meta") or raw).get("resolution") if isinstance(raw, dict) else None
            mfp.setdefault(fp(k, res), []).append(k)
        for k in only_want:
            hit = mfp.get(fp(k, want[k][1].get("resolution")), [])
            if len(hit) == 1:
                pair[hit[0]] = k
        if len(pair) != len(mine) or len(pair) != len(want) or len(set(pair.values())) != len(pair):
            return [f"package keys differ: only-mine={sorted(set(mine) - set(pair))[:3]} only-oracle={sorted(set(want) - set(pair.values()))[:3]}"]
    for nk, okey_n in pair.items():
        okey, w = want[okey_n]
        p = mine[nk]
        parsed = oracle["parsed"][okey]
        wname, wver = parsed.get("name"), parsed.get("version")
        if wname is None:
            wname, wver = w.get("name"), w.get("version")
        if wname is None:
            wname = None  # dependency-path could not decide; skip
        else:
            if p.name != wname:
                diffs.append(f"{nk}: name {p.name!r} != {wname!r}")
            if wver is not None and p.version != str(wver):
                diffs.append(f"{nk}: version {p.version!r} != {wver!r}")
        res = w.get("resolution") or {}
        if p.integrity != res.get("integrity"):
            diffs.append(f"{nk}: integrity differs")
        if res.get("type") == "git" and (p.source is None or p.source.kind != "git"):
            diffs.append(f"{nk}: git source not recognised")
        if res.get("type") == "directory" and (p.source is None or p.source.kind != "path"):
            diffs.append(f"{nk}: directory source not recognised")
        if p.dev != (w.get("dev") is True) or p.optional != (w.get("optional") is True):
            diffs.append(f"{nk}: dev/optional flags differ")
        edges = oracle["edges"][okey]
        for field, kind in (("dependencies", "prod"), ("optionalDependencies", "optional")):
            got = {d.name: d for d in p.dependencies if d.kind == kind}
            exp = w.get(field) or {}
            same_names = set(exp) == set(got)
            # the pnpm 9 reader rewrites non-registry references of v6 files into URLs; compare those by target only
            same_specs = all(
                str(v).lstrip("/") == str(got[k].spec).lstrip("/") or "://" in str(v)
                for k, v in exp.items()
                if k in got
            )
            if not (same_names and same_specs):

                diffs.append(f"{nk}: {field} differ")
                continue
            for n, d in got.items():
                rel = edges.get(f"{field}:{n}")
                rel_n = None if rel is None else norm(rel)
                if d.target is None:
                    if rel_n is not None and rel_n in want:
                        diffs.append(f"{nk}: edge {n} unresolved, oracle says {rel_n}")
                elif pair.get(norm(d.target)) != rel_n:
                    diffs.append(f"{nk}: edge {n} -> {d.target} but oracle says {rel}")
        peers = {d.name: d.spec for d in p.dependencies if d.kind == "peer"}
        if lf.version and lf.version.startswith(("5", "6", "7", "8")) or True:
            wp = {k: str(v) for k, v in (w.get("peerDependencies") or {}).items()}
            # v9 keeps peerDependencies in the package metadata; the converted oracle exposes them too
            if peers != wp:
                diffs.append(f"{nk}: peerDependencies differ")
    # importers
    want_imp = {k: v for k, v in (oracle["lf"].get("importers") or {}).items() if k in lf.roots or any(v.get(f) for f in ("dependencies", "devDependencies", "optionalDependencies"))}
    if set(lf.roots) != set(want_imp):
        diffs.append(f"importers differ: {sorted(lf.roots)[:3]} vs {sorted(want_imp)[:3]}")
    else:
        for path, imp in want_imp.items():
            exp_deps = {}
            for f in ("dependencies", "devDependencies", "optionalDependencies"):
                for n, ref in (imp.get(f) or {}).items():
                    exp_deps[n] = (imp.get("specifiers") or {}).get(n), str(ref)
            got_deps = {d.name: (d.spec, d.target) for d in lf.roots[path]}
            if set(exp_deps) != set(got_deps):
                diffs.append(f"importer {path}: dependency names differ")
                continue
            for n, (spec, ref) in exp_deps.items():
                if got_deps[n][0] != spec:
                    diffs.append(f"importer {path}: spec of {n} {got_deps[n][0]!r} != {spec!r}")
                rel = oracle["impEdges"][path].get(n)
                tgt = got_deps[n][1]
                if tgt is None:
                    if rel is not None and norm(rel) in want:
                        diffs.append(f"importer {path}: {n} unresolved, oracle says {rel}")
                elif pair.get(norm(tgt)) != (None if rel is None else norm(rel)):
                    diffs.append(f"importer {path}: {n} -> {tgt} but oracle says {rel}")
    return diffs


# -- mix.lock (oracle: an independent line-based regex reader; no Erlang/Elixir on the machine) --------------
def mix(lf: Lockfile, oracle: dict[str, Any]) -> Diff:
    diffs: Diff = []
    if oracle["unmatched"]:
        diffs.append(f"oracle could not read {len(oracle['unmatched'])} lines")
    want = {e["app"]: e for e in oracle["entries"]}
    mine = {p.id: p for p in lf.packages}
    if set(mine) != set(want):
        return [f"apps differ: only-mine={sorted(set(mine) - set(want))[:3]} only-oracle={sorted(set(want) - set(mine))[:3]}"]
    for app, w in want.items():
        p = mine[app]
        assert p.source is not None
        if w["tag"] == "hex":
            if (p.name, p.version, p.source.kind) != (w["name"], w["version"], "registry"):
                diffs.append(f"{app}: name/version differ")
            digest = w["outer"] or w["inner"]
            if p.integrity != (("sha256:" + digest) if digest else None):
                diffs.append(f"{app}: checksum differs")
            if w["repo"] and p.source.url != w["repo"]:
                diffs.append(f"{app}: repo differs")
            got = sorted((d.name, d.spec, d.kind == "optional") for d in p.dependencies)
            if got != sorted((n, r, o) for n, r, o in w["deps"]):
                diffs.append(f"{app}: dependencies differ")
        elif w["tag"] == "git":
            if (p.source.kind, p.source.url, p.source.revision) != ("git", w["url"], w["sha"]):
                diffs.append(f"{app}: git source differs")
        elif w["tag"] == "path":
            if (p.source.kind, p.source.url) != ("path", w["path"]):
                diffs.append(f"{app}: path source differs")
        else:
            diffs.append(f"{app}: unknown tag {w['tag']}")
    return diffs
