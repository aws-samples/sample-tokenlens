"""Context-file compression.

Compresses persistent context files (steering files, ``AGENTS.md``,
``CLAUDE.md``, project instruction docs) that an agent loads every session, so
each session starts with fewer input tokens. The transformation is
conservative and deterministic: it collapses redundant whitespace and repeated
lines and strips leading conversational filler, while preserving all
load-bearing content verbatim — fenced code blocks, inline code, file paths,
URLs, and long identifiers. It never paraphrases with a model, so meaning is
never altered. Compression is profitability-gated (never grows the file) and
idempotent (re-compressing changes nothing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tokens import count_tokens

# Fenced code block: opening fence (3+ backticks/tildes) through the matching
# closing fence or end of text.
_FENCED = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[ \t]*$|\Z)")
# Inline code span on a single line.
_INLINE = re.compile(r"`[^`\n]+`")
# URLs.
_URL = re.compile(r"https?://[^\s)]+")
# File paths: a slash-separated run with a segment containing a dot or a
# leading ./ or / — e.g. src/tokenlens/api.py, /etc/app/config.yaml, ./x/y.
_PATH = re.compile(r"(?:\.?/)?(?:[\w.\-]+/)+[\w.\-]+")
# Long identifiers / hashes: alphanumeric tokens of length >= 12 containing a
# digit (heuristic for hashes, ARNs segments, IDs).
_IDENT = re.compile(r"\b(?=[\w-]*\d)[A-Za-z0-9][\w-]{11,}\b")

_PLACEHOLDER = "\x00TLCTX{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00TLCTX(\d+)\x00")

# Leading conversational filler phrases (mirrors postprocess preambles).
_PREAMBLE = [
    "sure thing",
    "sure",
    "certainly",
    "of course",
    "great question",
    "i'd be happy to help",
    "happy to help",
    "absolutely",
    "here you go",
]
_PREAMBLE_RE = re.compile(
    r"^\s*(?:%s)\s*[!.,:]*\s*" % "|".join(re.escape(p) for p in _PREAMBLE),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContextCompressionResult:
    """Outcome of one context-file compression."""

    text: str
    original_tokens: int
    compressed_tokens: int
    applied: bool

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


class ContextFileCompressor:
    """Compress a persistent context file while preserving load-bearing content."""

    # Order matters: fenced first (multi-line), then URLs/paths/identifiers,
    # then inline code, so longer/structural spans are masked before shorter.
    _MASKERS = (_FENCED, _URL, _PATH, _IDENT, _INLINE)

    def compress(self, text: str) -> ContextCompressionResult:
        original_tokens = count_tokens(text) if isinstance(text, str) else 0
        if not isinstance(text, str) or not text:
            return ContextCompressionResult(
                text=text,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                applied=False,
            )

        masked, spans = self._mask(text)
        shrunk = self._compress_prose(masked)
        candidate = self._unmask(shrunk, spans)

        candidate_tokens = count_tokens(candidate)
        if candidate_tokens < original_tokens:
            return ContextCompressionResult(
                text=candidate,
                original_tokens=original_tokens,
                compressed_tokens=candidate_tokens,
                applied=True,
            )
        return ContextCompressionResult(
            text=text,
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            applied=False,
        )

    # ---- masking ----------------------------------------------------------

    def _mask(self, text: str) -> tuple[str, list[str]]:
        spans: list[str] = []

        def take(match: re.Match) -> str:
            spans.append(match.group(0))
            return _PLACEHOLDER.format(len(spans) - 1)

        for pattern in self._MASKERS:
            text = pattern.sub(take, text)
        return text, spans

    def _unmask(self, text: str, spans: list[str]) -> str:
        def restore(match: re.Match) -> str:
            return spans[int(match.group(1))]

        # Loop until stable in case a restored span somehow contains another
        # placeholder (it cannot, but this is defensive and terminates).
        for _ in range(3):
            new = _PLACEHOLDER_RE.sub(restore, text)
            if new == text:
                break
            text = new
        return text

    # ---- prose compression ------------------------------------------------

    def _strip_preamble(self, text: str) -> str:
        """Remove every leading filler phrase, reaching a fixed point in one pass.

        The pattern is anchored at the start of the text, so a single
        substitution peels one phrase and can expose the next one. Applying it
        once per compression made compression non-idempotent: "GREAT QUESTION\\n
        GREAT QUESTION" collapsed to one line on the first pass and to an empty
        file on the second. Looping here keeps `compress` idempotent, and text
        that is entirely filler is left alone rather than deleted.
        """
        while True:
            candidate = _PREAMBLE_RE.sub("", text, count=1)
            if candidate == text:
                return text
            if not candidate.strip():
                return text
            text = candidate

    def _compress_prose(self, text: str) -> str:
        text = self._strip_preamble(text)

        lines = text.split("\n")
        out: list[str] = []
        blank_run = False
        prev_nonblank: str | None = None

        for line in lines:
            stripped = line.rstrip()
            if stripped == "":
                if blank_run:
                    continue  # collapse consecutive blank lines
                blank_run = True
                out.append("")
                continue
            blank_run = False
            if stripped == prev_nonblank:
                continue  # drop consecutive duplicate content lines
            prev_nonblank = stripped
            out.append(stripped)

        # Trim leading/trailing blank lines.
        while out and out[0] == "":
            out.pop(0)
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out)
