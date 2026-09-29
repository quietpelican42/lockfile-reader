"""go.sum: ``module version[/go.mod] h1:hash`` lines, deduplicated to modules."""

from __future__ import annotations

from .model import Lockfile, LockfileError, Package, Source

# The hash of a go.mod that is empty. Older Go releases wrote it for non-modules by mistake, and the go
# command drops these lines when it reads go.sum, so this reader does too.
_EMPTY_GO_MOD_HASH = "h1:G7mAYYxgmS0lVkHyy2hEOLQCFB0DlQFTMLWggykrydY="


def parse(text: str) -> Lockfile:
    order: list[tuple[str, str]] = []
    zip_hash: dict[tuple[str, str], list[str]] = {}
    mod_hash: dict[tuple[str, str], list[str]] = {}
    for no, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 3:
            raise LockfileError(f"go.sum line {no}: expected 3 fields, got {len(fields)}")
        module, version, digest = fields
        if digest == _EMPTY_GO_MOD_HASH:
            continue
        is_mod = version.endswith("/go.mod")
        if is_mod:
            version = version[: -len("/go.mod")]
        if not version.startswith("v") or not digest.startswith("h1:"):
            raise LockfileError(f"go.sum line {no}: not a go.sum line")
        key = (module, version)
        if key not in zip_hash and key not in mod_hash:
            order.append(key)
        (mod_hash if is_mod else zip_hash).setdefault(key, [])
        target = mod_hash if is_mod else zip_hash
        if digest not in target[key]:
            target[key].append(digest)
    packages = []
    for module, version in order:
        z = zip_hash.get((module, version), [])
        g = mod_hash.get((module, version), [])
        packages.append(
            Package(
                id=f"{module}@{version}",
                name=module,
                version=version,
                source=Source("registry", None),
                integrity=z[0] if z else None,
                raw={"h1": tuple(z), "go_mod_h1": tuple(g), "go_mod_only": not z},
            )
        )
    return Lockfile("gosum", None, tuple(packages))
