"""Property-based test for the always-on-savings function-level fail-open guarantee.

Covers design Property 6 for the always-on-savings feature: every lever's core
function fails open. For any input content, a lever's core function does not
raise; if its internal optimization step errors, the function returns the
original input content unchanged (with ``applied`` false).

The property is exercised across all three levers' core functions:

- the Context File Compressor behind ``compress_context_file`` (Lever 1), whose
  internal step is ``ContextFileCompressor.compress``,
- the compressed read path behind ``compress_text`` (Lever 3), whose internal
  step is ``compress_block``,
- ``ConversationHistoryTrimmer.trim`` (Lever 2), whose internal step is the
  per-turn token count ``_count_content_tokens``.

Two things are asserted for each core function:

(a) for arbitrary inputs the core function never raises; and
(b) when its internal optimization step is patched to raise, the function
    returns the original input content unchanged with ``applied`` false.
"""

import copy
import string
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens import api as api_module
from tokenlens import history as history_module
from tokenlens.api import compress_context_file, compress_text
from tokenlens.history import ConversationHistoryTrimmer

# A mixed alphabet that produces both compressible content (repeated lines,
# runs of blank lines) and incompressible free-form prose, plus load-bearing
# spans, so the core functions are exercised on content that both does and
# does not compress.
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
    return ((("\n" * blanks) + (line + "\n") * repeats)) * draw(
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


def _boom(*_args, **_kwargs):
    """Stand-in for an internal optimization step that errors."""
    raise RuntimeError("internal optimization step exploded")


class TestFailOpenProperty:
    # ---- Lever 1: Context File Compressor (compress_context_file) ----------

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_context_file_compressor_does_not_raise(self, text):
        # (a) The core function must never raise for arbitrary input.
        result = compress_context_file(text)
        assert isinstance(result, dict)

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_context_file_compressor_fails_open_on_internal_error(self, text):
        # (b) When the internal optimization step raises, the function returns
        # the original input content unchanged with applied false.
        with patch.object(
            api_module.ContextFileCompressor, "compress", _boom
        ):
            result = compress_context_file(text)
        assert result["applied"] is False
        assert result["text"] == text

    # ---- Lever 3: Compressed read path (compress_text) ---------------------

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_compressed_read_path_does_not_raise(self, text):
        # (a) The core function must never raise for arbitrary input.
        result = compress_text(text)
        assert isinstance(result, dict)

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(text=content_strategy)
    def test_compressed_read_path_fails_open_on_internal_error(self, text):
        # (b) When the internal optimization step raises, the function returns
        # the original input content unchanged with applied false.
        with patch.object(api_module, "compress_block", _boom):
            result = compress_text(text)
        assert result["applied"] is False
        assert result["text"] == text

    # ---- Lever 2: History Trimmer (ConversationHistoryTrimmer.trim) --------

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_does_not_raise(self, messages, window):
        # (a) The core function must never raise for arbitrary input.
        result = ConversationHistoryTrimmer(window).trim(messages)
        assert result.messages is not None or messages == []

    # Feature: always-on-savings, Property 6: For any input content, a lever's core function does not raise; if its internal optimization step errors, the function returns the original input content unchanged.
    # **Validates: Requirements 1.8, 3.9, 6.1**
    @settings(max_examples=100, deadline=None)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_fails_open_on_internal_error(self, messages, window):
        # (b) When the internal optimization step raises, the function returns
        # the original input content unchanged with applied false.
        original = copy.deepcopy(messages)
        with patch.object(history_module, "_count_content_tokens", _boom):
            result = ConversationHistoryTrimmer(window).trim(messages)
        assert result.applied is False
        assert result.messages == original
