"""Validate the TokenLens Agent Skill bundle for standard conformance.

Deterministic structural/content checks, no live agent required. Exits
non-zero with a clear message on the first failure so it can gate CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "tokenlens"

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_REQUIRED_TOOLS = (
    "tokenlens_compress",
    "tokenlens_read_file",
    "tokenlens_optimize_request",
    "tokenlens_postprocess_response",
    "tokenlens_stats",
    "tokenlens_retrieve",
)


class ValidationError(Exception):
    pass


def parse_frontmatter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValidationError("SKILL.md has no YAML frontmatter block")
    data: dict = {}
    for line in match.group(1).splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m and m.group(2).strip():
            data[m.group(1)] = m.group(2).strip()
    return data


def check_name(data: dict) -> None:
    name = data.get("name", "")
    if name != "tokenlens":
        raise ValidationError(f"SKILL.md name must be 'tokenlens', got {name!r}")
    if not _NAME_RE.match(name):
        raise ValidationError("SKILL.md name must match ^[a-z0-9-]{1,64}$")


def check_description(data: dict) -> None:
    desc = data.get("description", "")
    if not desc:
        raise ValidationError("SKILL.md frontmatter missing a description")
    if len(desc) > 1024:
        raise ValidationError("SKILL.md description exceeds 1024 characters")


def check_instructions(body: str) -> None:
    for tool in _REQUIRED_TOOLS:
        if tool not in body:
            raise ValidationError(f"SKILL.md missing tool-usage instruction for {tool}")


def validate() -> None:
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.is_file():
        raise ValidationError(f"missing {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    data = parse_frontmatter(text)
    check_name(data)
    check_description(data)
    check_instructions(text)


def main() -> int:
    try:
        validate()
    except ValidationError as exc:
        print(f"SKILL BUNDLE INVALID: {exc}", file=sys.stderr)
        return 1
    print("Skill bundle valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
