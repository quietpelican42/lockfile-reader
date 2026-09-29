"""Differential tests: every corpus file is parsed and compared with an official (or independent) parser."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

import compare
import corpus
from conftest import corpus_path, oracle_json
from lockfile_reader import Lockfile, LockfileError, parse

COMPARERS: dict[str, Callable[[Lockfile, Any], list[str]]] = {
    "yarn-v1": compare.yarn_v1,
    "yarn-berry": compare.yarn_berry,
    "gemfile": compare.gemfile,
    "mix": compare.mix,
    "bun": compare.bun,
    "pnpm": compare.pnpm,
    "npm": compare.npm,
    "cargo": compare.cargo,
    "gosum": compare.gosum,
}

# Cases where the oracle disagrees with Node's own resolution rules; the reason is asserted, not assumed.
KNOWN_ORACLE_DIVERGENCE = {
    ("npm", "mongodb-js/compass"): (
        "packages/compass-aggregations/node_modules/@mongodb-js/connection-info/node_modules/mongodb",
        "the lockfile has no entry for the intermediate directory connection-info, so Arborist resolves bson "
        "to the root copy (7.3.2) although the installed nested copy (6.10.4) is the one that satisfies ^6.10.4",
    ),
}


CANONICAL_NAME = {
    "yarn-v1": "yarn.lock",
    "yarn-berry": "yarn.lock",
    "gemfile": "Gemfile.lock",
    "mix": "mix.lock",
    "bun": "bun.lock",
    "pnpm": "pnpm-lock.yaml",
    "npm": "package-lock.json",
    "cargo": "Cargo.lock",
    "gosum": "go.sum",
}


def _params() -> list[Any]:
    return [pytest.param(e, id=f"{e.fmt}:{e.repo}@{e.commit[:7]}") for e in corpus.entries()]


@pytest.mark.parametrize("entry", _params())
def test_matches_oracle(entry: corpus.Entry, fetched: set[str]) -> None:
    path = corpus_path(entry, fetched)
    if "merge-conflict" in entry.note:
        # a committed file with unresolved conflict markers: every reference parser rejects it, so must we
        with pytest.raises(LockfileError, match="conflict|invalid|unexpected|expected"):
            parse(path.read_text(encoding="utf-8"), format=entry.fmt)
        return
    want = oracle_json(entry, fetched)
    lf = parse(path.read_text(encoding="utf-8"), format=entry.fmt)
    diffs = COMPARERS[entry.fmt](lf, want)
    known = KNOWN_ORACLE_DIVERGENCE.get((entry.fmt, entry.repo))
    if known is not None and diffs:
        assert len(diffs) == 1 and diffs[0].startswith(known[0]), diffs
        return
    assert not diffs, "\n".join(diffs[:10])


@pytest.mark.parametrize("entry", [p for p in _params() if p.values[0].fmt == "gemfile"])
def test_gemfile_checksum_section_is_fully_read(entry: corpus.Entry, fetched: set[str]) -> None:
    """Bundler 1.17 predates CHECKSUMS, so count the section's lines independently of it."""
    text = corpus_path(entry, fetched).read_text(encoding="utf-8")
    m = re.search(r"^CHECKSUMS\n((?:  .*\n)*)", text, re.M)
    expected = len(re.findall(r"^  \S+ \([^)]*\) sha256=[0-9a-f]+$", m.group(1), re.M)) if m else 0
    lf = parse(text, format="gemfile")
    assert len(lf.raw["checksums"]) == expected
    # every checksum whose gem is in the file is attached to that gem (the section also lists bundler itself)
    known = {(p.name, (p.version or "") + (f"-{p.platform}" if p.platform else "")) for p in lf}
    attachable = sum(1 for k in lf.raw["checksums"] if (k.split(" (")[0], k.split(" (")[1][:-1]) in known)
    assert sum(1 for p in lf if p.integrity) == attachable


@pytest.mark.parametrize("entry", _params())
def test_detects_format_from_filename(entry: corpus.Entry, fetched: set[str], tmp_path: Any) -> None:
    path = corpus_path(entry, fetched)
    named = tmp_path / CANONICAL_NAME[entry.fmt]
    named.write_bytes(path.read_bytes())
    if "merge-conflict" in entry.note:
        with pytest.raises(LockfileError):
            parse(named)
        return
    assert parse(named).format == entry.fmt


@pytest.mark.parametrize("entry", _params())
def test_detects_format_from_content(entry: corpus.Entry, fetched: set[str]) -> None:
    path = corpus_path(entry, fetched)
    text = path.read_text(encoding="utf-8")
    if "merge-conflict" in entry.note or "empty" in entry.note:
        with pytest.raises(LockfileError):  # nothing to sniff, or a broken file
            parse(text)
        return
    assert parse(text).format == entry.fmt
