# lockfile-reader

Parses yarn, Bun, pnpm, npm, Cargo, Bundler, Mix and Go lockfiles into one model of packages, sources, integrity hashes and dependency edges, in pure Python with no runtime dependencies.

```python
from lockfile_reader import parse

lock = parse("yarn.lock")            # format detected from the name, then from the content
print(lock.format, lock.version)     # yarn-berry 8
for pkg in lock:
    print(pkg.name, pkg.version, pkg.source.kind if pkg.source else None, pkg.integrity)
    for dep in pkg.dependencies:     # dep.target is the id of the package the edge resolves to
        print("  ", dep.kind, dep.name, dep.spec, "->", dep.target)
```

## Why

Python tooling that reads other ecosystems' lockfiles (SBOM generators, vulnerability scanners, license audits) either shells out to the package manager or hand-rolls a regex per format. Projects that do this today include `sonic-net/sonic-buildimage` (`scripts/sbom_parse_lockfiles.py`; its docstrings say "v2+ (berry) uses YAML which we skip here" and "Best-effort pnpm-lock.yaml parser without yaml dependency") and `duriantaco/skylos` (`skylos/rules/sca/yarn_lockfile.py`, which needs PyYAML). `vllm-project/semantic-router` reads one version out of `Cargo.lock` with a regex.

The PyPI packages that exist cover part of the space. Checked: `yarnlock` 1.2.0, `pyarn` 0.3.0, `lockfile-parser` 0.2.0, `scancode-toolkit` 32.5.0 (a whole scanner; its lockfile handlers are called directly), and `lockfiles` 0.0.1 (a name reservation with no parsing code, per its own README). `gemlock-parser` (Gemfile.lock) is on GitHub only and was not re-measured here. No package found handles `mix.lock` or `bun.lock`.

Measured: `python bench/incumbents.py`, run over the pinned corpus (`tests/corpus.tsv`) against the reference parsers. Each cell is the number of files where the tool returns exactly the reference's set of (name, version) pairs.

```
| format | files | scancode-toolkit | yarnlock | pyarn | lockfile-parser | lockfile-reader |
| yarn-v1 | 53 | 31/53 (2 errors) | 53/53 | 53/53 | n/a | 53/53 |
| yarn-berry | 29 | 0/29 (8 errors) | 0/29 (29 errors) | 0/29 (29 errors) | n/a | 29/29 |
| gemfile | 69 | 44/69 | n/a | n/a | n/a | 69/69 |
| npm | 116 | 89/116 (3 errors) | n/a | n/a | n/a | 116/116 |
| pnpm | 73 | 41/73 (17 errors) | n/a | n/a | n/a | 73/73 |
| cargo | 111 | 111/111 | n/a | n/a | 6/111 | 111/111 |
| gosum | 80 | 80/80 | n/a | n/a | n/a | 80/80 |

yarn-v1 files whose specifier keys equal @yarnpkg/lockfile's keys as returned: yarnlock 0/53, pyarn 0/53, lockfile-reader 53/53
```

Reading the table:

- On yarn v1, `yarnlock` and `pyarn` return the right packages, but not the right keys. `yarnlock` returns each `"a@^1", "a@^2":` header as one key and `pyarn` keeps one key per entry, so neither can answer "which entry satisfies `a@^2`" the way Yarn does.
- Neither reads Yarn Berry. Berry is 29 of the 82 yarn files in the corpus.
- scancode-toolkit has a handler for every format in the table but is exact on fewer files for Gemfile.lock (in the files inspected, git-sourced gems get the commit SHA as version), pnpm (it raises on multi-document lockfiles and on six other files) and package-lock (in the files inspected, workspace packages are missing). It has no handler for `mix.lock` or `bun.lock`.
- `lockfile-parser` keys packages by name, so a `Cargo.lock` with two versions of one crate loses one.
- Names that the reference gives no version for (git and file dependencies) and npm aliases are removed from every tool's set before comparing.

## Installation

```
pip install lockfile-reader
```

