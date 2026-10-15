# Contributing to TokenLens

Thank you for helping improve TokenLens. Contributions may include bug reports,
feature proposals, documentation, tests, and code changes.

## Before contributing

- Search existing issues and pull requests before opening a duplicate.
- Open an issue before starting a large or behavior-changing contribution.
- Never include credentials, customer data, confidential information, or
  proprietary source code.
- Do not add organization-specific paths, artifact names, or workflow
  identifiers to source.
- Report suspected vulnerabilities privately as described in
  [SECURITY.md](SECURITY.md), never in a public issue.
- Follow the [Amazon Open Source Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

TokenLens requires Python 3.11 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -c constraints-release.txt -e ".[dev,tokenizers,mcp]"
```

## Making changes

1. Fork the repository and create a focused branch from `main`.
2. Keep changes limited to one concern; avoid unrelated formatting or refactors.
3. Add or update tests for changed behavior.
4. Update documentation and `CHANGELOG.md` for user-visible changes.
5. Use clear commit messages; Conventional Commits are preferred.
6. Open a pull request using the repository template.

## Validation

Run before submitting:

```bash
pytest -q
ruff check .
ruff format --check .
python power/scripts/validate_power.py
python skill/scripts/validate_skill.py
```

For packaging or installation changes, also run:

```bash
PYTHON=.venv/bin/python ./scripts/build-release.sh
```

The pull request description must identify tests run, compatibility impact,
security/privacy considerations, and any remaining limitations.

## Pull request review

Maintainers may request changes for correctness, security, compatibility,
documentation, test coverage, or project scope. Automated checks must pass
before merge. Review and response times are best effort.

## Licensing

By submitting a contribution, you agree that it may be distributed under this
project's [MIT No Attribution (MIT-0) License](LICENSE). You must have the right to submit the
work. A Contributor License Agreement may be required for some contributions.
