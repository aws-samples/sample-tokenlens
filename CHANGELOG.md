# Changelog

All notable changes to TokenLens are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added AWS Samples contribution, conduct, security, support, licensing, and
  release documentation.
- GitHub issue and pull request templates.
- Added release process documentation in `RELEASING.md`.
- Added README guidance on stating TokenLens numbers, including fixture
  provenance and prohibited wording for benchmark, eval, and stats output.
- Added a README safety and privacy limitations section clarifying that
  sensitive-path checks are pattern-based defense in depth rather than a
  security boundary, and that compression is lossy by design.
- Added README guidance for referring to comparable projects without ranked
  side-by-side comparisons.
- `tokenlens mcp --probe` builds the MCP server, reports success, and exits
  without serving, so a registration can be validated without a client.
- `tokenlens kiro install` now ends with a validation step that starts the
  registered MCP server, and exposes `--verify/--no-verify`. `install()` returns
  `verified` and `verification_problems`.
- `tokenlens kiro doctor` starts the registered server as part of its checks and
  exposes `--probe/--no-probe`. New `verify_registration()` helper reports a
  missing, disabled, mismatched, or non-runnable registration.
- `scripts/install-kiro.sh` probes the MCP server before it modifies a workspace,
  installs from a release wheel beside the script or in `dist/` when present,
  and fails with a clear message when `tokenlens` is not on `PATH`.

### Fixed

- Kiro MCP registration failed on a fresh machine. The installer wrote the
  resolved target of `sys.executable`, which for a virtual environment is often
  a base interpreter that cannot import `tokenlens`, so Kiro could not start the
  server ("No module named tokenlens"). Candidate interpreters are now verified
  before being written to MCP settings.
- Building the MCP server raised an unclear `ImportError` when the installed MCP
  Python SDK was 2.x, which removed the `mcp.server.fastmcp` API TokenLens uses.
  The error now names the supported range (`mcp>=1.2,<2`), the installed
  version, and the reinstall command. Declared optional dependencies use ranges
  rather than exact pins for the same reason, with exact versions still applied
  through `constraints-release.txt`.
- Context-file compression was not idempotent. The leading-filler pattern is
  anchored at the start of the text, so one substitution could expose another
  phrase and a second pass kept shrinking the file — `"GREAT QUESTION\nGREAT
  QUESTION"` became one line, then an empty file. Filler is now stripped to a
  fixed point in a single pass, and text that is entirely filler is preserved
  rather than deleted. This also unblocks `scripts/build-release.sh`, which runs
  the test suite before building.

### Changed

- Adopted MIT-0, the default license for AWS Samples content, and added public
  package metadata.
- Renamed the public Python distribution to `aws-samples-tokenlens` while
  retaining the `tokenlens` import package, CLI, and MCP server names.

### Security

- Hardened the installer's subprocess boundary following a security
  review: interpreter candidates and registered MCP commands must be
  absolute paths to executable regular files, module names passed to the
  import probe are validated against a strict pattern, and both invocations
  use argument lists with `shell=False` and a timeout. Documented that the
  `mcp` and `tokenizers` extras are supported only when installed with
  `constraints-release.txt` applied.

## [0.2.0]

### Added

- Transactional `tokenlens kiro install`, `doctor`, and `uninstall` commands.
- Kiro steering and large-file read-routing hook resources.
- MCP tools for compression, file reads, request optimization, response
  post-processing, savings statistics, and reversible-content retrieval.
- Reproducible wheel and source-archive release scripts with exact constraints
  and SHA-256 checksums.
- Installer, packaging, migration, rollback, compatibility, and property tests.

### Changed

- Replaced recursive steering hooks with guarded v1 `PreToolUse` routing.
- Unified Power and standalone MCP configuration.
- Pinned direct and release dependencies to validated versions.

### Security

- Added sensitive-path protection, local-storage opt-ins, atomic configuration
  updates, managed-file ownership checks, rollback, and legacy-hook migration.
- Completed security scans with no findings.