Python 3.11 or newer. There are no runtime dependencies: TOML comes from `tomllib`, and pnpm and Yarn Berry lockfiles are read by a YAML-subset reader that ships in the package.

## Usage

### Parse a path, bytes or text

```python
from pathlib import Path
from lockfile_reader import parse, LockfileError

parse("Cargo.lock")                    # path as str
parse(Path("go.sum"))                  # os.PathLike
parse(Path("bun.lock").read_bytes())   # bytes (UTF-8)
parse(text, format="pnpm")             # text, format given
try:
    parse("broken.lock")
except LockfileError as e:
    print(e)
```

A file name decides the format when it is one of `yarn.lock`, `Gemfile.lock`, `mix.lock`, `bun.lock`, `pnpm-lock.yaml`, `package-lock.json`, `npm-shrinkwrap.json`, `Cargo.lock`, `go.sum`. Otherwise the content is sniffed. Pass `format=` to skip detection.

### Follow dependency edges

```python
lock = parse("package-lock.json")
root = lock.roots[""]                       # direct dependencies of the project
by_id = {p.id: p for p in lock}
for dep in root:
    if dep.target:
        print(dep.name, "->", by_id[dep.target].version)
```

### Find git and path dependencies

```python
for pkg in parse("mix.lock"):
    if pkg.source and pkg.source.kind in ("git", "path"):
        print(pkg.name, pkg.source.url, pkg.source.revision, pkg.source.ref)
```

### Read the format-specific view

`Package.raw` is the entry as parsed (a dict for JSON, YAML and TOML formats, the term tuple for `mix.lock`, the ident tuple for `bun.lock`). `Lockfile.raw` holds file-level data such as Gemfile.lock platforms and Ruby version, pnpm settings, or Cargo metadata.

## Reference

### `parse(source, format=None) -> Lockfile`

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `source` | `str`, `bytes` or `os.PathLike` | required | A path, or the lockfile text (a `str` that is multi-line, or starts with `{`, `%{` or `[`), or UTF-8 bytes. |
| `format` | `str` or `None` | `None` | One of `yarn-v1`, `yarn-berry`, `gemfile`, `mix`, `bun`, `pnpm`, `npm`, `cargo`, `gosum`, or `yarn` (v1 or Berry by content). `None` detects it. |

Raises `LockfileError` (a `ValueError`) for unreadable, undecodable, malformed or undetectable input. No other exception type is raised for bad input.

### `Lockfile`

| Field | Type | Description |
| --- | --- | --- |
| `format` | `str` | The format id. |
| `version` | `str \| None` | The format's own version marker: `lockfileVersion`, `__metadata.version`, Cargo `version`, `BUNDLED WITH`. `None` where the format has none (go.sum, mix.lock). |
| `packages` | `tuple[Package, ...]` | Packages in file order. |
| `roots` | `Mapping[str, tuple[Dependency, ...]]` | Direct dependencies per project path (`""` or `"."` is the root). Recorded by npm v2/v3, pnpm, bun and Gemfile.lock. Empty for the others. |
| `raw` | `Mapping[str, Any]` | File-level data outside any package. |

Methods: iteration and `len()` over packages, `get(id)`, `find(name)`, `name_versions()`.

### `Package`

| Field | Type | Description |
| --- | --- | --- |
| `id` | `str` | Unique in the file: first specifier (yarn v1), resolution (Berry), `name@version[-platform]` (Gemfile), app name (mix), map key (bun), key (pnpm), `node_modules/...` path (npm), `name version (source)` (Cargo), `module@version` (go.sum). |
| `name` | `str` | The real package name. |
| `version` | `str \| None` | `None` for git, path and workspace packages where the file records no version. |
| `source` | `Source \| None` | `kind` (`registry`, `git`, `path`, `link`, `workspace`, `tarball`, `patch`, `other`), `url`, `revision`, `ref`. |
| `integrity` | `str \| None` | The file's digest. SRI strings stay as written; Cargo, Gemfile and mix digests are `sha256:<hex>`; go.sum is `h1:<base64>`; Berry keeps its `<cacheKey>/<hex>` checksum. |
| `dependencies` | `tuple[Dependency, ...]` | Edges out of the package. |
| `dev`, `optional`, `bundled` | `bool` | `False` when the format does not record the flag. |
| `platform` | `str \| None` | Gemfile.lock platform suffix. |
| `alias` | `str \| None` | The name it is installed or requested under, when that differs from `name` (npm aliases). |
| `specifiers` | `tuple[str, ...]` | Every descriptor that maps to this entry (yarn v1 and Berry). |
| `raw` | `Any` | The format-specific entry. |

