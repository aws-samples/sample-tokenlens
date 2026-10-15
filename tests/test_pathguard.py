"""Tests for the sensitive-path guard used by tokenlens_read_file.

The guard denies reads of well-known secret files (keys, .env, cloud
credentials) and malformed paths, closing the secret-exfiltration hardening gap
flagged in review, while leaving ordinary source/doc reads unaffected.
"""

import pytest

from tokenlens.pathguard import is_sensitive_path


class TestSensitivePathsDenied:
    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            ".env.production",
            "config/.env",
            "/home/dev/project/.env",
            "~/.ssh/id_rsa",
            "/Users/dev/.ssh/id_ed25519",
            ".ssh/known_hosts",
            "secrets/server.pem",
            "certs/private.key",
            "keystore.jks",
            "app.p12",
            "bundle.pfx",
            "/home/dev/.aws/credentials",
            ".aws/config",
            ".kube/config",
            ".docker/config.json",
            ".gnupg/secring.gpg",
            "home/.netrc",
            ".pgpass",
            ".npmrc",
            ".pypirc",
            "a/b/c/id_dsa",
            "backend\\.env",  # windows separators
        ],
    )
    def test_sensitive_paths_are_denied(self, path):
        assert is_sensitive_path(path) is True

    def test_null_byte_is_denied(self):
        assert is_sensitive_path("valid/path\x00.md") is True

    def test_non_string_is_denied(self):
        assert is_sensitive_path(None) is True  # type: ignore[arg-type]


class TestOrdinaryPathsAllowed:
    @pytest.mark.parametrize(
        "path",
        [
            "src/tokenlens/api.py",
            "docs/architecture/overview.md",
            "README.md",
            "/Users/dev/project/notes.txt",
            "docs/guide.md",
            "environment.md",          # not a dotenv file
            "keyboard-shortcuts.md",   # 'key' substring must not trip .key
            "package.json",
            "data/rows.json",
            "a/b/c/module.ts",
        ],
    )
    def test_ordinary_paths_are_allowed(self, path):
        assert is_sensitive_path(path) is False
