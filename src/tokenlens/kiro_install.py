"""Safe, repeatable Kiro installation for TokenLens."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from . import __version__

MCP_SERVER_NAME = "tokenlens"
MANIFEST_RELATIVE_PATH = Path(".kiro/tokenlens-install.json")
# Seconds allowed for the registered command to import TokenLens and build the
# MCP server during validation. Generous enough for a cold interpreter start.
PROBE_TIMEOUT_SECONDS = 60
STEERING_RELATIVE_PATH = Path(".kiro/steering/tokenlens-always-on.md")
HOOK_RELATIVE_PATH = Path(".kiro/hooks/tokenlens-route-reads.json")
AUTO_APPROVE = [
    "tokenlens_compress",
    "tokenlens_read_file",
    "tokenlens_optimize_request",
    "tokenlens_postprocess_response",
    "tokenlens_stats",
    "tokenlens_retrieve",
]
LEGACY_HOOKS: dict[Path, dict[str, Any]] = {
    Path(".kiro/hooks/tokenlens-compress-steering-created.kiro.hook"): {
        "name": "TokenLens: compress steering file on create",
        "version": "1",
        "when": {
            "type": "fileCreated",
            "patterns": [".kiro/steering/**/*.md", "AGENTS.md", "CLAUDE.md"],
        },
        "then": {
            "type": "runCommand",
            "command": 'tokenlens compress-file "${filePath}"',
        },
    },
    Path(".kiro/hooks/tokenlens-compress-steering.kiro.hook"): {
        "name": "TokenLens: compress steering file on save",
        "version": "1",
        "when": {
            "type": "fileEdited",
            "patterns": [".kiro/steering/**/*.md", "AGENTS.md", "CLAUDE.md"],
        },
        "then": {
            "type": "runCommand",
            "command": 'tokenlens compress-file "${filePath}"',
        },
    },
    Path(".kiro/hooks/tokenlens-route-reads.kiro.hook"): {
        "name": "TokenLens: route large file reads through compressed read path",
        "version": "1",
        "when": {"type": "preToolUse", "toolTypes": ["read_file", "read_files"]},
        "then": {
            "type": "askAgent",
            "prompt": (
                "Before performing this file read, route it through the "
                "tokenlens_read_file MCP tool instead of a raw read. "
                "tokenlens_read_file is profitability-gated and fail-open: it "
                "returns the original content unchanged when the file is below "
                "the routing threshold, is not UTF-8 text, or cannot be "
                "compressed. Use its returned text as the file contents. This "
                "applies only to reading a file's raw contents; it does not apply "
                "to code analysis (code), searching (grep_search, file_search), "
                "listing directories (list_directory), or fetching diagnostics."
            ),
        },
    },
}


class KiroInstallError(RuntimeError):
    """Raised when installation would overwrite invalid or user-owned state."""


def _resource_text(name: str) -> str:
    return (
        files("tokenlens.resources")
        .joinpath(*name.split("/"))
        .read_text(encoding="utf-8")
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_executable_file(candidate: str) -> bool:
    """True when `candidate` is an absolute path to an executable regular file."""
    path = Path(candidate)
    return path.is_absolute() and path.is_file() and os.access(candidate, os.X_OK)


def _interpreter_can_import(candidate: str, modules: str) -> bool:
    """Return True when `candidate` can import the given comma-separated modules.

    `candidate` comes only from `sys.executable` (verbatim or resolved) and is
    required to be an absolute, executable regular file. `modules` comes only
    from the fixed sets in `_python_command` and is validated against a strict
    module-name pattern, so no externally controlled value reaches the
    interpreter invocation. The call uses an argument list with no shell.
    """
    if not _is_executable_file(candidate):
        return False
    names = [name.strip() for name in modules.split(",")]
    if not names or not all(_MODULE_NAME_RE.fullmatch(name) for name in names):
        return False
    try:
        completed = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
            [candidate, "-c", f"import {', '.join(names)}"],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )  # nosec B603 - fixed argv, validated interpreter path, no shell
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _python_command() -> str:
    """Return an interpreter that can actually run the TokenLens MCP server.

    `sys.executable` is preferred verbatim, because a virtual environment's
    `python` is often a symlink into a base installation that cannot import
    TokenLens. Resolving that symlink produced an MCP registration Kiro could
    not start ("No module named tokenlens"), so the resolved path is only a
    fallback and candidates are verified before being written into MCP settings.

    An interpreter that imports both `tokenlens` and the MCP SDK wins. Failing
    that, importing `tokenlens` is enough to register: a missing or unsupported
    SDK is reported by the post-install validation step, which keeps the
    diagnosis specific instead of refusing to write any configuration.
    """
    candidates: list[str] = []
    for candidate in (sys.executable, str(Path(sys.executable).resolve())):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        raise KiroInstallError(
            "cannot determine the Python interpreter running TokenLens; "
            "reinstall TokenLens with pipx or uv and rerun the installer"
        )

    for modules in ("tokenlens, mcp", "tokenlens"):
        for candidate in candidates:
            if _interpreter_can_import(candidate, modules):
                return candidate

    raise KiroInstallError(
        f"interpreter {candidates[0]} cannot import 'tokenlens', so an MCP "
        "registration written now would fail to start. Reinstall TokenLens, "
        'for example `pipx install "aws-samples-tokenlens[mcp,tokenizers]"`, '
        "then rerun `tokenlens kiro install`."
    )


def probe_mcp_server(server: dict[str, Any]) -> str | None:
    """Run a registered MCP server command in probe mode.

    Returns None when the command starts and builds the server, otherwise a
    human-readable reason. This executes the exact command written into MCP
    settings, so it catches a wrong interpreter, a missing `tokenlens` module,
    and a missing or incompatible MCP SDK before Kiro is reconnected.

    Execution boundary: the command originates from the user's own MCP
    settings file — the same file Kiro itself executes on every reconnect —
    and callers pass either the installer-generated configuration or the
    manifest-recorded managed entry. Before running, the command must be an
    absolute path to an executable regular file and every argument a plain
    string; the invocation is an argument list with no shell, bounded by a
    timeout. A hostile edit to MCP settings is therefore detected here (as a
    validation failure) rather than granting anything Kiro would not already
    execute.
    """
    command = server.get("command")
    args = server.get("args")
    if not isinstance(command, str) or not command:
        return "MCP registration has no command to validate"
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return "MCP registration has no valid argument list to validate"
    if not _is_executable_file(command):
        return (
            "registered MCP command is not an absolute path to an executable "
            f"file: {command}"
        )

    argv = [command, *args, "--probe"]
    printable = shlex.join(argv)
    try:
        completed = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )  # nosec B603 - validated executable path, string argv, no shell
    except FileNotFoundError:
        return f"registered MCP command not found: {command}"
    except subprocess.TimeoutExpired:
        return f"registered MCP command timed out after {PROBE_TIMEOUT_SECONDS}s: {printable}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run registered MCP command ({printable}): {exc}"

    if completed.returncode == 0:
        return None
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    reason = detail[-1] if detail else f"exit code {completed.returncode}"
    return f"registered MCP command failed ({printable}): {reason}"


def mcp_server_config() -> dict[str, Any]:
    """Return MCP configuration using a verified TokenLens interpreter."""
    state = Path.home() / ".tokenlens"
    env = {
        "TOKENLENS_METRICS_DIR": str(state),
        "TOKENLENS_CAPTURE_DIR": str(state / "captures"),
        "TOKENLENS_RCS_DIR": str(state / "rcs"),
    }
    return {
        "command": _python_command(),
        "args": ["-m", "tokenlens", "mcp"],
        "disabled": False,
        "autoApprove": list(AUTO_APPROVE),
        "env": env,
    }


def hook_config() -> dict[str, Any]:
    command = f"{shlex.quote(_python_command())} -m tokenlens.kiro_hook"
    return {
        "version": "v1",
        "hooks": [
            {
                "name": "TokenLens: require compressed file reads",
                "trigger": "PreToolUse",
                "description": (
                    "Blocks only large native text reads and directs the agent "
                    "to tokenlens_read_file."
                ),
                "matcher": r"^(read|read_file|read_files|fs_read)$",
                "action": {"type": "command", "command": command, "timeout": 10},
            }
        ],
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KiroInstallError(f"cannot read valid JSON from {path}: {exc}") from exc


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = mode
    if existing_mode is None and path.exists():
        existing_mode = path.stat().st_mode & 0o777
    if existing_mode is None:
        existing_mode = 0o600

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.tokenlens-{stamp}.bak")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.tokenlens-{stamp}-{suffix}.bak")
        suffix += 1
    shutil.copy2(path, backup)
    return backup


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"existed": False}
    return {
        "existed": True,
        "content": path.read_text(encoding="utf-8"),
        "mode": path.stat().st_mode & 0o777,
    }


def _restore_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    if snapshot.get("existed"):
        _atomic_write(
            path,
            str(snapshot.get("content", "")),
            mode=int(snapshot.get("mode", 0o600)),
        )
    else:
        path.unlink(missing_ok=True)


def _legacy_hook_sets(root: Path) -> tuple[list[Path], list[Path]]:
    stock: list[Path] = []
    customized: list[Path] = []
    for relative, expected in LEGACY_HOOKS.items():
        path = root / relative
        if not path.exists():
            continue
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            customized.append(path)
            continue
        (stock if actual == expected else customized).append(path)
    return stock, customized


@contextmanager
def _settings_lock(settings_path: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - macOS/Linux distribution only
        raise KiroInstallError("Kiro installer requires macOS or Linux") from exc

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = settings_path.with_name(f".{settings_path.name}.tokenlens.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _workspace_path(workspace: str | Path) -> Path:
    path = Path(workspace).expanduser().resolve()
    if not path.is_dir():
        raise KiroInstallError(f"workspace is not a directory: {path}")
    return path


def _settings_path(
    workspace: Path,
    scope: Literal["workspace", "user"],
    *,
    home: Path | None = None,
) -> Path:
    if scope == "workspace":
        return workspace / ".kiro/settings/mcp.json"
    if scope == "user":
        user_home = home or Path(os.environ.get("HOME", "~")).expanduser()
        return user_home / ".kiro/settings/mcp.json"
    raise KiroInstallError(f"unsupported MCP scope: {scope}")


def _managed_assets() -> dict[Path, str]:
    steering = _resource_text("kiro/tokenlens-always-on.md")
    hook = _json_text(hook_config())
    return {STEERING_RELATIVE_PATH: steering, HOOK_RELATIVE_PATH: hook}


def _validate_settings(data: Any, path: Path) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise KiroInstallError(f"{path} must contain a JSON object")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise KiroInstallError(f"{path}: mcpServers must be a JSON object")
    return data


def _verify_owned_files(
    workspace: Path, manifest: dict[str, Any], *, force: bool
) -> None:
    for relative, expected_hash in manifest.get("files", {}).items():
        path = workspace / relative
        if not path.exists():
            continue
        actual_hash = _sha256(path.read_text(encoding="utf-8"))
        if actual_hash != expected_hash and not force:
            raise KiroInstallError(
                f"managed file was modified: {path}; rerun with --force to replace it"
            )


def verify_registration(
    workspace: str | Path = ".", *, probe: bool = True
) -> list[str]:
    """Validate that TokenLens is registered and that the server actually runs.

    Checks the recorded MCP settings file on disk, confirms the TokenLens entry
    is present, enabled, and matches what the installer wrote, then optionally
    runs the registered command in probe mode. An empty list means the
    registration is usable by Kiro.
    """
    root = _workspace_path(workspace)
    manifest_path = root / MANIFEST_RELATIVE_PATH
    manifest = _load_json(manifest_path, default=None)
    if not isinstance(manifest, dict):
        return [f"TokenLens is not installed in {root}"]

    settings_value = manifest.get("mcp_settings")
    if not isinstance(settings_value, str) or not settings_value:
        return ["installation manifest has no MCP settings path"]

    settings_path = Path(settings_value)
    if not settings_path.is_file():
        return [f"MCP settings file is missing: {settings_path}"]

    problems: list[str] = []
    try:
        settings = _validate_settings(
            _load_json(settings_path, default={}), settings_path
        )
    except KiroInstallError as exc:
        return [str(exc)]

    server = settings["mcpServers"].get(MCP_SERVER_NAME)
    if server is None:
        return [
            (
                f"MCP server '{MCP_SERVER_NAME}' is not registered in "
                f"{settings_path}; rerun `tokenlens kiro install`"
            )
        ]
    if not isinstance(server, dict):
        return [f"MCP server '{MCP_SERVER_NAME}' in {settings_path} is not an object"]

    managed_server = manifest.get("managed_server")
    if managed_server is not None and server != managed_server:
        problems.append(f"TokenLens MCP registration differs in {settings_path}")
    if server.get("disabled") is True:
        problems.append(
            f"MCP server '{MCP_SERVER_NAME}' is disabled in {settings_path}"
        )

    command = server.get("command")
    if isinstance(command, str) and command and not Path(command).exists():
        problems.append(f"registered MCP command does not exist: {command}")

    if probe:
        reason = probe_mcp_server(server)
        if reason:
            problems.append(reason)

    return problems


def install(
    workspace: str | Path = ".",
    *,
    mcp_scope: Literal["workspace", "user"] = "workspace",
    force: bool = False,
    home: Path | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Install steering, read-routing hook, and MCP registration for Kiro."""
    root = _workspace_path(workspace)
    manifest_path = root / MANIFEST_RELATIVE_PATH
    previous_manifest = _load_json(manifest_path, default=None)
    if previous_manifest is not None and not isinstance(previous_manifest, dict):
        raise KiroInstallError(f"{manifest_path} must contain a JSON object")
    if previous_manifest:
        previous_scope = previous_manifest.get("mcp_scope")
        if previous_scope != mcp_scope:
            raise KiroInstallError(
                "MCP scope differs from existing installation; uninstall before "
                "changing between workspace and user scope"
            )
        _verify_owned_files(root, previous_manifest, force=force)

    assets = _managed_assets()
    asset_snapshots = {str(relative): _snapshot(root / relative) for relative in assets}
    for relative, content in assets.items():
        destination = root / relative
        unmanaged_conflict = (
            destination.exists()
            and not previous_manifest
            and _sha256(destination.read_text(encoding="utf-8")) != _sha256(content)
        )
        if unmanaged_conflict and not force:
            raise KiroInstallError(
                f"existing file is not managed by TokenLens: {destination}"
            )

    settings_path = _settings_path(root, mcp_scope, home=home)
    desired_server = mcp_server_config()
    backup: Path | None = None
    stock_legacy, customized_legacy = _legacy_hook_sets(root)
    legacy_snapshots = {str(path): _snapshot(path) for path in stock_legacy}
    manifest_snapshot = _snapshot(manifest_path)

    with _settings_lock(settings_path):
        settings = _validate_settings(
            _load_json(settings_path, default={}), settings_path
        )
        settings_snapshot = _snapshot(settings_path)
        servers = settings["mcpServers"]
        current_server = servers.get(MCP_SERVER_NAME)
        managed_server = (
            previous_manifest.get("managed_server") if previous_manifest else None
        )
        compatible = current_server in (None, desired_server, managed_server)
        if not compatible and not force:
            raise KiroInstallError(
                f"MCP server '{MCP_SERVER_NAME}' already has different settings in "
                f"{settings_path}; rerun with --force to replace it"
            )

        if previous_manifest:
            had_previous = bool(previous_manifest.get("had_previous_server"))
            previous_server = previous_manifest.get("previous_server")
            previous_files = previous_manifest.get("previous_files", {})
        else:
            had_previous = (
                current_server is not None and current_server != desired_server
            )
            previous_server = current_server if had_previous else None
            previous_files = asset_snapshots

        manifest = {
            "schema_version": 2,
            "tokenlens_version": __version__,
            "workspace": str(root),
            "mcp_scope": mcp_scope,
            "mcp_settings": str(settings_path),
            "had_previous_server": had_previous,
            "previous_server": previous_server,
            "managed_server": desired_server,
            "files": {
                str(relative): _sha256(content) for relative, content in assets.items()
            },
            "previous_files": previous_files,
            "removed_legacy_hooks": [
                str(path.relative_to(root)) for path in stock_legacy
            ],
        }

        try:
            if current_server != desired_server:
                backup = _backup(settings_path)
                servers[MCP_SERVER_NAME] = desired_server
                _atomic_write(settings_path, _json_text(settings))

            for relative, content in assets.items():
                destination = root / relative
                if (
                    not destination.exists()
                    or destination.read_text(encoding="utf-8") != content
                ):
                    _atomic_write(destination, content, mode=0o644)

            for path in stock_legacy:
                path.unlink()

            _atomic_write(manifest_path, _json_text(manifest), mode=0o600)
        except Exception as exc:
            try:
                _restore_snapshot(settings_path, settings_snapshot)
                for relative, snapshot in asset_snapshots.items():
                    _restore_snapshot(root / relative, snapshot)
                for path, snapshot in legacy_snapshots.items():
                    _restore_snapshot(Path(path), snapshot)
                _restore_snapshot(manifest_path, manifest_snapshot)
            except Exception as rollback_exc:
                raise KiroInstallError(
                    f"installation failed ({exc}); rollback also failed: {rollback_exc}"
                ) from rollback_exc
            raise KiroInstallError(
                f"installation failed and was rolled back: {exc}"
            ) from exc

    verification = verify_registration(root, probe=verify) if verify else []

    return {
        "workspace": str(root),
        "mcp_scope": mcp_scope,
        "mcp_settings": str(settings_path),
        "manifest": str(manifest_path),
        "backup": str(backup) if backup else None,
        "migrated_legacy_hooks": [str(path) for path in stock_legacy],
        "preserved_legacy_hooks": [str(path) for path in customized_legacy],
        "verified": verify and not verification,
        "verification_problems": verification,
    }


