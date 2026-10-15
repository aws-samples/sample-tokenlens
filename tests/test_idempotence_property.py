"""Property-based test for the always-on-savings idempotence guarantee.

Covers design Property 5 for the always-on-savings feature: every always-on
lever is idempotent. For any input content, applying a lever to content it has
already optimized returns that content unchanged (``applied`` false), so
compressing or trimming twice yields the same result as once.

The property is exercised across all three levers:

- the Context File Compressor behind ``compress_context_file`` (Lever 1),
- the compressed read path behind ``compress_text`` (Lever 3),
- the ``ConversationHistoryTrimmer`` (Lever 2).

For the two content-producing levers "already optimized" content is the ``text``
returned by a first pass; a second pass must return that same text with
``applied`` false. For the trimmer, an already-trimmed ``messages`` list must be
returned unchanged with ``applied`` false on a second trim.
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_context_file, compress_text
from tokenlens.history import ConversationHistoryTrimmer

# A mixed alphabet that produces both compressible content (repeated lines,
# runs of blank lines) and incompressible free-form prose, so both the
# reducing (first pass shrinks) and non-reducing (already-optimized) branches
# of each lever are exercised.
_TEXT = string.ascii_letters + string.digits + " \n\t.,:;!?(){}[]\"'-_/`"


@st.composite
def _content(draw) -> str:
    """Free-form documents, some of which compress and some of which do not."""
    return draw(st.text(alphabet=_TEXT, min_size=0, max_size=4000))


@st.composite
def _repetitive_content(draw) -> str:
    """Documents with repeated and blank lines, which reliably compress once."""
    line = draw(st.text(alphabet=string.ascii_letters + " .,", min_size=1, max_size=40))
    repeats = draw(st.integers(min_value=2, max_value=20))
    blanks = draw(st.integers(min_value=1, max_value=10))
    return ((("\n" * blanks) + (line + "\n") * repeats) * draw(
        st.integers(min_value=1, max_value=4)
    ))


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


class TestIdempotenceProperty:
    # Feature: always-on-savings, Property 5: For any input content, applying a lever to content it has already optimized returns that content unchanged (applied false); compressing or trimming twice yields the same result as once.
    # **Validates: Requirements 5.2**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_context_file_compressor_idempotent(self, text):
        first = compress_context_file(text)["text"]

        second_result = compress_context_file(first)
        # Applying the compressor to its own output changes nothing.
        assert second_result["text"] == first, (
            "context-file compression is not idempotent: second pass differs"
        )
        # The second pass achieves no further reduction, so applied is false.
        assert second_result["applied"] is False, (
            "second context-file compression reports applied True (further reduction)"
        )

        # Compressing twice yields the same result as compressing once.
        assert first == compress_context_file(text)["text"]

    # Feature: always-on-savings, Property 5: For any input content, applying a lever to content it has already optimized returns that content unchanged (applied false); compressing or trimming twice yields the same result as once.
    # **Validates: Requirements 5.2**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_compressed_read_path_idempotent(self, text):
        first = compress_text(text)["text"]

        second_result = compress_text(first)
        # Applying the read-path compressor to its own output changes nothing.
        assert second_result["text"] == first, (
            "compressed read path is not idempotent: second pass differs"
        )
        # The second pass achieves no further reduction, so applied is false.
        assert second_result["applied"] is False, (
            "second read-path compression reports applied True (further reduction)"
        )

    # Feature: always-on-savings, Property 5: For any input content, applying a lever to content it has already optimized returns that content unchanged (applied false); compressing or trimming twice yields the same result as once.
    # **Validates: Requirements 5.2**
    @settings(max_examples=100, deadline=None)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_idempotent(self, messages, window):
        trimmer = ConversationHistoryTrimmer(window)

        first = trimmer.trim(copy.deepcopy(messages))
        once = first.messages

        # Trim the already-trimmed conversation a second time.
        second = trimmer.trim(copy.deepcopy(once))

        # Trimming twice yields the same messages as trimming once.
        assert second.messages == once, (
            "history trimming is not idempotent: second trim differs from first"
        )
        # The already-trimmed conversation yields no further reduction, so the
        # second application reports applied false.
        assert second.applied is False, (
            "second history trim reports applied True (further reduction)"
        )
