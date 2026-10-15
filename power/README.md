# TokenLens Kiro Power

Optional discovery bundle for TokenLens. Primary installation path is the
versioned CLI installer because it can safely register MCP, steering, and hooks
without relying on keyword-triggered Power activation.

## Recommended one-click installation

After `tokenlens` is published to your approved Python registry:

```bash
pipx install --pip-args="--constraint ./constraints-release.txt" \
  "aws-samples-tokenlens[mcp,tokenizers]==0.2.0"
tokenlens kiro install --workspace /path/to/project
```

Or distribute and run pinned `scripts/install-kiro.sh` release asset with the
matching `constraints-release.txt` file in the same directory. It safely
reinstalls or upgrades an existing TokenLens tool installation:

```bash
./install-kiro.sh /path/to/project
```

The installer:

- registers TokenLens in workspace `.kiro/settings/mcp.json` by default;
- installs always-on steering and a v1 large-read routing hook;
- uses the exact Python interpreter that owns the installed package;
- preserves unrelated MCP servers and creates backups before changes;
- records hashes and pre-existing file snapshots in
  `.kiro/tokenlens-install.json` for safe upgrades/removal;
- removes exact stock 0.1 hooks while preserving customized legacy hooks for
  manual review.

Use `--mcp-scope user` to register the server in
`~/.kiro/settings/mcp.json` while keeping steering and hooks in the workspace.
## Verify and remove

```bash
tokenlens --version
tokenlens kiro doctor --workspace /path/to/project
tokenlens stats
```

Reconnect Kiro after install so it loads the MCP server and hook.

```bash
tokenlens kiro uninstall --workspace /path/to/project
```

Modified managed files cause install/uninstall to stop. Review changes before
using `--force`.

## Optional Power import

Power activation remains useful for discovery and onboarding, but it is not the
always-on mechanism. Import this `power/` directory through Kiro's custom Power
flow. The Power's `name` field (`tokenlens`) is stable across releases.

`mcp.json` and `mcp-standalone.json` intentionally declare the same six tools.
Capture, RCS, response post-processing, and model routing retain runtime defaults;
the bundle does not silently enable local content storage.

## Manual fallback

If the CLI installer cannot be used:

1. Install exact TokenLens version with `pipx` or `uv`.
2. Merge the `tokenlens` entry from `mcp-standalone.json` into the appropriate
   Kiro `mcp.json` without replacing unrelated settings.
3. Copy `steering/tokenlens-always-on.md` into `.kiro/steering/`.
4. Install repository `.kiro/hooks/tokenlens-route-reads.json` and its helper.

Do not install former steering create/save compression hooks. Generated
Markdown can retrigger those hooks and duplicate steering context.

## Runtime behavior

- Hook prefilter blocks native reads at 8 KB by default. Override with
  `TOKENLENS_READ_HOOK_MIN_BYTES`.
- MCP compressed-read threshold defaults to 2,000 tokens. Override with
  `TOKENLENS_ALWAYS_ON_READ_ROUTING_THRESHOLD`.
- Disable generic routing with
  `TOKENLENS_ALWAYS_ON_READ_ROUTING_ENABLED=false`.
- History trimming occurs only when `tokenlens_optimize_request` runs.
- `tokenlens_read_file` is UTF-8-only, sensitive-path guarded, fail-open, and
  profitability gated.

## Validate bundle

```bash
python power/scripts/validate_power.py
```
