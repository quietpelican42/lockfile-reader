"""Print every package in a lockfile and the packages it depends on.

    python examples/list_packages.py path/to/yarn.lock
"""

from __future__ import annotations

import sys

from lockfile_reader import LockfileError, parse


def main(path: str) -> int:
    try:
        lock = parse(path)
    except LockfileError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 2
    print(f"{lock.format} {lock.version or ''}: {len(lock)} packages")
    for pkg in lock:
        source = pkg.source.kind if pkg.source else "-"
        targets = sorted({d.target for d in pkg.dependencies if d.target})
        print(f"{pkg.name}\t{pkg.version or '-'}\t{source}\t{len(targets)} resolved dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