### `Dependency`

| Field | Type | Description |
| --- | --- | --- |
| `name` | `str` | Dependency name. |
| `spec` | `str \| None` | The constraint as the file writes it. For formats that record resolved edges only (pnpm, Cargo), the resolved version. |
| `kind` | `str` | `prod`, `dev`, `optional` or `peer`. |
| `target` | `str \| None` | `id` of the package the edge resolves to, or `None` (peer edges, absent optional packages, workspace links). |

## How it works

Each format has a small reader in `src/lockfile_reader/`. The pieces that are not plain JSON or TOML:

- `_yaml.py` reads the YAML subset that pnpm and Yarn Berry write: block mappings and sequences, plain and quoted scalars, flow collections, block scalars, `? ` keys, comments and `---`. Anchors and tags raise `LockfileError`. Plain scalars resolve like the YAML core schema.
- `_terms.py` is a tokenizer and recursive-descent parser for Elixir/Erlang terms: atoms (including `:"quoted"` and `key:` forms), strings, numbers, lists, tuples, maps, keyword lists. `mix.lock` `:hex`, `:git` and `:path` tuples, and any other tag, are kept.
- `_jsonc.py` strips comments and trailing commas outside strings before `json.loads` (`bun.lock`).
- Edge targets are resolved with each ecosystem's rule: descriptor lookup (yarn), Node module resolution through `node_modules` paths (npm), nearest ancestor key (bun), dependency-path keys (pnpm), name plus version plus source (Cargo), name plus platform (Gemfile.lock).
- pnpm 10 lockfiles with several YAML documents: the last document is the project, earlier ones are parsed and kept in `Lockfile.raw["env_lockfiles"]`.

Every failure inside a reader ends as `LockfileError`. `parse()` also converts any stray `KeyError`, `TypeError` and similar into `LockfileError` as a safety net; the fuzz tests check that the readers never need it.

## Limitations

- Yarn v1 records no direct dependencies, so `roots` is empty. Berry `peerDependenciesMeta` is ignored.
- Berry `checksum` is kept as written, including the cache key prefix. It is not an SRI hash.
- Gemfile.lock splits `version-platform` at the first dash, as Bundler does. Sources are per section; Bundler merges all `GEM` sections into one source.
- `mix.lock` binaries (`<<...>>`) and heredocs are rejected.
- pnpm v9 records no `dev` flag, so `dev` is `False` there. Peer dependencies are listed as edges with `target=None`.
- npm lockfile v1 records no edge kind. An edge from a non-optional package into an optional one is reported as `optional`; all others as `prod`. `peerDependenciesMeta` is ignored.
- npm edge targets follow Node's lookup through `node_modules` directories. One corpus file (`mongodb-js/compass`) has a nested package under a directory the lockfile does not list; Arborist resolves one edge to the root copy where Node would find the nested one. The test asserts exactly that one difference.
- Only text `bun.lock` is read, not the binary `bun.lockb`.
- go.sum lines carrying the hash of an empty go.mod are dropped, as the go command does.
- Not read: `poetry.lock`, `uv.lock`, `Pipfile.lock`, `composer.lock`, `pubspec.lock`, `Package.resolved`, `packages.lock.json`, `deno.lock`.
- No writing or editing of lockfiles, no network access, and no version-range solving.

## Compatibility

