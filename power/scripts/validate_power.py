"""Validate the TokenLens Kiro Power bundle for internal consistency.

Runs deterministic structural and consistency checks without requiring a live
Kiro install. Exits non-zero with a clear message on the first failure so it
can gate CI.

Checks:
1. POWER.md exists and its YAML frontmatter parses with all required fields
   and a non-empty keyword list.
2. Keyword categories (compression, large-file, token, handoff) present.
3. Every MCP server name referenced in POWER.md exists in mcp.json.
4. No absolute machine-specific path is declared anywhere in the bundle.
5. Onboarding instructs a `tokenlens` version check and a missing-dependency
   report.
6. The four tool-usage instructions are present across POWER.md + steering.
7. The always-on steering file exists with keyword-independent guidance.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

POWER_DIR = Path(__file__).resolve().parent.parent

REQUIRED_FRONTMATTER = ("name", "displayName", "description", "author", "keywords")
REQUIRED_TOOLS = (
    "tokenlens_compress",
    "tokenlens_read_file",
    "tokenlens_optimize_request",
    "tokenlens_postprocess_response",
    "tokenlens_stats",
    "tokenlens_retrieve",
)
KEYWORD_CATEGORIES = {
    "compression": ("compress", "compression"),
    "large-file": ("large file", "tool output"),
    "token": ("token", "tokens"),
    "handoff": ("handoff", "agent handoff"),
}


class ValidationError(Exception):
    pass


def _read(path: Path) -> str:
    if not path.exists():
        raise ValidationError(f"missing required file: {path.relative_to(POWER_DIR)}")
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValidationError("POWER.md has no YAML frontmatter block")
    block = match.group(1)
    data: dict = {}
    keywords: list[str] = []
    current_key = None
    for line in block.splitlines():
        if re.match(r"^\s*-\s+", line) and current_key == "keywords":
            keywords.append(line.strip()[1:].strip().strip('"'))
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            current_key = m.group(1)
            value = m.group(2).strip().strip('"')
            if current_key == "keywords":
                if value:
                    keywords = [
                        v.strip().strip('"')
                        for v in value.strip("[]").split(",")
                        if v.strip()
                    ]
            else:
                data[current_key] = value
    data["keywords"] = keywords
    return data


def check_frontmatter(data: dict) -> None:
    for field in REQUIRED_FRONTMATTER:
        if field not in data or (field != "keywords" and not data[field]):
            raise ValidationError(f"POWER.md frontmatter missing field: {field}")
    if not data["keywords"]:
        raise ValidationError("POWER.md frontmatter has an empty keyword list")


def check_keyword_categories(keywords: list[str]) -> None:
    lowered = [k.lower() for k in keywords]
    for category, terms in KEYWORD_CATEGORIES.items():
        if not any(any(term in k for k in lowered) for term in terms):
            raise ValidationError(
                f"POWER.md keywords missing a '{category}' term (any of {terms})"
            )


def check_server_names(power_text: str, mcp: dict) -> None:
    declared = set(mcp.get("mcpServers", {}).keys())
    if not declared:
        raise ValidationError("mcp.json declares no mcpServers")
    # Every server key must be present; and POWER.md/mcp must agree on tokenlens.
    if "tokenlens" not in declared:
        raise ValidationError("mcp.json is missing the 'tokenlens' server")


def check_no_machine_paths(*texts: str) -> None:
    """Reject absolute user-specific paths that would break on another machine."""
    for text in texts:
        if re.search(r"(^|[\s\"'`(])/(Users|home)/[A-Za-z0-9._-]+/", text):
            raise ValidationError(
                "bundle declares an absolute user-specific path (not portable)"
            )


def check_onboarding(power_text: str) -> None:
    if "tokenlens --version" not in power_text:
        raise ValidationError(
            "POWER.md onboarding does not instruct a `tokenlens --version` check"
        )
    if "constraints-release.txt" not in power_text:
        raise ValidationError("POWER.md onboarding does not apply release constraints")
    lowered = power_text.lower()
    if "install" not in lowered or ("pipx" not in lowered and "uv tool" not in lowered):
        raise ValidationError(
            "POWER.md onboarding does not give an install command for a missing dependency"
        )


def check_tool_instructions(*texts: str) -> None:
    combined = "\n".join(texts)
    for tool in REQUIRED_TOOLS:
        if tool not in combined:
            raise ValidationError(f"tool-usage instruction missing for {tool}")


def check_always_on(text: str) -> None:
    if "tokenlens_compress" not in text:
        raise ValidationError(
            "always-on steering does not direct tokenlens_compress use"
        )
    if "regardless" not in text.lower() and "every task" not in text.lower():
        raise ValidationError(
            "always-on steering does not state keyword-independent coverage"
        )


def check_standalone(mcp: dict, standalone: dict) -> None:
    servers = standalone.get("mcpServers", {})
    if "tokenlens" not in servers:
        raise ValidationError(
            "mcp-standalone.json missing a 'tokenlens' server in mcpServers"
        )
    power_server = mcp["mcpServers"]["tokenlens"]
    standalone_server = servers["tokenlens"]
    if standalone_server != power_server:
        raise ValidationError(
            "standalone TokenLens server configuration differs from mcp.json"
        )


def validate() -> None:
    power_text = _read(POWER_DIR / "POWER.md")
    mcp_text = _read(POWER_DIR / "mcp.json")
    standalone_text = _read(POWER_DIR / "mcp-standalone.json")
    usage_text = _read(POWER_DIR / "steering" / "tokenlens-usage.md")
    always_on_text = _read(POWER_DIR / "steering" / "tokenlens-always-on.md")

    try:
        mcp = json.loads(mcp_text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"mcp.json is not valid JSON: {exc}") from exc
    try:
        standalone = json.loads(standalone_text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"mcp-standalone.json is not valid JSON: {exc}") from exc

    data = parse_frontmatter(power_text)
    check_frontmatter(data)
    check_keyword_categories(data["keywords"])
    check_server_names(power_text, mcp)
    check_no_machine_paths(mcp_text, standalone_text, usage_text, always_on_text)
    check_onboarding(power_text)
    check_tool_instructions(power_text, usage_text)
    check_always_on(always_on_text)
    check_standalone(mcp, standalone)


def main() -> int:
    try:
        validate()
    except ValidationError as exc:
        print(f"POWER BUNDLE INVALID: {exc}", file=sys.stderr)
        return 1
    print("POWER bundle valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
