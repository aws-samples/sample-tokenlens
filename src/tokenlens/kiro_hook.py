"""Kiro PreToolUse hook for routing large text reads through TokenLens."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_MIN_BYTES = 8_000
_BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dmg",
    ".doc",
    ".docx",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".otf",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".pyc",
    ".so",
    ".tar",
    ".tif",
    ".tiff",
    ".ttf",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".zip",
}
_PATH_KEYS = ("path", "file_path", "filePath", "targetFile", "filename")


def _find_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key in _PATH_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                paths.append(candidate)
        for nested in value.values():
            paths.extend(_find_paths(nested))
    elif isinstance(value, list):
        for nested in value:
            paths.extend(_find_paths(nested))
    return list(dict.fromkeys(paths))


def _threshold() -> int:
    try:
        return max(
            0,
            int(os.environ.get("TOKENLENS_READ_HOOK_MIN_BYTES", DEFAULT_MIN_BYTES)),
        )
    except ValueError:
        return DEFAULT_MIN_BYTES


def _routing_enabled() -> bool:
    value = os.environ.get("TOKENLENS_ALWAYS_ON_READ_ROUTING_ENABLED", "true")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _resolve(raw_path: str) -> Path:
    path = Path(os.path.expanduser(raw_path))
    return path if path.is_absolute() else Path.cwd() / path


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as handle:
            sample = handle.read(1024)
    except OSError:
        return False
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _large_text_path(raw_paths: list[str]) -> str | None:
    threshold = _threshold()
    for raw_path in raw_paths:
        path = _resolve(raw_path)
        try:
            if not path.is_file() or path.stat().st_size < threshold:
                continue
        except OSError:
            continue
        if not _is_binary(path):
            return raw_path
    return None


def main() -> int:
    if not _routing_enabled():
        return 0
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    raw_path = _large_text_path(_find_paths(event))
    if raw_path is None:
        return 0

    print(
        "Large text-file raw read blocked. First narrow with grep/code tools when "
        f"possible; otherwise call tokenlens_read_file(path={raw_path!r}). "
        "Do not retry the same built-in read_file call.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
