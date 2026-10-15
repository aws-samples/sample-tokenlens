"""Property-based test for Always-On Savings Property 8.

Covers the retained-window and short-conversation guarantees of the
``ConversationHistoryTrimmer``: the most recent ``retained_window`` turns are
always preserved verbatim in their original positions, and a conversation whose
length is at or below the window is returned entirely unchanged.
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.history import ConversationHistoryTrimmer

_TEXT = string.ascii_letters + string.digits + " .,"


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 40):
    """Generate 0-40 turns with alternating user/assistant roles."""
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages


windows = st.integers(min_value=1, max_value=20)


class TestRetainedWindowProperty:
    # Feature: always-on-savings, Property 8: For any messages list and retained window size, every turn among the most recent retained_window turns appears unmodified in its original position; when the list length is at or below the window, the entire conversation is returned unchanged.
    @settings(max_examples=100)
    @given(messages=message_lists(), window=windows)
    def test_retained_window_and_short_conversations(self, messages, window):
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)

        # (a) When the list length is at or below the window, the entire
        # conversation is returned unchanged (Requirement 2.3).
        if len(original) <= window:
            assert result.applied is False
            assert result.messages == original
            return

        # (b) For longer lists, the most recent `window` turns are unmodified
        # in their original positions (Requirement 2.2). This holds whether or
        # not trimming was applied, because the non-profitable path returns the
        # original list unchanged and the profitable path copies the recent
        # turns through verbatim.
        assert result.messages[-window:] == original[-window:]