def doctor(workspace: str | Path = ".", *, probe: bool = True) -> list[str]:
    """Return installation problems; an empty list means installation is healthy."""
    root = _workspace_path(workspace)
    manifest_path = root / MANIFEST_RELATIVE_PATH
    try:
        manifest = _load_json(manifest_path, default=None)
    except KiroInstallError as exc:
        return [str(exc)]
    if not isinstance(manifest, dict):
        return [f"TokenLens is not installed in {root}"]

    problems: list[str] = []
    for relative, expected_hash in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file():
            problems.append(f"missing managed file: {path}")
            continue
        if _sha256(path.read_text(encoding="utf-8")) != expected_hash:
            problems.append(f"modified managed file: {path}")

    settings_value = manifest.get("mcp_settings")
    if not isinstance(settings_value, str) or not settings_value:
        problems.append("installation manifest has no MCP settings path")
        return problems
    settings_path = Path(settings_value)
    try:
        settings = _validate_settings(
            _load_json(settings_path, default={}), settings_path
        )
        if settings["mcpServers"].get(MCP_SERVER_NAME) != manifest.get(
            "managed_server"
        ):
            problems.append(f"TokenLens MCP registration differs in {settings_path}")
        elif probe:
            reason = probe_mcp_server(settings["mcpServers"][MCP_SERVER_NAME])
            if reason:
                problems.append(reason)
    except KiroInstallError as exc:
        problems.append(str(exc))

    if manifest.get("tokenlens_version") != __version__:
        problems.append(
            "installed Kiro assets use TokenLens "
            f"{manifest.get('tokenlens_version')}; current CLI is {__version__}"
        )
    stock_legacy, customized_legacy = _legacy_hook_sets(root)
    for path in [*stock_legacy, *customized_legacy]:
        problems.append(f"legacy TokenLens hook requires manual review: {path}")
    return problems


