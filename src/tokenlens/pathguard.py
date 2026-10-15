"""Sensitive-path guard for the file-read tool.

``tokenlens_read_file`` reads arbitrary local files on behalf of the agent,
running with the user's own privileges. It is not a privilege boundary, but
TokenLens is designed to run in customer environments, so a read tool that will
happily return secrets (private keys, ``.env`` files, cloud credentials) is a
hardening gap worth closing.

This module provides a small, pure classifier that flags paths pointing at
well-known sensitive files or directories, plus obviously malformed paths
(embedded null bytes). It denies by pattern; it does not confine reads to a
directory, so legitimate in- and out-of-workspace reads keep working.

The guard is deny-by-known-pattern, not allow-listing: it targets the concrete
exfiltration risk (secrets) without breaking normal file reads.
"""

from __future__ import annotations

import posixpath

# Directory names that commonly hold secrets. Matched as a path segment
# (case-insensitive), so ``.ssh``, ``~/.ssh/id_rsa``, ``a/.aws/credentials``
# are all denied.
_SENSITIVE_DIR_SEGMENTS: frozenset[str] = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".kube",
        ".docker",
        ".gem",
    }
)

# Exact base names that are sensitive regardless of directory.
_SENSITIVE_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
    }
)

# Base-name suffixes that indicate secret material (keys, certs, env files).
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".keystore",
    ".jks",
)

# Base-name prefixes that indicate a dotenv variant (.env.local, .env.prod).
_SENSITIVE_PREFIXES: tuple[str, ...] = (".env",)


def is_sensitive_path(path: str) -> bool:
    """Return True if ``path`` points at a well-known sensitive file/dir or is
    malformed (contains a null byte). Pure and side-effect free."""
    if not isinstance(path, str) or "\x00" in path:
        return True

    normalized = path.replace("\\", "/")
    segments = [seg for seg in normalized.split("/") if seg]
    if not segments:
        return False

    lowered_segments = [seg.lower() for seg in segments]
    if any(seg in _SENSITIVE_DIR_SEGMENTS for seg in lowered_segments):
        return True

    base = posixpath.basename(normalized)
    base_lower = base.lower()
    if base_lower in _SENSITIVE_BASENAMES:
        return True
    if any(base_lower.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
        return True
    if any(base_lower.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
        return True

    return False
