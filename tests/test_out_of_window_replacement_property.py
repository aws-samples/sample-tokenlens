"""Property-based test for always-on-savings Property 9.

Out-of-window turns are replaced by a role-preserving placeholder without any
turn being removed or reordered. The `history.py` trimmer applies a
profitability gate: when replacing older turns with placeholders would not
reduce the Token Count (out-of-window turns are empty or tiny), `trim` returns
the original messages unchanged with `applied=False`. Placeholder replacement
is therefore guaranteed only on the profitable path (`result.applied is True`).
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.history import ConversationHistoryTrimmer

_TEXT = string.ascii_letters + string.digits + " .,"

windows = st.integers(min_value=1, max_value=20)


@st.composite
def long_message_lists(draw):
    """Messages lists guaranteed longer than the paired retained window.

    A ``sizable`` flag drives whether out-of-window turns carry enough content
    to make trimming profitable. When ``sizable`` is True the older turns get
    clearly-worth-trimming content so the profitable branch (applied=True) is
    reliably exercised; when False they may be empty or tiny so the
    profitability gate (applied=False) is also exercised.
    """
    window = draw(windows)
    extra = draw(st.integers(min_value=1, max_value=20))
    sizable = draw(st.booleans())
    n = window + extra
    cutoff = n - window

    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        if sizable and i < cutoff:
            # Sizable out-of-window content so placeholders reduce tokens and
            # the profitable branch (applied=True) is taken.
            content = draw(
                st.text(alphabet=_TEXT, min_size=60, max_size=200)
            ) + " padding content that is clearly worth trimming here"
        else:
            content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages, window


class TestOutOfWindowReplacement:
    # Feature: always-on-savings, Property 9: For any messages list longer than the retained window, every turn older than the window has its content replaced by a role-preserving placeholder while its role and position are unchanged, and no turn is removed or reordered.
    @settings(max_examples=100)
    @given(data=long_message_lists())
    def test_out_of_window_replaced_without_removal_or_reorder(self, data):
        messages, window = data
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)

        cutoff = len(original) - window
        assert cutoff >= 1  # generator guarantees the list exceeds the window

        # Profitability gate: when replacing out-of-window turns with
        # placeholders does not reduce tokens (older turns empty/tiny), the
        # trimmer returns the conversation unchanged (Requirement 2.5).
        if not result.applied:
            assert result.messages == original
            return

        # No turn removed or reordered: length and role sequence preserved.
        assert len(result.messages) == len(original)
        assert [m["role"] for m in result.messages] == [
            m["role"] for m in original
        ]

        # Every out-of-window turn: role and position unchanged, content
        # replaced by a role-preserving placeholder.
        for i in range(cutoff):
            assert result.messages[i]["role"] == original[i]["role"]
            assert result.messages[i]["content"] != original[i]["content"]
            assert result.messages[i]["content"].startswith("[Earlier ")

        # Retained window turns are untouched in their original positions.
        assert result.messages[cutoff:] == original[cutoff:]
