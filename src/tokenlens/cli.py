"""Command-line interface for TokenLens.

Commands:
    tokenlens start    Start the optimization proxy.
    tokenlens stats    Print aggregate savings from the event log.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import click

from .config import load_config
from .metrics import MetricsRecorder


@click.group()
@click.version_option(package_name="aws-samples-tokenlens")
def main() -> None:
    """TokenLens: intelligent token optimization for LLM workflows."""


@main.command()
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
@click.option("--host", default=None, help="Override the proxy bind host.")
@click.option("--port", default=None, type=int, help="Override the proxy bind port.")
def start(config_path: str | None, host: str | None, port: int | None) -> None:
    """Start the TokenLens optimization proxy."""
    import uvicorn

    config = load_config(config_path)
    if host:
        config.proxy.host = host
    if port:
        config.proxy.port = port

    # Import here so that `tokenlens stats` does not require the web stack.
    from .adapters.proxy import create_app

    app = create_app(config)

    click.echo(
        f"TokenLens proxy listening on http://{config.proxy.host}:{config.proxy.port}"
    )
    click.echo(f"Forwarding to {config.proxy.upstream_base_url}")
    click.echo("Point your client at the proxy, e.g.:")
    click.echo(
        f"  export ANTHROPIC_BASE_URL=http://{config.proxy.host}:{config.proxy.port}"
    )
    uvicorn.run(app, host=config.proxy.host, port=config.proxy.port, log_level="info")


@main.command()
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
def stats(config_path: str | None) -> None:
    """Print aggregate token savings recorded so far."""
    config = load_config(config_path)
    recorder = MetricsRecorder(config.metrics.dir, enabled=True)
    click.echo(json.dumps(recorder.stats(), indent=2))


@main.command(name="compress-file")
@click.argument("path", type=click.Path())
@click.option(
    "--out",
    "out_path",
    default=None,
    help="Output path (default: <path>.tokenlens.md).",
)
@click.option(
    "--in-place", "in_place", is_flag=True, help="Overwrite the original file."
)
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
def compress_file(path, out_path, in_place, config_path) -> None:
    """Compress a persistent context file (steering, AGENTS.md, CLAUDE.md).

    Preserves code blocks, inline code, file paths, URLs, and identifiers
    verbatim. Writes a separate compressed copy by default; use --in-place to
    overwrite the original.
    """
    import os

    from .api import compress_context_file

    if not os.path.isfile(path):
        raise click.ClickException(f"not a file: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except UnicodeDecodeError:
        raise click.ClickException(f"not a UTF-8 text file: {path}")
    except OSError as exc:
        raise click.ClickException(f"read error: {exc}")

    result = compress_context_file(content)

    destination = path if in_place else (out_path or f"{path}.tokenlens.md")
    with open(destination, "w", encoding="utf-8") as handle:
        handle.write(result["text"])

    config = load_config(config_path)
    if config.metrics.enabled:
        recorder = MetricsRecorder(config.metrics.dir, enabled=True)
        recorder.record(
            recorder.make_event(
                model=f"context-file:{os.path.basename(path)}",
                input_tokens_before=result["original_tokens"],
                input_tokens_after=result["compressed_tokens"],
                stage_savings={"context_file_compression": result["tokens_saved"]},
            )
        )

    click.echo(
        f"{path}: {result['original_tokens']} -> {result['compressed_tokens']} tokens "
        f"({result['tokens_saved']} saved)  written to {destination}"
    )


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
@click.option(
    "--mode",
    default=None,
    type=click.Choice(["conservative", "balanced", "aggressive"]),
    help="Override compression mode for this run.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
def bench(paths, config_path, mode, as_json) -> None:
    """Measure token savings offline on request-body JSON files.

    PATHS may be files or directories of Anthropic Messages request bodies.
    With no PATHS, the bundled sample fixtures are used. No API key required.
    """
    from .bench import default_fixtures_dir, run_bench

    config = load_config(config_path)
    if mode:
        config.compression.mode = mode

    targets = list(paths)
    if not targets:
        fixtures = default_fixtures_dir()
        if not fixtures.exists():
            raise click.ClickException(
                "No paths given and bundled fixtures were not found. "
                "Provide a path to request-body JSON files."
            )
        targets = [str(fixtures)]
        click.echo(f"No paths given; using bundled fixtures at {fixtures}\n")

    report = run_bench(targets, config)

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return

    _print_bench_table(report)


def _print_bench_table(report) -> None:
    name_width = max((len(_short(f.path)) for f in report.files), default=4)
    name_width = max(name_width, 4)
    header = f"{'file'.ljust(name_width)}  {'before':>8}  {'after':>8}  {'saved':>8}  {'%':>6}"
    click.echo(header)
    click.echo("-" * len(header))
    for f in report.files:
        if f.error:
            click.echo(f"{_short(f.path).ljust(name_width)}  ERROR: {f.error}")
            continue
        click.echo(
            f"{_short(f.path).ljust(name_width)}  "
            f"{f.input_tokens_before:>8}  {f.input_tokens_after:>8}  "
            f"{f.tokens_saved:>8}  {f.savings_percent:>5}%"
        )
    click.echo("-" * len(header))
    click.echo(
        f"{'TOTAL'.ljust(name_width)}  "
        f"{report.total_before:>8}  {report.total_after:>8}  "
        f"{report.total_saved:>8}  {report.savings_percent:>5}%"
    )


def _short(path: str) -> str:
    import os

    return os.path.basename(path)


@main.command(name="eval")
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
@click.option(
    "--mode",
    default=None,
    type=click.Choice(["conservative", "balanced", "aggressive"]),
    help="Compression mode to evaluate.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
def eval_cmd(config_path, mode, as_json) -> None:
    """Run the quality suite: verify critical information survives compression.

    Checks that errors, code signatures, JSON structure/counts, and identifiers
    are preserved. Exits non-zero if any critical information is lost, so this
    can be used as a CI regression gate.
    """
    from .quality import run_quality_suite

    config = load_config(config_path)
    suite_mode = mode or config.compression.mode
    report = run_quality_suite(mode=suite_mode)

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        _print_eval_table(report, suite_mode)

    if not report.passed:
        raise SystemExit(1)


def _print_eval_table(report, mode) -> None:
    click.echo(f"Quality suite (mode: {mode}) — critical information retention\n")
    header = f"{'case':<26}{'type':<8}{'critical':>9}{'kept':>6}{'retain':>8}{'saved%':>8}  verdict"
    click.echo(header)
    click.echo("-" * len(header))
    for o in report.outcomes:
        r = o.result
        verdict = "PASS" if r.passed else "FAIL"
        click.echo(
            f"{r.name:<26}{r.content_type:<8}{r.critical_total:>9}{r.critical_preserved:>6}"
            f"{r.retention * 100:>7.1f}%{o.savings_percent:>7.1f}%  {verdict}"
        )
        for miss in r.missing:
            click.echo(f"    MISSING: {miss[:80]}")
        for note in r.notes:
            click.echo(f"    NOTE: {note}")
    click.echo("-" * len(header))
    overall = "PASS" if report.passed else "FAIL"
    click.echo(
        f"{'OVERALL':<26}{'':<8}{report.total_critical:>9}{report.total_preserved:>6}"
        f"{report.retention * 100:>7.1f}%{'':>8}  {overall}"
    )
    if report.passed:
        click.echo(
            "\nAll critical information preserved. Compression is safe to apply."
        )
    else:
        click.echo(
            "\nCritical information was lost — quality regression. See MISSING lines."
        )


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
@click.option(
    "--format",
    "fmt",
    default="markdown",
    type=click.Choice(["markdown", "json"]),
    help="Output format for the report.",
)
def report(paths, config_path, fmt) -> None:
    """Generate a savings report from captured optimization records.

    PATHS may be capture files or directories. With no PATHS, the configured
    capture directory is used. Enable capture first with
    TOKENLENS_CAPTURE_ENABLED=true (or capture.enabled in config).
    """
    from .report import build_report

    config = load_config(config_path)
    targets = list(paths) or [config.capture.dir]

    rep = build_report(targets)
    if rep.total.count == 0:
        raise click.ClickException(
            "No capture records found. Enable capture "
            "(TOKENLENS_CAPTURE_ENABLED=true) and run some traffic through "
            "the MCP server or proxy first, or pass a path to capture files."
        )

    if fmt == "json":
        click.echo(json.dumps(rep.to_dict(), indent=2))
    else:
        click.echo(rep.to_markdown())


@main.command(name="example-config")
def example_config() -> None:
    """Print the packaged example configuration."""
    text = (
        files("tokenlens.resources")
        .joinpath("tokenlens.example.yaml")
        .read_text(encoding="utf-8")
    )
    click.echo(text, nl=False)


@main.group(name="kiro")
def kiro_group() -> None:
    """Install and manage TokenLens integration for Kiro."""


@kiro_group.command(name="install")
@click.option(
    "--workspace",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--mcp-scope",
    type=click.Choice(["workspace", "user"]),
    default="workspace",
    show_default=True,
)
@click.option(
    "--force", is_flag=True, help="Replace conflicting TokenLens files/settings."
)
@click.option(
    "--verify/--no-verify",
    default=True,
    show_default=True,
    help="After installing, start the registered MCP server to confirm it works.",
)
def kiro_install(workspace: Path, mcp_scope: str, force: bool, verify: bool) -> None:
    """Install MCP registration, steering, and large-read routing hook."""
    from .kiro_install import KiroInstallError, install

    try:
        result = install(workspace, mcp_scope=mcp_scope, force=force, verify=verify)
    except KiroInstallError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"TokenLens installed for {result['workspace']}")
    click.echo(f"MCP settings: {result['mcp_settings']}")
    if result["backup"]:
        click.echo(f"Backup: {result['backup']}")
    if result["migrated_legacy_hooks"]:
        click.echo(
            f"Removed {len(result['migrated_legacy_hooks'])} stock legacy hook(s)."
        )
    for path in result["preserved_legacy_hooks"]:
        click.echo(f"Warning: preserved customized legacy hook: {path}", err=True)

    if not verify:
        click.echo("Validation skipped (--no-verify).")
        click.echo("Reconnect Kiro to load the MCP server and hook.")
        return
    if result["verification_problems"]:
        for problem in result["verification_problems"]:
            click.echo(f"Validation failed: {problem}", err=True)
        raise click.ClickException(
            "TokenLens files were written but the MCP server did not validate. "
            "Fix the problems above, then rerun `tokenlens kiro install` or "
            "`tokenlens kiro doctor`."
        )
    click.echo("Validation: MCP server registered and starts correctly.")
    click.echo("Reconnect Kiro to load the MCP server and hook.")


@kiro_group.command(name="doctor")
@click.option(
    "--workspace",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--probe/--no-probe",
    default=True,
    show_default=True,
    help="Start the registered MCP server to confirm it actually runs.",
)
def kiro_doctor(workspace: Path, probe: bool) -> None:
    """Validate installed TokenLens Kiro assets and MCP registration."""
    from .kiro_install import doctor

    problems = doctor(workspace, probe=probe)
    if problems:
        raise click.ClickException("\n".join(problems))
    click.echo("TokenLens Kiro installation is healthy.")


@kiro_group.command(name="uninstall")
@click.option(
    "--workspace",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
)
@click.option("--force", is_flag=True, help="Remove modified TokenLens-owned state.")
def kiro_uninstall(workspace: Path, force: bool) -> None:
    """Remove files and MCP settings owned by the TokenLens installer."""
    from .kiro_install import KiroInstallError, uninstall

    try:
        result = uninstall(workspace, force=force)
    except KiroInstallError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"TokenLens removed from {result['workspace']}")


@main.command()
@click.option(
    "--config", "config_path", default=None, help="Path to a YAML config file."
)
@click.option(
    "--probe",
    is_flag=True,
    help="Build the server, report success, and exit without serving.",
)
def mcp(config_path: str | None, probe: bool) -> None:
    """Run the TokenLens MCP server over stdio.

    Point any MCP-capable client (Kiro, Cursor, Claude Code, Amazon Q, Copilot)
    at this command. Requires no model credentials and makes no network calls.

    With --probe, build the server and exit 0 without serving. The installer
    uses this to validate a registration before Kiro reconnects.
    """
    config = load_config(config_path)
    from .adapters.mcp_server import create_mcp_server

    server = create_mcp_server(config)
    if probe:
        click.echo("TokenLens MCP server starts correctly.")
        return
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