Run here: Python 3.14.7 on macOS (arm64): `pytest` 2445 passed, `mypy --strict` clean. Python 3.11 to 3.13 are configured in the CI matrix (`.github/workflows/test.yml`) and have not been run by the author. Only the standard library is used at runtime (`tomllib` needs 3.11).

Reference parsers used as test oracles (in `tests/oracle/`):

| Format | Oracle | Notes |
| --- | --- | --- |
| yarn v1 | `@yarnpkg/lockfile` 1.1.0 | |
| yarn Berry | `@yarnpkg/parsers` 3.1.0 `parseSyml` | Compares entries, resolutions, dependency maps and optional flags. |
| Gemfile.lock | `Bundler::LockfileParser` 1.17.2 (Ruby 2.6.10) | Bundler 1.17 is from 2018 and predates `CHECKSUMS`; that section and `BUNDLED WITH` are removed before the oracle reads a file, and the section is counted separately. |
| package-lock.json | `@npmcli/arborist` 10.0.3 `loadVirtual` | Nodes, versions, integrity, links, bundled flags and every dependency edge target. |
| pnpm | `@pnpm/lockfile-file` 6.0.5 (v5), 9.1.3 (v6 and v9), with `@pnpm/dependency-path` | The v9 reader converts v6 keys, so pairing is by resolution. For multi-document files the oracle reads the last document. |
| Cargo.lock | `smol-toml` 1.9.0 | No `cargo` on the test machine. This is an independent TOML parser plus an independent resolution of dependency strings. |
| go.sum | `readGoSum` copied from the Go toolchain's `cmd/go` (Go 1.27.1 installed; license in `tests/oracle/gosum/LICENSE-go`) | |
| bun.lock | `jsonc-parser` 3.3.1, then an independent decode of the ident tuples | Bun is not installed, so its own parser was not used. |
| mix.lock | A line-based regex reader (`tests/oracle/mix.js`) | No `erl` or `elixir` on the test machine, so there is no independent Erlang term parser. Coverage is that regex reader on 112 files plus the hand-written fixtures in `tests/test_formats.py`. |
| YAML subset | `js-yaml` 4.1.0 (JSON schema) | Deep equality on every pnpm and Berry document. |

## Development

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
(cd tests/oracle && npm ci)          # oracles for the differential tests
.venv/bin/python -m pytest           # downloads the corpus into .corpus/ on first run
.venv/bin/python -m mypy --strict
.venv/bin/python tests/fuzzing.py 200 10     # mutation fuzzing on corpus files
```

`tests/corpus.tsv` pins 731 real lockfiles by repository, commit and sha256. Files are downloaded from `raw.githubusercontent.com` at test time into the git-ignored `.corpus/`; nothing from them is committed. Tests that need the network, Node, Ruby or Go skip with a message saying what is missing. Corpus mix:

| Format | Files | Versions |
| --- | --- | --- |
| yarn v1 | 53 | all `# yarn lockfile v1`; 13 taken from commits dated before 2020 |
| yarn Berry | 30 | metadata versions 6 to 10 (one file has conflict markers and is expected to be rejected) |
| Gemfile.lock | 69 | Bundler 1.10 to 4.0 |
| mix.lock | 112 | 23 taken from older commits or older repositories |
| bun.lock | 86 | lockfileVersion 1 to 3 |
| pnpm | 73 | lockfileVersion 5.3, 5.4 (17), 6.0 (14), 9.0 (42) |
| package-lock.json | 116 | lockfileVersion 1 (16), 2 (14), 3 (86) |
| Cargo.lock | 112 | 1 (12), 2 (6), 3 (9), 4 (84); one file has conflict markers |
| go.sum | 80 | one is empty |

Mutation fuzzing (`tests/fuzzing.py 200 10`, ten corpus files per format, 200 random edits each: truncation, deleted or duplicated or swapped lines, inserted delimiters and NULs, 5000-deep nesting, changed indentation): 18000 parses, 0 exceptions other than `LockfileError`, 0 internal exceptions that the safety net in `parse()` would have converted, slowest single parse 1.5 s.

## License

MIT. See [LICENSE](LICENSE).
