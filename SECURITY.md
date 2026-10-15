# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| Earlier versions | No |

Only the latest patch release in a supported series receives security fixes.

## Reporting a vulnerability

Do not report suspected vulnerabilities through public GitHub issues,
discussions, or pull requests. Report them privately through the
[AWS vulnerability reporting process](https://aws.amazon.com/security/vulnerability-reporting/).

Include, when available:

- affected version and commit;
- impacted component and configuration;
- reproduction steps or proof of concept;
- expected security impact;
- suggested mitigation;
- whether the issue is already public.

Do not include real credentials, customer data, or confidential information in
a report. Use synthetic examples and redact sensitive logs.

## Scope

Security reports may cover the Python SDK and CLI, MCP server, optional keyed
proxy, Kiro installer and hooks, release scripts, packaged resources, dependency
configuration, and data-retention behavior.

TokenLens is designed to process content locally. Capture, reversible
compression storage, response post-processing, and model routing remain opt-in.
The keyed proxy is a separate credential-bearing path and must be configured by
the operator.

## Disclosure and fixes

Maintainers will validate reports, determine severity, coordinate remediation,
and publish updates when appropriate. Response and fix timelines vary with
impact and complexity. Please allow coordinated disclosure before publishing
technical details.
