from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tokenlens import kiro_install as kiro_module
from tokenlens.kiro_install import (
    HOOK_RELATIVE_PATH,
    LEGACY_HOOKS,
    MANIFEST_RELATIVE_PATH,
    MCP_SERVER_NAME,
    STEERING_RELATIVE_PATH,
    KiroInstallError,
    doctor,
    install,
    mcp_server_config,
    uninstall,
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_is_safe_idempotent_and_uninstallable(tmp_path):
    settings = tmp_path / ".kiro/settings/mcp.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "other"}}}),
        encoding="utf-8",
    )

    first = install(tmp_path)
    installed = _json(settings)
    assert installed["theme"] == "dark"
    assert installed["mcpServers"]["other"] == {"command": "other"}
    tokenlens = installed["mcpServers"][MCP_SERVER_NAME]
    # Must be an interpreter that can import tokenlens. Resolving the venv
    # symlink used to register a base interpreter that could not, so Kiro failed
    # to start the server.
    assert tokenlens["command"] == kiro_module._python_command()
    assert kiro_module._interpreter_can_import(tokenlens["command"], "tokenlens")
    assert tokenlens["args"] == ["-m", "tokenlens", "mcp"]
    assert "tokenlens_retrieve" in tokenlens["autoApprove"]
    assert first["backup"] is not None

    hook = _json(tmp_path / HOOK_RELATIVE_PATH)
    assert "-m tokenlens.kiro_hook" in hook["hooks"][0]["action"]["command"]
    assert (tmp_path / STEERING_RELATIVE_PATH).is_file()
    assert (tmp_path / MANIFEST_RELATIVE_PATH).is_file()
    assert doctor(tmp_path, probe=False) == []

    second = install(tmp_path)
    assert second["backup"] is None
    assert doctor(tmp_path, probe=False) == []

    result = uninstall(tmp_path)
    assert result["removed"]
    restored = _json(settings)
    assert restored["theme"] == "dark"
    assert restored["mcpServers"] == {"other": {"command": "other"}}
    assert not (tmp_path / MANIFEST_RELATIVE_PATH).exists()


def test_install_supports_user_mcp_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"

    result = install(workspace, mcp_scope="user", home=home)

    assert Path(result["mcp_settings"]) == home / ".kiro/settings/mcp.json"
    assert MCP_SERVER_NAME in _json(Path(result["mcp_settings"]))["mcpServers"]
    assert (workspace / STEERING_RELATIVE_PATH).is_file()


def test_install_rejects_unmanaged_file_without_force(tmp_path):
    steering = tmp_path / STEERING_RELATIVE_PATH
    steering.parent.mkdir(parents=True)
    steering.write_text("custom steering", encoding="utf-8")

    with pytest.raises(KiroInstallError, match="not managed"):
        install(tmp_path)

    install(tmp_path, force=True)
    assert "TokenLens Always-On Policy" in steering.read_text(encoding="utf-8")

    uninstall(tmp_path)
    assert steering.read_text(encoding="utf-8") == "custom steering"


def test_install_rejects_malformed_settings_without_changing_it(tmp_path):
    settings = tmp_path / ".kiro/settings/mcp.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("not json", encoding="utf-8")

    with pytest.raises(KiroInstallError, match="valid JSON"):
        install(tmp_path)

    assert settings.read_text(encoding="utf-8") == "not json"


def test_uninstall_protects_modified_managed_file(tmp_path):
    install(tmp_path)
    steering = tmp_path / STEERING_RELATIVE_PATH
    steering.write_text("locally modified", encoding="utf-8")

    with pytest.raises(KiroInstallError, match="managed file was modified"):
        uninstall(tmp_path)

    assert steering.exists()
    assert (tmp_path / MANIFEST_RELATIVE_PATH).exists()


def test_installer_rejects_missing_workspace(tmp_path):
    with pytest.raises(KiroInstallError, match="not a directory"):
        install(tmp_path / "does-not-exist")


