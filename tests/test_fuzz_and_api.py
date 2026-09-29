"""Mutation fuzzing on the hand-written fixtures, plus the public API surface."""

from __future__ import annotations

from pathlib import Path

import pytest

import fuzzing
import test_formats as tf
from lockfile_reader import FORMATS, Lockfile, LockfileError, parse

SEEDS: dict[str, str] = {
    "yarn-v1": tf.YARN_V1,
    "yarn-berry": tf.BERRY,
    "gemfile": tf.GEMFILE,
    "mix": tf.MIX,
    "bun": tf.BUN,
    "pnpm": tf.PNPM_V9,
    "npm": tf.NPM_V3,
    "cargo": tf.CARGO_V4,
    "gosum": tf.GOSUM,
}
EXTRA_SEEDS = {"pnpm": [tf.PNPM_V5, tf.PNPM_V6], "npm": [tf.NPM_V1], "cargo": [tf.CARGO_V1]}


@pytest.mark.parametrize("fmt", FORMATS)
def test_mutated_input_never_crashes(fmt: str) -> None:
    seeds = [SEEDS[fmt], *EXTRA_SEEDS.get(fmt, [])]
    stats = fuzzing.run(fmt, seeds, count=400, rng_seed=1234)
    assert not stats.crashes, stats.crashes[:3]
    assert not stats.leaks, f"parser let raw exceptions escape (parse() would convert them): {dict(stats.leaks)}"
    assert stats.clean_errors > 0 and stats.ok > 0  # both outcomes are exercised
    assert stats.slowest < 5.0


@pytest.mark.parametrize("fmt", FORMATS)
def test_garbage_and_empty_input_raise_lockfile_error(fmt: str) -> None:
    for junk in ["", "   \n", "\x00\x01\x02", "}{", "not a lockfile at all\nreally not\n"]:
        try:
            parse(junk, format=fmt)
        except LockfileError:
            continue
        # a lockfile with no entries is valid for these formats (yarn v1 and Cargo.lock keep only a header)
        assert fmt in ("gosum", "yarn-v1", "cargo") and not junk.strip(), (fmt, junk)


def test_crlf_line_endings_parse_identically() -> None:
    for fmt, text in SEEDS.items():
        if fmt in ("npm", "bun", "mix"):
            continue
        assert [p.id for p in parse(text.replace("\n", "\r\n"), format=fmt)] == [p.id for p in parse(text, format=fmt)], fmt


def test_utf8_bom_is_ignored() -> None:
    assert len(parse("﻿" + tf.GOSUM, format="gosum")) == 4
    assert parse(("﻿" + tf.NPM_V3).encode("utf-8")).format == "npm"


def test_parse_accepts_path_str_path_bytes_and_text(tmp_path: Path) -> None:
    f = tmp_path / "Cargo.lock"
    f.write_text(tf.CARGO_V4, encoding="utf-8")
    for source in (f, str(f), f.read_bytes(), tf.CARGO_V4):
        lf = parse(source)
        assert lf.format == "cargo" and len(lf) == 6


def test_filename_beats_content_sniffing(tmp_path: Path) -> None:
    f = tmp_path / "go.sum"
    f.write_text(tf.GOSUM, encoding="utf-8")
    assert parse(f).format == "gosum"


def test_explicit_format_overrides_detection() -> None:
    with pytest.raises(LockfileError):
        parse(tf.GOSUM, format="cargo")


def test_generic_yarn_format_picks_v1_or_berry() -> None:
    assert parse(tf.YARN_V1, format="yarn").format == "yarn-v1"
    assert parse(tf.BERRY, format="yarn").format == "yarn-berry"


def test_unknown_format_and_missing_file(tmp_path: Path) -> None:
    with pytest.raises(LockfileError, match="unknown format"):
        parse("x\n", format="composer")
    with pytest.raises(LockfileError, match="no such file"):
        parse(str(tmp_path / "missing.lock"))
    with pytest.raises(LockfileError, match="cannot read"):
        parse(tmp_path)  # a directory


def test_undetectable_content_asks_for_format() -> None:
    with pytest.raises(LockfileError, match="cannot detect"):
        parse("just some\nplain text\n")


def test_invalid_utf8_is_a_lockfile_error() -> None:
    with pytest.raises(LockfileError, match="UTF-8"):
        parse(b"\xff\xfe\x00bad", format="gosum")


def test_content_sniffing_each_format() -> None:
    for fmt, text in SEEDS.items():
        assert parse(text).format == fmt


def test_lockfile_helpers() -> None:
    lf: Lockfile = parse(tf.GOSUM, format="gosum")
    assert len(lf) == 4 and list(lf) == list(lf.packages)
    assert lf.get("github.com/a/b@v1.0.0") is not None and lf.get("nope") is None
    assert ("golang.org/x/text", "v0.3.0+incompatible") in lf.name_versions()
    assert len(lf.find("github.com/a/b")) == 2
