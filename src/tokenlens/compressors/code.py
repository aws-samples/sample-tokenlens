"""Source code compressor.

When a large source file is included as context but only its structure matters
(not every line of every function body), this compressor keeps the "skeleton":
imports, class and function signatures, decorators, and comments, while
collapsing long function bodies into a placeholder. Recent/edited files should
be sent verbatim; this is intended for background context files.

This MVP uses lightweight, language-agnostic heuristics for Python-like and
brace-based languages. It is intentionally conservative: if indentation or
structure is ambiguous, lines are kept.
"""

from __future__ import annotations

import re

from . import Compressor

# Signature / structural lines we always keep (Python, JS/TS, Java-ish).
_STRUCTURAL = re.compile(
    r"^\s*(def |class |async def |@|import |from |export |function |"
    r"public |private |protected |static |const |let |var |interface |"
    r"type |struct |enum |func |package |module\.exports|#!|//|#|/\*|\*)",
    re.MULTILINE,
)

# A line that opens a block body in brace languages.
_OPENS_BRACE = re.compile(r"\{\s*$")

# Minimum number of lines before code compression is worth attempting.
_MIN_LINES = 40
# Only collapse function bodies longer than this many lines.
_MIN_BODY_LINES = 6


class CodeCompressor(Compressor):
    name = "code"

    def matches(self, text: str) -> bool:
        if text.count("\n") < _MIN_LINES:
            return False
        # Require several structural markers to avoid mangling prose.
        markers = len(_STRUCTURAL.findall(text))
        return markers >= 3

    def _compress(self, text: str, mode: str) -> str:
        lines = text.splitlines()
        result: list[str] = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]
            result.append(line)

            if self._is_body_start(line):
                body_indent = self._indent(line)
                body_lines: list[str] = []
                j = i + 1
                while j < n:
                    nxt = lines[j]
                    if nxt.strip() == "":
                        body_lines.append(nxt)
                        j += 1
                        continue
                    if self._indent(nxt) <= body_indent:
                        break
                    body_lines.append(nxt)
                    j += 1

                non_blank = [b for b in body_lines if b.strip()]
                if len(non_blank) > _MIN_BODY_LINES:
                    pad = " " * (body_indent + 4)
                    result.append(f"{pad}... [body of {len(non_blank)} lines omitted] ...")
                else:
                    result.extend(body_lines)
                i = j
                continue
            i += 1

        return "\n".join(result)

    @staticmethod
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _is_body_start(self, line: str) -> bool:
        # Only collapse function/method bodies. Class bodies are left intact so
        # their nested method signatures are preserved (and their own bodies
        # collapsed on later iterations).
        stripped = line.strip()
        if stripped.startswith(("def ", "async def ")) and stripped.endswith(":"):
            return True
        if _OPENS_BRACE.search(line) and _STRUCTURAL.match(line):
            # Exclude class/interface/struct/enum declarations in brace languages.
            if re.match(r"^\s*(class |interface |struct |enum )", line):
                return False
            return True
        return False
