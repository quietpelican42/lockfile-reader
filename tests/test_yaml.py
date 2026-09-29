"""The YAML-subset reader against js-yaml on every pnpm and Yarn Berry file in the corpus."""

from __future__ import annotations

import math
from typing import Any

import pytest

import corpus
from conftest import corpus_path, oracle_json
from lockfile_reader import LockfileError, _yaml
from pnpm_docs import split_documents


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return bool(a == b) or (math.isnan(a) and math.isnan(b))
    return bool(a == b)


def _entries() -> list[Any]:
    return [pytest.param(e, id=f"{e.fmt}:{e.repo}@{e.commit[:7]}") for e in corpus.entries() if e.fmt in ("pnpm", "yarn-berry")]


@pytest.mark.parametrize("entry", _entries())
def test_yaml_reader_matches_js_yaml(entry: corpus.Entry, fetched: set[str]) -> None:
    path = corpus_path(entry, fetched)
    text = path.read_text(encoding="utf-8")
    if "merge-conflict" in entry.note:
        with pytest.raises(LockfileError):  # js-yaml rejects it too (no oracle output exists)
            _yaml.loads(text)
        return
    want = oracle_json(entry, fetched, script="yaml.js")["docs"]
    docs = split_documents(text) if entry.fmt == "pnpm" else [text]
    got = [_yaml.loads(d) for d in docs]
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert _equal(g, w)


def test_scalars_resolve_like_the_core_schema() -> None:
    doc = _yaml.loads("a: 1\nb: 1.5\nc: true\nd: null\ne: ~\nf: '1'\ng: 0x10\nh: 1.0.0\ni: -3\n")
    assert doc == {"a": 1, "b": 1.5, "c": True, "d": None, "e": None, "f": "1", "g": "0x10", "h": "1.0.0", "i": -3}


def test_nested_blocks_and_flow_collections() -> None:
    text = (
        "root:\n"
        "  list:\n"
        "    - a\n"
        "    - b: 1\n"
        "      c: [x, 'y z', {k: v}]\n"
        "  empty: {}\n"
        "  seq_same_indent:\n"
        "  - one\n"
        "  - two\n"
        "'@scope/pkg@1.0.0':\n"
        "  resolution: {integrity: sha512-abc==, tarball: 'https://x/y.tgz'}\n"
    )
    doc = _yaml.loads(text)
    assert doc["root"]["list"] == ["a", {"b": 1, "c": ["x", "y z", {"k": "v"}]}]
    assert doc["root"]["empty"] == {}
    assert doc["root"]["seq_same_indent"] == ["one", "two"]
    assert doc["@scope/pkg@1.0.0"]["resolution"] == {"integrity": "sha512-abc==", "tarball": "https://x/y.tgz"}


def test_quoted_keys_with_colons_and_escapes() -> None:
    doc = _yaml.loads("\"a: b\": 1\n'it''s': 'x: y'\nq: \"tab\\there\"\n")
    assert doc == {"a: b": 1, "it's": "x: y", "q": "tab\there"}


def test_comments_and_document_markers() -> None:
    assert _yaml.loads("---\n# c\na: 1 # trailing\nb: 'x # not a comment'\n") == {"a": 1, "b": "x # not a comment"}


def test_block_scalars() -> None:
    assert _yaml.loads("a: |\n  l1\n  l2\nb: >-\n  x\n  y\nc: 1\n") == {"a": "l1\nl2\n", "b": "x y", "c": 1}


def test_explicit_key() -> None:
    assert _yaml.loads("? long key\n: value\nother: 1\n") == {"long key": "value", "other": 1}


@pytest.mark.parametrize(
    "bad",
    ["a: 1\na: 2\n", "a:\n\t- x\n", "a: [1, 2\n", "a: 'open\n", "a: *ref\n", "a: 1\n  b: 2\n", "- a\nb: 1\n"],
)
def test_malformed_yaml_raises_lockfile_error(bad: str) -> None:
    with pytest.raises(LockfileError):
        _yaml.loads(bad)