def uninstall(
    workspace: str | Path = ".",
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Remove only files and MCP settings owned by the TokenLens installer."""
    root = _workspace_path(workspace)
    manifest_path = root / MANIFEST_RELATIVE_PATH
    manifest = _load_json(manifest_path, default=None)
    if not isinstance(manifest, dict):
        raise KiroInstallError(f"TokenLens is not installed in {root}")
    _verify_owned_files(root, manifest, force=force)

    settings_path = Path(manifest["mcp_settings"])
    with _settings_lock(settings_path):
        settings = _validate_settings(
            _load_json(settings_path, default={}), settings_path
        )
        servers = settings["mcpServers"]
        current_server = servers.get(MCP_SERVER_NAME)
        managed_server = manifest.get("managed_server")
        if current_server != managed_server and not force:
            raise KiroInstallError(
                f"TokenLens MCP registration was modified in {settings_path}; "
                "rerun with --force to remove it"
            )
        if manifest.get("had_previous_server"):
            servers[MCP_SERVER_NAME] = manifest.get("previous_server")
        else:
            servers.pop(MCP_SERVER_NAME, None)
        _backup(settings_path)
        _atomic_write(settings_path, _json_text(settings))

    removed: list[str] = []
    restored: list[str] = []
    previous_files = manifest.get("previous_files", {})
    for relative in manifest.get("files", {}):
        path = root / relative
        snapshot = previous_files.get(relative, {"existed": False})
        if snapshot.get("existed"):
            _restore_snapshot(path, snapshot)
            restored.append(str(path))
        elif path.exists():
            path.unlink()
            removed.append(str(path))
    manifest_path.unlink(missing_ok=True)
    return {
        "workspace": str(root),
        "removed": removed,
        "restored": restored,
        "mcp_settings": str(settings_path),
    }
