"""Generate realistic sample request bodies for `tokenlens bench`.

Run:  python fixtures/generate_fixtures.py
Produces Anthropic-style Messages request bodies that mirror common
token-heavy coding-agent traffic: a large JSON tool result, a verbose build
log, and a large source file provided as context.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def big_json_tool_result() -> dict:
    rows = [
        {
            "id": i,
            "name": f"resource-{i}",
            "region": "us-east-1" if i % 2 else "eu-west-1",
            "status": "ACTIVE" if i % 3 else "PENDING",
            "arn": f"arn:aws:service:us-east-1:123456789012:resource/resource-{i}",
            "tags": {"env": "prod", "team": f"team-{i % 5}"},
        }
        for i in range(250)
    ]
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": "List the resources and summarize by status."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "list_resources",
                        "input": {"limit": 250},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": json.dumps(rows),
                    }
                ],
            },
        ],
    }


def build_log() -> dict:
    lines = []
    for i in range(400):
        lines.append(f"[{i:04d}] 12:30:{i % 60:02d} INFO  compiling module_{i} ... ok")
        if i % 50 == 0 and i:
            lines.append(f"[{i:04d}] 12:30:{i % 60:02d} INFO  {i} modules done, 0 warnings")
    lines.insert(210, "[0210] 12:30:30 ERROR  module_210 failed: undefined symbol 'foo_bar'")
    lines.insert(211, "  at src/module_210.c:42")
    lines.insert(212, "  at src/linker.c:88")
    lines.append("[0400] 12:31:00 ERROR  build failed: 1 error, 0 warnings")
    log = "\n".join(lines)
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Why did the build fail?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_2", "name": "run_build", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_2", "content": log}
                ],
            },
        ],
    }


def large_code_file() -> dict:
    parts = ["import os", "import sys", "from dataclasses import dataclass", ""]
    parts.append("class ResourceManager:")
    for m in range(8):
        parts.append(f"    def operation_{m}(self, payload):")
        for line in range(15):
            parts.append(
                f"        step_{line} = transform(payload['field_{line}']) "
                f"+ compute({line}) - normalize({m})"
            )
        parts.append("        return step_0")
        parts.append("")
    code = "\n".join(parts)
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here is the file for context:\n\n" + code},
                    {"type": "text", "text": "Add a docstring to each method."},
                ],
            }
        ],
    }


def main() -> None:
    fixtures = {
        "big-json-tool-result.json": big_json_tool_result(),
        "build-log.json": build_log(),
        "large-code-file.json": large_code_file(),
    }
    for name, body in fixtures.items():
        (OUT / name).write_text(json.dumps(body, indent=2), encoding="utf-8")
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
