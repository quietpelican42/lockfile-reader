"""Shared helper: split a multi-document pnpm lockfile the way lockfile_reader does."""

from __future__ import annotations

from lockfile_reader import pnpm


def split_documents(text: str) -> list[str]:
    return pnpm._documents(text)
