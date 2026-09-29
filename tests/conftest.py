from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import corpus  # noqa: E402

ORACLE = Path(__file__).parent / "oracle"

_NODE = {
    "yarn-v1": "y1.js",
    "yarn-berry": "berry.js",
    "npm": "arb.js",
    "pnpm": "pnpm.js",
    "bun": "bun.js",
    "cargo": "cargo.js",
    "mix": "mix.js",
}
_TOOLS = {
    "gemfile": ("ruby", "gem.rb needs ruby with Bundler (tested with Bundler 1.17.2 from macOS system Ruby 2.6)"),
    "gosum": ("go", "the go.sum oracle needs the Go toolchain"),
}


def _skip_reason_for(fmt: str) -> str | None:
    if fmt in _NODE:
        if shutil.which("node") is None:
            return f"oracle for {fmt} needs node; install Node.js"
        if not (ORACLE / "node_modules").is_dir():
            return f"oracle for {fmt} needs its npm packages: run `npm ci` in tests/oracle"
        return None
    tool, why = _TOOLS[fmt]
    if shutil.which(tool) is None:
        return f"oracle for {fmt} unavailable: {why}"
    return None


@pytest.fixture(scope="session")
def fetched() -> set[str]:
    """Download the corpus once. Returns the set of local paths that are present and verified."""
    errors = corpus.fetch()
    if errors:
        print(f"\nlockfile-reader: {len(errors)} corpus files could not be fetched, e.g. {errors[0]}", file=sys.stderr)
    return {str(e.local) for e in corpus.entries() if e.local.exists()}


def corpus_path(entry: corpus.Entry, fetched: set[str]) -> Path:
    if str(entry.local) not in fetched:
        pytest.skip(f"corpus file {entry.repo}@{entry.commit[:8]} not available (offline?). Run `python tests/corpus.py` with network access.")
    return entry.local


def _run_oracle(fmt: str, script: str, indir: Path, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, BUNDLE_GEMFILE=str(ORACLE / "Gemfile"))
    if fmt in _NODE or script == "yaml.js":
        cmd = ["node", str(ORACLE / script), str(indir), str(outdir)]
    elif fmt == "gemfile":
        cmd = ["ruby", str(ORACLE / "gem.rb"), str(indir), str(outdir)]
    else:  # gosum
        exe = corpus.CACHE / "oracle" / "gosum_oracle"
        if not exe.exists():
            subprocess.run(["go", "build", "-o", str(exe), "."], cwd=ORACLE / "gosum", check=True, timeout=300)
        cmd = [str(exe), str(indir), str(outdir)]
    subprocess.run(cmd, check=True, timeout=1800, env=env, capture_output=True)


@lru_cache(maxsize=None)
def _oracle_dir(fmt: str, script: str | None, fetched_key: int) -> Path:
    indir = corpus.CACHE / fmt
    outdir = corpus.CACHE / "oracle" / (script or fmt).replace(".js", "") / fmt
    have = {p.name for p in indir.iterdir()} if indir.is_dir() else set()
    done = {p.name[: -len(".json")] for p in outdir.glob("*.json")} if outdir.is_dir() else set()
    if have - done:
        _run_oracle(fmt, script or _NODE.get(fmt, ""), indir, outdir)
    return outdir


def oracle_json(entry: corpus.Entry, fetched: set[str], script: str | None = None) -> Any:
    reason = _skip_reason_for(entry.fmt)
    if reason is not None:
        pytest.skip(reason)
    if script == "yaml.js" and shutil.which("node") is None:
        pytest.skip("YAML differential needs node")
    outdir = _oracle_dir(entry.fmt, script, len(fetched))
    f = outdir / (entry.local.name + ".json")
    if not f.exists():
        pytest.skip(f"oracle produced no output for {entry.local.name}: it rejected the file (see tests/oracle)")
    return json.loads(f.read_text(encoding="utf-8"))
