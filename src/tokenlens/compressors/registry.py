"""Compressor selection.

Given a block of text, pick the first compressor whose ``matches`` returns
True. Order matters: JSON is checked first because a JSON payload can
incidentally contain diff-, log-, or code-like substrings; diff is checked
before logs and code because a unified diff contains code- and log-like
lines those compressors could otherwise claim.
"""

from __future__ import annotations

import os

from . import Compressor, CompressionResult
from .code import CodeCompressor
from .diff import DiffCompressor
from .json_compressor import JsonCompressor
from .logs import LogCompressor

# Ordered by specificity: most specific / least ambiguous first. Diff is
# checked before logs and code because a unified diff contains code-like and
# log-like lines that those compressors could otherwise claim.
_COMPRESSORS: list[Compressor] = [
    JsonCompressor(),
    DiffCompressor(),
    LogCompressor(),
    CodeCompressor(),
]

# File extensions that strongly signal a content type. When a filename is
# known, this takes priority over content sniffing so a source file that
# happens to contain log-like strings (e.g. the literal word "ERROR" in a
# regex) is not misrouted to the log compressor. The mapped compressor is
# still asked to confirm via matches(); if it declines, we fall back to the
# ordered content scan.
_EXTENSION_COMPRESSOR: dict[str, str] = {
    # source code
    "py": "code", "pyi": "code", "js": "code", "jsx": "code", "mjs": "code",
    "cjs": "code", "ts": "code", "tsx": "code", "java": "code", "go": "code",
    "rs": "code", "rb": "code", "c": "code", "h": "code", "cpp": "code",
    "cc": "code", "cxx": "code", "hpp": "code", "cs": "code", "kt": "code",
    "kts": "code", "swift": "code", "php": "code", "scala": "code",
    "sh": "code", "bash": "code", "zsh": "code",
    # structured data
    "json": "json",
    # diffs / patches
    "diff": "diff", "patch": "diff",
    # logs
    "log": "logs",
}


def _compressor_by_name(name: str) -> Compressor | None:
    for compressor in _COMPRESSORS:
        if compressor.name == name:
            return compressor
    return None


def _extension_of(filename: str | None) -> str | None:
    if not filename:
        return None
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    return ext or None


def select_compressor(text: str, filename: str | None = None) -> Compressor | None:
    """Return the best-matching compressor for the text, or None.

    When ``filename`` is provided and its extension maps to a compressor, that
    compressor gets first refusal (subject to its own ``matches`` check). This
    prevents extension-known files from being claimed by a lower-priority
    content sniffer. Falls back to the ordered content scan otherwise.
    """
    ext = _extension_of(filename)
    if ext is not None:
        preferred_name = _EXTENSION_COMPRESSOR.get(ext)
        if preferred_name is not None:
            preferred = _compressor_by_name(preferred_name)
            if preferred is not None and preferred.matches(text):
                return preferred

    for compressor in _COMPRESSORS:
        if compressor.matches(text):
            return compressor
    return None


def compress_block(
    text: str, mode: str = "balanced", filename: str | None = None
) -> CompressionResult:
    """Compress a single block using the best-matching compressor.

    If no compressor matches, returns an unmodified result so callers can treat
    every block uniformly. ``filename`` is an optional routing hint (its
    extension can override content-based selection).
    """
    compressor = select_compressor(text, filename)
    if compressor is None:
        from ..tokens import count_tokens

        tokens = count_tokens(text)
        return CompressionResult(
            text=text,
            original_tokens=tokens,
            compressed_tokens=tokens,
            applied=False,
            compressor="none",
        )
    return compressor.compress(text, mode)
