"""Conversation history trimming.

In a long multi-turn session, the full ``messages`` list is resent on every
request even though older turns contribute diminishing value. The
Conversation History Trimmer keeps the most recent turns verbatim and
replaces the content of older turns with a short placeholder, preserving each
turn's ``role`` and its position in the list. Trimming is a pure function of
the message list and the retained-window size: no network, no model, no
external state.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tokens import _count_content_tokens


@dataclass(frozen=True)
class HistoryTrimResult:
    """Outcome of one trim operation."""

    messages: list
    tokens_saved: int
    applied: bool


class ConversationHistoryTrimmer:
    """Replace older turns with short placeholders, keep recent turns verbatim."""

    def __init__(self, retained_window: int):
        self._retained_window = retained_window

    def trim(self, messages: list) -> HistoryTrimResult:
        """Trim older turns from a messages list.

        The most recent ``retained_window`` turns are preserved unmodified in
        their original positions. Every earlier turn's ``content`` is replaced
        with a short placeholder that preserves the turn's ``role``. Turns are
        never removed or reordered. Malformed input (non-list messages, or a
        list containing a non-dict turn) is returned unchanged rather than
        raising.
        """
        if not isinstance(messages, list) or not all(
            isinstance(turn, dict) for turn in messages
        ):
            return HistoryTrimResult(messages=messages, tokens_saved=0, applied=False)

        if len(messages) <= self._retained_window:
            return HistoryTrimResult(messages=messages, tokens_saved=0, applied=False)

        try:
            cutoff = len(messages) - self._retained_window
            original_tokens = 0
            trimmed_tokens = 0
            new_messages: list = []

            for index, turn in enumerate(messages):
                content = turn.get("content")
                if index < cutoff:
                    role = turn.get("role", "unknown")
                    turn_tokens = _count_content_tokens(content)
                    placeholder = f"[Earlier {role} turn omitted: ~{turn_tokens} tokens]"
                    new_turn = dict(turn)
                    new_turn["content"] = placeholder
                    new_messages.append(new_turn)
                    original_tokens += turn_tokens
                    trimmed_tokens += _count_content_tokens(placeholder)
                else:
                    new_messages.append(turn)

            tokens_saved = max(0, original_tokens - trimmed_tokens)
        except Exception:
            # Fail open (Requirement 6.1; design Property 6): if any internal
            # step (e.g. the per-turn token count) raises, the core function
            # returns the original conversation unchanged rather than
            # propagating the error.
            return HistoryTrimResult(messages=messages, tokens_saved=0, applied=False)

        if tokens_saved <= 0:
            # Profitability gate: when replacing older turns with placeholders
            # does not reduce the Token Count (e.g. the trimmed turns were
            # empty or tiny, so placeholders are as large or larger), return
            # the original conversation unchanged rather than the enlarged
            # placeholder list (Requirement 2.5; design Properties 2 and 5).
            return HistoryTrimResult(messages=messages, tokens_saved=0, applied=False)

        return HistoryTrimResult(
            messages=new_messages,
            tokens_saved=tokens_saved,
            applied=True,
        )
