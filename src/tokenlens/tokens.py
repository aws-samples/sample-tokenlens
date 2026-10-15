"""Token counting utilities.

Provides a best-effort token count for text. Uses tiktoken when available
(a close proxy for most tokenizers) and falls back to a character-based
heuristic otherwise. Counts are used for measuring optimization savings, not
for billing, so an approximation is acceptable.
"""

from __future__ import annotations

from functools import lru_cache

# Average characters per token observed on mixed code/prose LLM traffic.
# Used only by the heuristic fallback when tiktoken is unavailable.
_CHARS_PER_TOKEN = 3.5


@lru_cache(maxsize=1)
def _load_encoder():
    """Load a tiktoken encoder once, or return None if unavailable."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    """Return an estimated token count for a string of text."""
    if not text:
        return 0
    encoder = _load_encoder()
    if encoder is not None:
        return len(encoder.encode(text, disallowed_special=()))
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


def count_message_tokens(messages: list[dict]) -> int:
    """Estimate tokens for a list of Anthropic-style message dicts.

    Each message has a ``role`` and ``content`` that is either a string or a
    list of content blocks. Only textual content contributes to the estimate;
    non-text blocks (for example images) are ignored here because their token
    cost is computed differently by the provider.
    """
    total = 0
    for message in messages:
        content = message.get("content", "")
        total += _count_content_tokens(content)
    return total


def _count_content_tokens(content) -> int:
    if isinstance(content, str):
        return count_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                total += count_tokens(block.get("text", ""))
            elif isinstance(block, str):
                total += count_tokens(block)
        return total
    return 0
