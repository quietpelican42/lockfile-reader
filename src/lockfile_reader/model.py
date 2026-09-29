"""Common data model shared by every lockfile format."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class LockfileError(ValueError):
    """Raised when text cannot be parsed as the requested lockfile format."""


def _empty() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Source:
    """Where a package comes from.

    ``kind`` is one of ``registry``, ``git``, ``path``, ``link``, ``workspace``,
    ``tarball``, ``patch`` or ``other``.
    """

    kind: str
    url: str | None = None
    revision: str | None = None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Dependency:
    """One dependency edge out of a package (or out of a root).

    ``spec`` is the constraint or reference exactly as the lockfile records it.
    For formats that record only resolved edges (pnpm, Cargo) it is the resolved
    version string. ``target`` is the ``id`` of the package the edge resolves to,
    or ``None`` when the lockfile does not say or the target is not in the file.
    ``kind`` is ``prod``, ``dev``, ``optional`` or ``peer``.
    """

    name: str
    spec: str | None = None
    kind: str = "prod"
    target: str | None = None


@dataclass(frozen=True, slots=True)
class Package:
    """A package entry in the common model.

    ``id`` is unique within one lockfile. ``name`` is the real package name;
    ``alias`` is the name it is installed or requested under when that differs
    (npm ``npm:`` aliases). ``raw`` is the format-specific view of
    the entry as parsed (a mapping, list or string, depending on the format).
    ``dev``, ``optional`` and ``bundled`` are ``False`` when the format does not
    record the flag.
    """

    id: str
    name: str
    version: str | None
    source: Source | None = None
    integrity: str | None = None
    dependencies: tuple[Dependency, ...] = ()
    dev: bool = False
    optional: bool = False
    bundled: bool = False
    platform: str | None = None
    alias: str | None = None
    specifiers: tuple[str, ...] = ()
    raw: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class Lockfile:
    """A parsed lockfile.

    ``format`` is one of ``yarn-v1``, ``yarn-berry``, ``gemfile``, ``mix``,
    ``bun``, ``pnpm``, ``npm``, ``cargo`` or ``gosum``. ``version`` is the
    format's own version marker as a string, or ``None``. ``roots`` maps a
    project path (``""`` for the top-level project) to its direct dependencies
    where the format records them. ``raw`` holds format-level data that is not
    part of any package (metadata tables, platforms, Ruby version, and so on).
    """

    format: str
    version: str | None
    packages: tuple[Package, ...]
    roots: Mapping[str, tuple[Dependency, ...]] = field(default_factory=_empty)
    raw: Mapping[str, Any] = field(default_factory=_empty, repr=False)

    def __iter__(self) -> Iterator[Package]:
        return iter(self.packages)

    def __len__(self) -> int:
        return len(self.packages)

    def get(self, package_id: str) -> Package | None:
        """Return the package with this ``id``, or ``None``."""
        for pkg in self.packages:
            if pkg.id == package_id:
                return pkg
        return None

    def find(self, name: str) -> list[Package]:
        """Return every package with this name."""
        return [p for p in self.packages if p.name == name]

    def name_versions(self) -> set[tuple[str, str | None]]:
        """The set of ``(name, version)`` pairs in the file."""
        return {(p.name, p.version) for p in self.packages}


def make_source_from_url(url: str) -> Source:
    """Classify an npm-style ``resolved`` value."""
    low = url.lower()
    if low.startswith(("git+", "git://", "git@", "ssh://", "github:", "gitlab:", "bitbucket:", "gist:")):
        base, _, rev = url.partition("#")
        return Source("git", base, rev or None)
    if ".git#" in low or low.endswith(".git"):
        base, _, rev = url.partition("#")
        return Source("git", base, rev or None)
    if low.startswith("file:"):
        return Source("path", url)
    if low.startswith("link:"):
        return Source("link", url)
    if low.startswith(("http://", "https://")):
        if "/-/" in url:
            return Source("registry", url)
        return Source("tarball", url)
    return Source("other", url)
