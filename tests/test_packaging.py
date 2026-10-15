from __future__ import annotations

import json
import os
import tomllib
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import tokenlens
from tokenlens.bench import default_fixtures_dir, run_bench

_ROOT = Path(__file__).resolve().parent.parent


def test_version_is_consistent_across_release_surfaces():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == tokenlens.__version__ == "0.2.0"
    assert 'TOKENLENS_VERSION="${TOKENLENS_VERSION:-0.2.0}"' in (
        _ROOT / "scripts/install-kiro.sh"
    ).read_text(encoding="utf-8")
    bootstrap = (_ROOT / "scripts/install-kiro.sh").read_text(encoding="utf-8")
    assert "uv tool install --force" in bootstrap
    assert "aws-samples-tokenlens" in bootstrap
    assert '--constraints "$CONSTRAINTS"' in bootstrap
    assert '--with "mcp>=1.2,<2"' in bootstrap
    assert '--with "tiktoken>=0.6"' in bootstrap
    assert "pipx install --force" in bootstrap
    assert '--pip-args="--constraint $CONSTRAINTS"' in bootstrap
    assert "$(tokenlens --version)" in bootstrap
    # The bootstrap must prove the MCP server starts before it edits a workspace.
    assert "tokenlens mcp --probe" in bootstrap


def test_packaged_resources_are_available_and_functional():
    root = files("tokenlens.resources")
    assert root.joinpath("kiro", "tokenlens-always-on.md").is_file()
    assert root.joinpath("tokenlens.example.yaml").is_file()
    fixture = root.joinpath("fixtures", "sample-request.json")
    assert fixture.is_file()
    json.loads(fixture.read_text(encoding="utf-8"))

    fixtures = default_fixtures_dir()
    assert fixtures.is_dir()
    report = run_bench([fixtures])
    assert report.files
    assert report.total_saved > 0


def test_power_mcp_configs_are_identical_and_storage_is_opt_in():
    power = json.loads((_ROOT / "power/mcp.json").read_text(encoding="utf-8"))
    standalone = json.loads(
        (_ROOT / "power/mcp-standalone.json").read_text(encoding="utf-8")
    )
    assert standalone == power
    server = power["mcpServers"]["tokenlens"]
    assert "tokenlens_retrieve" in server["autoApprove"]
    assert "env" not in server


def test_packaged_steering_matches_power_bundle():
    packaged = (
        files("tokenlens.resources")
        .joinpath("kiro", "tokenlens-always-on.md")
        .read_text(encoding="utf-8")
    )
    power = (_ROOT / "power/steering/tokenlens-always-on.md").read_text(
        encoding="utf-8"
    )
    assert packaged == power


def test_bootstrap_is_executable():
    mode = (_ROOT / "scripts/install-kiro.sh").stat().st_mode
    assert mode & os.X_OK


def test_release_constraints_pin_every_declared_dependency():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    constraint_lines = [
        line.strip()
        for line in (_ROOT / "constraints-release.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    ]
    constraints = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, constraint_lines)
    }
    assert all(
        len(requirement.specifier) == 1
        and next(iter(requirement.specifier)).operator == "=="
        for requirement in constraints.values()
    )

    declared = list(project["build-system"]["requires"])
    declared.extend(project["project"]["dependencies"])
    for requirements in project["project"]["optional-dependencies"].values():
        declared.extend(requirements)
    declared_requirements = list(map(Requirement, declared))

    # Declared requirements may use ranges: an exact `mcp` pin in project
    # metadata broke fresh installs. Reproducibility is guaranteed by
    # constraints-release.txt instead, so every declared requirement must be
    # covered there, and the pinned version must satisfy the declared range.
    missing = {
        requirement.name
        for requirement in declared_requirements
        if canonicalize_name(requirement.name) not in constraints
    }
    assert missing == set()

    for requirement in declared_requirements:
        pinned = constraints[canonicalize_name(requirement.name)]
        version = next(iter(pinned.specifier)).version
        assert requirement.specifier.contains(version), (
            f"{pinned.name}=={version} does not satisfy declared "
            f"{requirement.name}{requirement.specifier}"
        )


def test_release_script_ships_and_checksums_constraints():
    script = (_ROOT / "scripts/build-release.sh").read_text(encoding="utf-8")
    assert 'PIP_CONSTRAINT="$CONSTRAINTS"' in script
    assert "cp constraints-release.txt dist/constraints-release.txt" in script
    assert "ARTIFACT_NAME" in script
    assert 're.sub(r"[-_.]+", "_", sys.argv[1])' in script
    assert "constraints-release.txt > SHA256SUMS" in script
