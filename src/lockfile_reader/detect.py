"""Format detection by file name, then by content."""

from __future__ import annotations

import json
import re

from . import _jsonc
from .model import LockfileError

FORMATS = ("yarn-v1", "yarn-berry", "gemfile", "mix", "bun", "pnpm", "npm", "cargo", "gosum")

_BY_NAME = {
    "yarn.lock": "yarn",
    "gemfile.lock": "gemfile",
    "gems.locked": "gemfile",
    "mix.lock": "mix",
    "bun.lock": "bun",
    "pnpm-lock.yaml": "pnpm",
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "cargo.lock": "cargo",
    "go.sum": "gosum",
}
_GOSUM_LINE = re.compile(r"^\S+ v\S+ h1:\S+$")
_PNPM = re.compile(r"^lockfileVersion:", re.M)


def from_name(name: str) -> str | None:
    """Return ``'yarn'`` (v1 or berry, undecided), another format id, or ``None``."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return _BY_NAME.get(base)


def sniff(text: str) -> str:
    """Decide the format from content alone."""
    head = text.lstrip("﻿")
    stripped = head.lstrip()
    if not stripped:
        raise LockfileError("empty input")
    if stripped.startswith("%{"):
        return "mix"
    if stripped.startswith("{"):
        try:
            doc = json.loads(head)
        except (ValueError, RecursionError):
            try:
                doc = _jsonc.loads(head)
            except LockfileError:
                raise LockfileError("looks like JSON but does not parse") from None
        if isinstance(doc, dict):
            pk = doc.get("packages")
            if isinstance(pk, dict) and pk and all(isinstance(v, list) for v in pk.values()):
                return "bun"
            if "workspaces" in doc and isinstance(pk, dict) and "lockfileVersion" in doc:
                first = next(iter(pk.values()), None)
                if isinstance(first, list):
                    return "bun"
            if "lockfileVersion" in doc or "dependencies" in doc or isinstance(pk, dict):
                return "npm"
        raise LockfileError("JSON document is not a recognised lockfile")
    lines = head.split("\n")
    if any(ln.startswith("__metadata:") for ln in lines[:50]):
        return "yarn-berry"
    if "# yarn lockfile v1" in head[:300]:
        return "yarn-v1"
    if _PNPM.search(head):
        return "pnpm"
    if re.search(r"^\[\[package\]\]", head, re.M) or re.search(r"^\[root\]", head, re.M):
        return "cargo"
    if re.search(r"^(GEM|GIT|PATH|PLUGIN SOURCE|DEPENDENCIES|PLATFORMS)$", head, re.M):
        return "gemfile"
    nonblank = [ln for ln in lines if ln.strip()][:20]
    if nonblank and all(_GOSUM_LINE.match(ln.strip()) for ln in nonblank):
        return "gosum"
    if re.search(r'^(?:"[^"\n]+"|[^\s"#][^\n]*):$', head, re.M) and re.search(r'^  version "', head, re.M):
        return "yarn-v1"
    raise LockfileError("cannot detect the lockfile format; pass format=")
