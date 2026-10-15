# Releasing TokenLens

This document describes the release process. Releases require project
maintainer approval and completion of the applicable open-source, license,
security, and artifact-signing requirements.

## Public distribution identity

The public Python distribution is `aws-samples-tokenlens` because the PyPI name
`tokenlens` belongs to an unrelated project. The Python import package, CLI
command, and MCP server remain named `tokenlens`.

Verify and reserve the distribution name during onboarding for the approved
distribution channel. Update all metadata, tests, installer scripts, and
documentation if the approved name changes.

## Prepare the release

1. Start from an up-to-date, clean `main` branch.
2. Confirm the intended version follows Semantic Versioning.
3. Update every version surface:
   - `pyproject.toml`;
   - `src/tokenlens/__init__.py`;
   - `scripts/install-kiro.sh`;
   - README, Power, and Skill installation examples;
   - packaging/version tests.
4. Move relevant entries from `CHANGELOG.md`'s `Unreleased` section into a
   versioned release section with the release date.
5. Refresh direct and transitive dependency review; update
   `constraints-release.txt` only after compatibility and vulnerability tests.
6. Complete the required security, license, and third-party software reviews.

## Validate

```bash
pytest -q
ruff check .
ruff format --check .
python power/scripts/validate_power.py
python skill/scripts/validate_skill.py
PYTHON=.venv/bin/python ./scripts/build-release.sh
```

Inspect the generated wheel and source archive. Verify they contain only
intended source, resources, documentation, and LICENSE. Install the
wheel in a clean virtual environment and exercise:

```bash
tokenlens --version
tokenlens bench --json
tokenlens kiro install --workspace <temporary-workspace>
tokenlens kiro doctor --workspace <temporary-workspace>
tokenlens kiro uninstall --workspace <temporary-workspace>
```

Run the security scanners, dependency audit, and secret scan. Resolve or
formally disposition all blocking findings.

## Publish

1. Verify `dist/SHA256SUMS` for every release artifact.
2. Apply required artifact signing and provenance controls.
3. Publish the wheel and source archive using an approved trusted-publishing
   workflow; do not use long-lived repository credentials.
4. Create a signed version tag and GitHub Release from the reviewed commit.
5. Attach installer, constraints, checksums, and any required attestations.
6. Verify installation from the public index and GitHub Release on a clean
   macOS or Linux environment.
7. Confirm release links, issue tracker, documentation, and changelog metadata.

## Rollback

If a release is unsafe or unusable, stop promotion, document impact, publish a
fixed patch release, and use the package index's supported yanking mechanism
when appropriate. Do not replace immutable release artifacts under an existing
version.
