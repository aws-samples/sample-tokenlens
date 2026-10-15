"""Response post-processing (pipeline Stage 6).

Removes a bounded, well-defined set of conversational filler (leading
preambles and closing offers) from a model's response text, while preserving
all load-bearing content — especially fenced code blocks and inline code —
exactly. The transformation is local, deterministic, idempotent, and
profitability-gated: it can never grow its input, and applying it twice yields
the same result as applying it once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tokens import count_tokens

# Leading preambles, matched only at the very start of the text.
_PREAMBLE = [
    "sure thing",
    "sure",
    "certainly",
    "of course",
    "great question",
    "i'd be happy to help",
    "happy to help",
    "absolutely",
    "let me help you with that",
    "here you go",
    "here's what you asked for",
]

# Closing offers, matched only as a trailing line/sentence at the very end.
_CLOSING = [
    "let me know if you need anything else",
    "let me know if you have any questions",
    "let me know if you'd like me to explain further",
    "hope this helps",
    "i hope that helps",
    "feel free to ask",
]

# A fenced code block: an opening fence (3+ backticks or tildes) through the
# next line that starts with a matching fence. DOTALL so it spans newlines.
_FENCED = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[ \t]*$|\Z)")

# An inline code span: backtick-delimited run on a single line.
_INLINE = re.compile(r"`[^`\n]+`")

_PLACEHOLDER = "\x00TLPROT{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00TLPROT(\d+)\x00")

# Preamble anchored at start: phrase, optional punctuation, then whitespace or
# end. Case-insensitive.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:%s)\s*[!.,:]*\s*"
    % "|".join(re.escape(p) for p in _PREAMBLE),
    re.IGNORECASE,
)

# Closing anchored at end: optional whitespace, phrase, optional punctuation,
# then end of text. Case-insensitive.
_CLOSING_RE = re.compile(
    r"\s*(?:%s)\s*[!.,]*\s*$"
    % "|".join(re.escape(c) for c in _CLOSING),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of one response post-processing run."""

    text: str
    original_tokens: int
    processed_tokens: int
    applied: bool

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.processed_tokens)


class ResponsePostProcessor:
    """Remove conversational filler while preserving protected code spans."""

    def process(self, text: str) -> ProcessResult:
        original_tokens = count_tokens(text) if isinstance(text, str) else 0
        if not isinstance(text, str) or not text:
            return ProcessResult(
                text=text,
                original_tokens=original_tokens,
                processed_tokens=original_tokens,
                applied=False,
            )

        masked, spans = self._mask(text)
        stripped = self._strip_filler(masked)
        candidate = self._unmask(stripped, spans)

        candidate_tokens = count_tokens(candidate)
        if candidate_tokens < original_tokens:
            return ProcessResult(
                text=candidate,
                original_tokens=original_tokens,
                processed_tokens=candidate_tokens,
                applied=True,
            )
        return ProcessResult(
            text=text,
            original_tokens=original_tokens,
            processed_tokens=original_tokens,
            applied=False,
        )

    # ---- Protected-span masking ------------------------------------------

    def _mask(self, text: str) -> tuple[str, list[str]]:
        spans: list[str] = []

        def take(match: re.Match) -> str:
            spans.append(match.group(0))
            return _PLACEHOLDER.format(len(spans) - 1)

        masked = _FENCED.sub(take, text)
        masked = _INLINE.sub(take, masked)
        return masked, spans

    def _unmask(self, text: str, spans: list[str]) -> str:
        def restore(match: re.Match) -> str:
            return spans[int(match.group(1))]

        return _PLACEHOLDER_RE.sub(restore, text)

    # ---- Filler stripping -------------------------------------------------

    def _strip_filler(self, text: str) -> str:
        # Strip repeatedly: a response may stack multiple filler phrases
        # ("Sure, I'd be happy to help!"). Looping until stable also makes the
        # transformation idempotent — a second process() finds nothing left to
        # remove at the anchors.
        while True:
            stripped = _PREAMBLE_RE.sub("", text, count=1)
            stripped = _CLOSING_RE.sub("", stripped, count=1)
            if stripped == text:
                return stripped
            text = stripped
