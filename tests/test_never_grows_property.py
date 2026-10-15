"""Property-based test for the always-on-savings never-grows guarantee.

Covers design Property 2 for the always-on-savings feature: no always-on lever
ever grows its input. For any input content, a lever's optimization produces
output whose Token Count is less than or equal to the input's Token Count, and
returns the original input unchanged (with ``applied`` false) whenever no
reduction is achieved.

The property is exercised across all three levers:

- the Context File Compressor behind ``compress_context_file`` (Lever 1),
- the compressed read path behind ``compress_text`` (Lever 3),
- the ``ConversationHistoryTrimmer`` (Lever 2).

For the two content-producing levers "input" is the text and its Token Count is
``count_tokens``; for the trimmer "input" is the ``messages`` list and its
Token Count is ``count_message_tokens``.
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_context_file, compress_text
from tokenlens.history import ConversationHistoryTrimmer
from tokenlens.tokens import count_message_tokens, count_tokens

# A mixed alphabet that produces both compressible content (repeated lines,
# runs of blank lines, fenced blocks) and incompressible free-form prose, so
# both the reducing and non-reducing branches of each lever are exercised.
_TEXT = string.ascii_letters + string.digits + " \n\t.,:;!?(){}[]\"'-_/`"


@st.composite
def _content(draw) -> str:
    """Free-form documents, some of which compress and some of which do not."""
    return draw(st.text(alphabet=_TEXT, min_size=0, max_size=4000))


@st.composite
def _repetitive_content(draw) -> str:
    """Documents with repeated and blank lines, which reliably compress."""
    line = draw(st.text(alphabet=string.ascii_letters + " .,", min_size=1, max_size=40))
    repeats = draw(st.integers(min_value=2, max_value=20))
    blanks = draw(st.integers(min_value=1, max_value=10))
    return (("\n" * blanks) + (line + "\n") * repeats) * draw(
        st.integers(min_value=1, max_value=4)
    )


content_strategy = st.one_of(_content(), _repetitive_content())


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 40):
    """0-40 alternating user/assistant turns with varied content sizes."""
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=200))
        messages.append({"role": role, "content": content})
    return messages


windows = st.integers(min_value=1, max_value=20)


def _assert_never_grows(input_text: str, result: dict) -> None:
    """Shared assertions for a content-producing lever's result dict."""
    original_tokens = count_tokens(input_text)
    output_tokens = count_tokens(result["text"])

    # Never grows: output Token Count <= input Token Count.
    assert output_tokens <= original_tokens, (
        f"lever grew input: {original_tokens} -> {output_tokens} tokens"
    )

    # When no reduction is achieved, the original input is returned unchanged
    # with applied false.
    if not result["applied"]:
        assert result["text"] == input_text, (
            "applied is False but output content differs from input"
        )


class TestNeverGrowsProperty:
    # Feature: always-on-savings, Property 2: For any input content, a lever's optimization produces output whose Token Count is less than or equal to the input's Token Count, and returns the original input unchanged (with applied false) whenever no reduction is achieved.
    # **Validates: Requirements 1.4, 2.5, 3.7, 5.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_context_file_compressor_never_grows(self, text):
        _assert_never_grows(text, compress_context_file(text))

    # Feature: always-on-savings, Property 2: For any input content, a lever's optimization produces output whose Token Count is less than or equal to the input's Token Count, and returns the original input unchanged (with applied false) whenever no reduction is achieved.
    # **Validates: Requirements 1.4, 2.5, 3.7, 5.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_compressed_read_path_never_grows(self, text):
        _assert_never_grows(text, compress_text(text))

    # Feature: always-on-savings, Property 2: For any input content, a lever's optimization produces output whose Token Count is less than or equal to the input's Token Count, and returns the original input unchanged (with applied false) whenever no reduction is achieved.
    # **Validates: Requirements 1.4, 2.5, 3.7, 5.1**
    @settings(max_examples=100, deadline=None)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_never_grows(self, messages, window):
        original = copy.deepcopy(messages)
        original_tokens = count_message_tokens(original)

        result = ConversationHistoryTrimmer(window).trim(messages)
        output_tokens = count_message_tokens(result.messages)

        # Never grows: the trimmed conversation's Token Count <= the original's.
        assert output_tokens <= original_tokens, (
            f"trimmer grew conversation: {original_tokens} -> {output_tokens} tokens"
        )

        # When no reduction is achieved, the original conversation is returned
        # unchanged with applied false.
        if not result.applied:
            assert result.messages == original, (
                "applied is False but trimmed messages differ from input"
            )
