"""Tests for cross-surface large-text read routing.

The standalone v1 hook works in Kiro IDE and CLI. Tests invoke the packaged
hook entry point directly so event data cannot influence process execution.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import chdir, redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tokenlens.kiro_hook import main as route_read_main

_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / ".kiro" / "hooks"
_HOOK = _HOOKS / "tokenlens-route-reads.json"
_SCRIPT = _HOOKS / "tokenlens_route_reads.py"


def _hook() -> dict:
    return json.loads(_HOOK.read_text(encoding="utf-8"))


def _run_raw(
    raw_event: str, cwd: Path, *, env: dict[str, str] | None = None
) -> SimpleNamespace:
    stderr = StringIO()
    with (
        chdir(cwd),
        patch.object(sys, "stdin", StringIO(raw_event)),
        patch.dict(os.environ, env or {}, clear=False),
        redirect_stderr(stderr),
    ):
        return_code = route_read_main()
    return SimpleNamespace(returncode=return_code, stderr=stderr.getvalue())


def _run(
    event: object, cwd: Path, *, env: dict[str, str] | None = None
) -> SimpleNamespace:
    return _run_raw(json.dumps(event), cwd, env=env)


class TestRouteReadsHook:
    def test_hook_and_script_exist(self):
        assert _HOOK.is_file()
        assert _SCRIPT.is_file()
        wrapper = _SCRIPT.read_text(encoding="utf-8")
        assert 'import_module("tokenlens.kiro_hook").main' in wrapper
        assert "raise SystemExit(main())" in wrapper

    def test_portable_v1_pretooluse_hook(self):
        data = _hook()
        assert data["version"] == "v1"
        assert len(data["hooks"]) == 1
        hook = data["hooks"][0]
        assert hook["trigger"] == "PreToolUse"
        assert hook["matcher"] == r"^(read|read_file|read_files|fs_read)$"
        assert hook["action"]["type"] == "command"
        assert "tokenlens_route_reads.py" in hook["action"]["command"]

    def test_matcher_excludes_tokenlens_and_search_tools(self):
        matcher = _hook()["hooks"][0]["matcher"]
        for excluded in (
            "tokenlens_read_file",
            "tokenlens_compress",
            "grep_search",
            "file_search",
            "code",
            "list_directory",
        ):
            assert excluded not in matcher

    def test_small_text_read_is_allowed(self, tmp_path):
        path = tmp_path / "small.txt"
        path.write_text("small", encoding="utf-8")
        result = _run({"tool_input": {"path": str(path)}}, tmp_path)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_large_text_read_is_blocked_with_tokenlens_guidance(self, tmp_path):
        path = tmp_path / "large.txt"
        path.write_text("content\n" * 2_000, encoding="utf-8")
        result = _run({"toolInput": {"file_path": str(path)}}, tmp_path)
        assert result.returncode == 2
        assert "tokenlens_read_file" in result.stderr
        assert str(path) in result.stderr

    def test_large_binary_extension_is_allowed(self, tmp_path):
        path = tmp_path / "image.png"
        path.write_bytes(b"x" * 20_000)
        result = _run({"tool_input": {"path": str(path)}}, tmp_path)
        assert result.returncode == 0

    def test_malformed_input_fails_open(self, tmp_path):
        result = _run_raw("not json", tmp_path)
        assert result.returncode == 0


def test_routing_can_be_disabled(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("content\n" * 2_000, encoding="utf-8")
    result = _run(
        {"tool_input": {"path": str(path)}},
        tmp_path,
        env={"TOKENLENS_ALWAYS_ON_READ_ROUTING_ENABLED": "false"},
    )
    assert result.returncode == 0


def test_unknown_binary_extension_is_allowed(tmp_path):
    path = tmp_path / "payload.unknown"
    path.write_bytes(b"\x00\xff" * 10_000)
    result = _run({"tool_input": {"path": str(path)}}, tmp_path)
    assert result.returncode == 0


def test_multi_file_input_blocks_when_any_file_is_large_text(tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("small", encoding="utf-8")
    large = tmp_path / "large.txt"
    large.write_text("content\n" * 2_000, encoding="utf-8")
    result = _run(
        {"tool_input": {"paths": [{"path": str(small)}, {"path": str(large)}]}},
        tmp_path,
    )
    assert result.returncode == 2
    assert str(large) in result.stderr
