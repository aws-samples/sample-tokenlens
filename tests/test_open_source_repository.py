from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_REQUIRED_PUBLIC_FILES = (
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "SUPPORT.md",
    "CHANGELOG.md",
    "RELEASING.md",
)


def _text(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def test_required_open_source_files_exist_and_are_nonempty():
    for relative in _REQUIRED_PUBLIC_FILES:
        path = _ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_size > 100, relative


def test_license_is_mit_no_attribution():
    license_text = _text("LICENSE")
    assert license_text.startswith("MIT No Attribution")
    assert "Copyright 2026 Amazon.com, Inc. or its affiliates" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text
    assert (
        "copyright notice and this permission notice shall be included"
        not in license_text
    )


def test_project_metadata_declares_public_repository_and_governance():
    project = tomllib.loads(_text("pyproject.toml"))["project"]
    assert project["name"] == "aws-samples-tokenlens"
    assert project["license"] == "MIT-0"
    assert set(project["license-files"]) == {"LICENSE"}
    assert project["maintainers"]
    assert project["keywords"]
    assert "Intended Audience :: Developers" in project["classifiers"]
    assert project["urls"] == {
        "Homepage": "https://github.com/aws-samples/sample-tokenlens",
        "Documentation": "https://github.com/aws-samples/sample-tokenlens#readme",
        "Source": "https://github.com/aws-samples/sample-tokenlens",
        "Issues": "https://github.com/aws-samples/sample-tokenlens/issues",
        "Changelog": "https://github.com/aws-samples/sample-tokenlens/blob/main/CHANGELOG.md",
    }


def test_readme_links_public_project_policies():
    readme = _text("README.md")
    for relative in (
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "SECURITY.md",
        "SUPPORT.md",
        "CHANGELOG.md",
        "RELEASING.md",
        "LICENSE",
    ):
        assert f"]({relative})" in readme
    assert "not an AWS service" in readme


def test_changelog_and_release_script_cover_current_version():
    project = tomllib.loads(_text("pyproject.toml"))["project"]
    version = project["version"]
    assert f"## [{version}]" in _text("CHANGELOG.md")
    release_script = _text("scripts/build-release.sh")
    assert 'grep -F "## [$VERSION]" CHANGELOG.md' in release_script


def test_source_distribution_manifest_includes_public_docs():
    manifest = _text("MANIFEST.in")
    for relative in _REQUIRED_PUBLIC_FILES:
        assert f"include {relative}" in manifest


def test_github_issue_templates_are_valid_yaml():
    issue_dir = _ROOT / ".github/ISSUE_TEMPLATE"
    bug = yaml.safe_load((issue_dir / "bug_report.yml").read_text(encoding="utf-8"))
    feature = yaml.safe_load(
        (issue_dir / "feature_request.yml").read_text(encoding="utf-8")
    )
    config = yaml.safe_load((issue_dir / "config.yml").read_text(encoding="utf-8"))

    assert bug["name"] == "Bug report"
    assert feature["name"] == "Feature request"
    assert config["blank_issues_enabled"] is False
    assert any("Security" in link["name"] for link in config["contact_links"])


def test_pull_request_template_covers_quality_and_security():
    template = _text(".github/PULL_REQUEST_TEMPLATE.md")
    for section in (
        "## Summary",
        "## Validation",
        "## Compatibility and release notes",
        "## Security and privacy",
        "## Contribution",
    ):
        assert section in template
