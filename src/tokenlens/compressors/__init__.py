"""Base types for content compressors.

A compressor takes a block of text and returns a (possibly) shorter form that
preserves the load-bearing information for an LLM. Compressors are rule-based
and deterministic in this MVP: no model calls, no network, no external state.

Design rules shared by all compressors:
- Never fabricate content. Only remove or summarize existing content.
- Preserve verbatim: file paths, error messages, identifiers, numbers.
- Be safe on unexpected input: if unsure, return the text unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tokens import count_tokens


@dataclass
class CompressionResult:
    """Outcome of a single compressor run."""

    text: str
    original_tokens: int
    compressed_tokens: int
    applied: bool
    compressor: str

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


class Compressor:
    """Base class for all content compressors."""

    name: str = "base"

    def matches(self, text: str) -> bool:
        """Return True if this compressor is appropriate for the given text."""
        raise NotImplementedError

    def _compress(self, text: str, mode: str) -> str:
        """Return the compressed form of the text. Subclasses implement this."""
        raise NotImplementedError

    def compress(self, text: str, mode: str = "balanced") -> CompressionResult:
        """Compress text, guarding against non-profitable or unsafe results.

        The compressed output is only accepted if it is actually shorter than
        the input. Otherwise the original text is returned unchanged so a
        compressor can never make a block larger.
        """
        original_tokens = count_tokens(text)
        try:
            candidate = self._compress(text, mode)
        except Exception:
            # Fail safe: never let a compressor error affect the request.
            candidate = text

        candidate_tokens = count_tokens(candidate)
        if candidate_tokens < original_tokens:
            return CompressionResult(
                text=candidate,
                original_tokens=original_tokens,
                compressed_tokens=candidate_tokens,
                applied=True,
                compressor=self.name,
            )
        return CompressionResult(
            text=text,
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            applied=False,
            compressor=self.name,
        )