def test_install_requires_uninstall_before_scope_change(tmp_path):
    home = tmp_path / "home"
    install(tmp_path)

    with pytest.raises(KiroInstallError, match="MCP scope differs"):
        install(tmp_path, mcp_scope="user", home=home)


def test_install_migrates_stock_legacy_hooks(tmp_path):
    for relative, payload in LEGACY_HOOKS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = install(tmp_path)

    assert len(result["migrated_legacy_hooks"]) == len(LEGACY_HOOKS)
    assert all(not (tmp_path / relative).exists() for relative in LEGACY_HOOKS)
    assert doctor(tmp_path, probe=False) == []

    uninstall(tmp_path)
    assert all(not (tmp_path / relative).exists() for relative in LEGACY_HOOKS)


def test_install_preserves_customized_legacy_hook(tmp_path):
    relative = next(iter(LEGACY_HOOKS))
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text('{"custom": true}', encoding="utf-8")

    result = install(tmp_path)

    assert result["preserved_legacy_hooks"] == [str(path)]
    assert path.read_text(encoding="utf-8") == '{"custom": true}'
    assert any("legacy TokenLens hook" in problem for problem in doctor(tmp_path))


def test_install_rolls_back_settings_and_assets_on_failure(tmp_path, monkeypatch):
    settings = tmp_path / ".kiro/settings/mcp.json"
    settings.parent.mkdir(parents=True)
    original_settings = '{"mcpServers":{"other":{"command":"other"}}}'
    settings.write_text(original_settings, encoding="utf-8")
    real_atomic_write = kiro_module._atomic_write
    calls = 0

    def fail_first_asset(path, text, *, mode=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated asset write failure")
        real_atomic_write(path, text, mode=mode)

    monkeypatch.setattr(kiro_module, "_atomic_write", fail_first_asset)

    with pytest.raises(KiroInstallError, match="rolled back"):
        install(tmp_path)

    assert settings.read_text(encoding="utf-8") == original_settings
    assert not (tmp_path / STEERING_RELATIVE_PATH).exists()
    assert not (tmp_path / HOOK_RELATIVE_PATH).exists()
    assert not (tmp_path / MANIFEST_RELATIVE_PATH).exists()


def test_registered_interpreter_can_import_tokenlens(tmp_path):
    """A registration Kiro cannot start is the bug this guards against.

    Resolving a virtual environment symlink could yield a base interpreter that
    cannot import tokenlens, so the installer must verify candidates instead of
    writing the resolved path unconditionally.
    """
    install(tmp_path, verify=False)

    settings = _json(tmp_path / ".kiro/settings/mcp.json")
    server = settings["mcpServers"][MCP_SERVER_NAME]
    assert server["args"] == ["-m", "tokenlens", "mcp"]
    assert Path(server["command"]).exists()
    assert kiro_module._interpreter_can_import(server["command"], "tokenlens")


def test_python_command_prefers_unresolved_interpreter_when_resolved_is_broken(
    monkeypatch,
):
    resolved = str(Path(sys.executable).resolve())

    def only_unresolved_works(candidate: str, modules: str) -> bool:
        return candidate != resolved or sys.executable == resolved

    monkeypatch.setattr(kiro_module, "_interpreter_can_import", only_unresolved_works)
    assert kiro_module._python_command() == sys.executable


def test_python_command_fails_clearly_when_no_interpreter_can_import(monkeypatch):
    monkeypatch.setattr(
        kiro_module, "_interpreter_can_import", lambda candidate, modules: False
    )
    with pytest.raises(KiroInstallError, match="cannot import 'tokenlens'"):
        kiro_module._python_command()


def test_install_reports_probe_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kiro_module, "probe_mcp_server", lambda server: "No module named 'mcp'"
    )

    result = install(tmp_path)

    # Files are written, but the caller learns the server will not start.
    assert (tmp_path / MANIFEST_RELATIVE_PATH).is_file()
    assert result["verified"] is False
    assert result["verification_problems"] == ["No module named 'mcp'"]


