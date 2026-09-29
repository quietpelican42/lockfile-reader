"""Fetch the pinned real-world lockfile corpus.

Every entry in ``corpus.tsv`` is ``format, repo, commit, path, sha256, note``. Files are
downloaded from raw.githubusercontent.com at the pinned commit, verified against the
sha256, and cached in ``.corpus/`` (gitignored). Nothing from the corpus is committed.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".corpus"
MANIFEST = HERE / "corpus.tsv"


@dataclass(frozen=True)
class Entry:
    fmt: str
    repo: str
    commit: str
    path: str
    sha256: str
    note: str

    @property
    def local(self) -> Path:
        return CACHE / self.fmt / f"{self.repo.replace('/', '__')}@{self.commit[:8]}"

    @property
    def url(self) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.commit}/{self.path}"

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def entries(fmt: str | None = None) -> list[Entry]:
    out = []
    for line in MANIFEST.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        parts += [""] * (6 - len(parts))
        e = Entry(*parts[:6])
        if fmt is None or e.fmt == fmt:
            out.append(e)
    return out


def _fetch_one(e: Entry) -> str | None:
    """Return an error string, or None when ``e.local`` is present and verified."""
    if e.local.exists():
        if hashlib.sha256(e.local.read_bytes()).hexdigest() == e.sha256:
            return None
        e.local.unlink()
    try:
        req = urllib.request.Request(e.url, headers={"User-Agent": "lockfile-reader-tests"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"{e.repo}@{e.commit[:8]}: {exc}"
    if hashlib.sha256(data).hexdigest() != e.sha256:
        return f"{e.repo}@{e.commit[:8]}: sha256 mismatch"
    e.local.parent.mkdir(parents=True, exist_ok=True)
    e.local.write_bytes(data)
    return None


def fetch(fmt: str | None = None) -> list[str]:
    """Download missing files. Returns the list of errors (empty when everything is present)."""
    todo = entries(fmt)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch_one, todo))
    return [r for r in results if r]


if __name__ == "__main__":
    errs = fetch(sys.argv[1] if len(sys.argv) > 1 else None)
    for err in errs:
        print("ERROR", err)
    print(f"{len(entries(sys.argv[1] if len(sys.argv) > 1 else None)) - len(errs)} files ready in {CACHE.name}/")
    sys.exit(1 if errs else 0)
