# TokenLens Agent Skill

Packages TokenLens as a portable [Agent Skill](https://agentskills.io) so any
compatible agent (Kiro, Claude Code, and others) can use the TokenLens tools to
reduce token consumption. This is the cross-agent counterpart to the Kiro Power
in `power/`.

## Prerequisite

The skill directs the agent to call the TokenLens MCP tools, so the TokenLens
MCP server must be registered with your agent. Install the CLI and register it:

```bash
pipx install --pip-args="--constraint ./constraints-release.txt" \
  "aws-samples-tokenlens[mcp,tokenizers]==0.2.0"
# or: uv tool install --constraints ./constraints-release.txt --with "mcp==1.29.1" --with "tiktoken==0.13.0" "aws-samples-tokenlens==0.2.0"
```

Then add the server to your agent's MCP config (command `tokenlens`, args
`["mcp"]`). Without the server, the skill's instructions reference tools that
are not present.

## Install

The skill is the `tokenlens/` folder (contains `SKILL.md`).

### Kiro

- Global (all workspaces): copy `skill/tokenlens/` into `~/.kiro/skills/`
- Workspace (one project): copy `skill/tokenlens/` into `.kiro/skills/`

Or use the Kiro panel: Agent Steering & Skills → + → Import a skill → Local
folder (select `skill/tokenlens/`) or GitHub (URL to the `skill/tokenlens`
subdirectory).

### Claude Code

```bash
cp -r skill/tokenlens ~/.claude/skills/
```

### Other harnesses

Any Agent-Skills-compatible tool can import `skill/tokenlens/` from a local
folder or a GitHub subdirectory URL pointing at `skill/tokenlens`.

## Verify

Start a session and ask the agent to compress a large block of text, or check
`tokenlens_stats`. If the tools respond, the skill and MCP server are wired up.

## Validate the bundle

```bash
python skill/scripts/validate_skill.py
```

Exits non-zero if `SKILL.md` is missing, its frontmatter is malformed, the
`name` is wrong, or a required tool-usage instruction is absent.