def test_install_verification_passes_when_server_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(kiro_module, "probe_mcp_server", lambda server: None)

    result = install(tmp_path)

    assert result["verified"] is True
    assert result["verification_problems"] == []
    assert kiro_module.verify_registration(tmp_path) == []


def test_verify_registration_detects_missing_and_disabled_server(tmp_path, monkeypatch):
    monkeypatch.setattr(kiro_module, "probe_mcp_server", lambda server: None)
    install(tmp_path)
    settings_path = tmp_path / ".kiro/settings/mcp.json"

    settings = _json(settings_path)
    settings["mcpServers"][MCP_SERVER_NAME]["disabled"] = True
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    problems = kiro_module.verify_registration(tmp_path)
    assert any("disabled" in problem for problem in problems)

    settings["mcpServers"].pop(MCP_SERVER_NAME)
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    problems = kiro_module.verify_registration(tmp_path)
    assert any("is not registered" in problem for problem in problems)


def test_verify_registration_reports_uninstalled_workspace(tmp_path):
    assert kiro_module.verify_registration(tmp_path) == [
        f"TokenLens is not installed in {tmp_path}"
    ]


def test_probe_mcp_server_reports_missing_command():
    reason = kiro_module.probe_mcp_server(
        {"command": "/nonexistent/python", "args": ["-m", "tokenlens", "mcp"]}
    )
    assert reason is not None
    assert "not an absolute path to an executable file" in reason


def test_probe_mcp_server_runs_registered_command_with_probe_flag(monkeypatch):
    recorded: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        return Completed()

    monkeypatch.setattr(kiro_module.subprocess, "run", fake_run)
    assert (
        kiro_module.probe_mcp_server(
            {"command": "/usr/bin/python3", "args": ["-m", "tokenlens", "mcp"]}
        )
        is None
    )
    assert recorded["argv"] == [
        "/usr/bin/python3",
        "-m",
        "tokenlens",
        "mcp",
        "--probe",
    ]


def test_probe_mcp_server_rejects_non_executable_command(tmp_path):
    plain = tmp_path / "not-executable"
    plain.write_text("data", encoding="utf-8")
    reason = kiro_module.probe_mcp_server(
        {"command": str(plain), "args": ["-m", "tokenlens", "mcp"]}
    )
    assert reason is not None
    assert "not an absolute path to an executable file" in reason


def test_probe_mcp_server_rejects_relative_command():
    reason = kiro_module.probe_mcp_server(
        {"command": "python3", "args": ["-m", "tokenlens", "mcp"]}
    )
    assert reason is not None
    assert "not an absolute path to an executable file" in reason


def test_interpreter_can_import_rejects_bad_module_names():
    # Module names outside [A-Za-z_][A-Za-z0-9_]* must never reach `-c import`.
    assert not kiro_module._interpreter_can_import(sys.executable, "os; import sys")
    assert not kiro_module._interpreter_can_import(sys.executable, "")
    assert not kiro_module._interpreter_can_import(sys.executable, "tokenlens, 1bad")


def test_interpreter_can_import_rejects_non_executable_interpreter(tmp_path):
    plain = tmp_path / "fake-python"
    plain.write_text("", encoding="utf-8")
    assert not kiro_module._interpreter_can_import(str(plain), "tokenlens")


def test_mcp_server_config_pins_storage_dirs_to_absolute_home_paths():
    state = Path.home() / ".tokenlens"
    env = mcp_server_config()["env"]
    assert set(env) == {
        "TOKENLENS_METRICS_DIR",
        "TOKENLENS_CAPTURE_DIR",
        "TOKENLENS_RCS_DIR",
    }
    for value in env.values():
        assert Path(value).is_absolute()
    assert Path(env["TOKENLENS_METRICS_DIR"]) == state
    assert Path(env["TOKENLENS_CAPTURE_DIR"]) == state / "captures"
    assert Path(env["TOKENLENS_RCS_DIR"]) == state / "rcs"
