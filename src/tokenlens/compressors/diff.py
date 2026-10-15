"""Unified diff compressor.

Git diffs (and `diff -u` output) pasted or returned as tool output are
dominated by unchanged context lines that surround the actual edit. This
compressor keeps every changed line (`+`/`-`) and every hunk header
verbatim, and collapses long runs of unchanged context lines within a hunk
into a short placeholder stating how many lines were omitted.
"""

from __future__ import annotations

import re

from . import Compressor

# A unified-diff hunk header, e.g. "@@ -12,7 +12,6 @@" (optionally followed
# by trailing function-context text, which we don't need to match).
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)

# A file header line within a hunk region signals a new file's diff section.
_FILE_HEADER = re.compile(r"^(---|\+\+\+) ")

# Maximum run of consecutive context lines to keep before collapsing, and how
# many lines to preserve on each edge of a collapsed run, by mode.
_RUN_THRESHOLD = {"conservative": 12, "balanced": 8, "aggressive": 5}
_EDGE_KEEP = {"conservative": 4, "balanced": 3, "aggressive": 2}


class DiffCompressor(Compressor):
    name = "diff"

    def matches(self, text: str) -> bool:
        return bool(_HUNK_HEADER.search(text))

    def _compress(self, text: str, mode: str) -> str:
        threshold = _RUN_THRESHOLD.get(mode, _RUN_THRESHOLD["balanced"])
        edge_keep = _EDGE_KEEP.get(mode, _EDGE_KEEP["balanced"])

        lines = text.splitlines()
        out: list[str] = []
        run: list[str] = []
        in_hunk = False

        def flush_run() -> None:
            nonlocal run
            if not run:
                return
            if len(run) > threshold:
                head = run[:edge_keep]
                tail = run[-edge_keep:] if edge_keep else []
                omitted = len(run) - len(head) - len(tail)
                out.extend(head)
                out.append(f"... [{omitted} unchanged lines omitted] ...")
                out.extend(tail)
            else:
                out.extend(run)
            run = []

        for line in lines:
            if _HUNK_HEADER.match(line):
                flush_run()
                in_hunk = True
                out.append(line)
            elif in_hunk and _FILE_HEADER.match(line):
                flush_run()
                in_hunk = False
                out.append(line)
            elif in_hunk and (line.startswith("+") or line.startswith("-")):
                flush_run()
                out.append(line)
            elif in_hunk and line.startswith(" "):
                run.append(line)
            else:
                flush_run()
                out.append(line)

        flush_run()
        return "\n".join(out)
