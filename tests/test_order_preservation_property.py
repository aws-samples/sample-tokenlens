"""Property-based test for the always-on-savings order-preservation guarantee.

Covers design Property 4 for the always-on-savings feature: for any input, the
relative order of all content that a lever does not remove is the same in the
output as in the input.

Two lever families are exercised:

* The **History Trimmer** must preserve turn order: it never removes or
  reorders turns, so the sequence of roles (and the list length) is identical
  in the output.
* The **content-producing compressors** (the Context File Compressor behind
  ``compress_context_file`` and ``compress_text`` behind the compressed read
  path) must preserve the relative order of retained content: any load-bearing
  span or non-blank line that survives appears in the same relative order in
  the output as it did in the input.
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_context_file, compress_text
from tokenlens.history import ConversationHistoryTrimmer

# Content alphabet: prose plus the structural characters used by fenced code,
# inline code, paths, URLs, and identifiers, so generated documents both do and
# do not compress.
_TEXT = string.ascii_letters + string.digits + " \n\t.,:;!?{}[]()\"'-_/`#"


@st.composite
def _unique_span(draw, index: int) -> str:
    """A load-bearing span made unique by ``index`` so it can be located.

    Each span embeds ``markerNNN`` where ``NNN`` is the span's original
    position, letting the test recover the relative order of surviving spans in
    the compressor output.
    """
    word = draw(st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=8))
    marker = f"marker{index:03d}"
    kind = draw(st.integers(min_value=0, max_value=4))
    if kind == 0:
        return f"```\ncode {word} = {marker}\n```"
    if kind == 1:
        return f"`{word}_{marker}`"
    if kind == 2:
        return f"src/tokenlens/{word}_{marker}.py"
    if kind == 3:
        return f"https://example.com/{word}/{marker}"
    return f"{word}{marker}deadbeefcafe"


@st.composite
def documents_with_ordered_spans(draw):
    """Prose interleaved with uniquely-marked load-bearing spans.

    Returns ``(text, markers)`` where ``markers`` is the list of span markers in
    the order they appear in ``text``. Blank and duplicate prose lines are
    included so the compressor has something to collapse, exercising the
    compressed branch as well as the unchanged branch. Every span is
    load-bearing, so every marker is preserved verbatim by the compressors; the
    test then checks their relative order is unchanged.
    """
    prose = draw(st.lists(st.text(alphabet=_TEXT, min_size=0, max_size=60), max_size=8))
    span_count = draw(st.integers(min_value=0, max_value=6))
    spans = [draw(_unique_span(i)) for i in range(span_count)]

    chunks: list[str] = []
    markers: list[str] = []
    for i in range(max(len(prose), len(spans))):
        if i < len(prose):
            part = prose[i]
            chunks.append(part)
            chunks.append("")  # blank line (collapsible)
            chunks.append(part)  # duplicate line (droppable)
        if i < len(spans):
            chunks.append(spans[i])
            markers.append(f"marker{i:03d}")
    return "\n".join(chunks), markers


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 40):
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages


windows = st.integers(min_value=1, max_value=20)


def _surviving_markers(all_markers: list[str], output: str) -> list[str]:
    """Markers that appear in ``output``, in the order they appear there."""
    positions = []
    for marker in all_markers:
        idx = output.find(marker)
        if idx != -1:
            positions.append((idx, marker))
    positions.sort()
    return [marker for _idx, marker in positions]


class TestOrderPreservationProperty:
    # Feature: always-on-savings, Property 4: For any input, the relative order of all content that a lever does not remove is the same in the output as in the input.
    # **Validates: Requirements 4.3**

    @settings(max_examples=100)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_preserves_turn_order(self, messages, window):
        # The trimmer never removes or reorders turns: it keeps the list length
        # and the exact sequence of roles, whether or not trimming applied.
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)

        assert len(result.messages) == len(original)
        assert [m["role"] for m in result.messages] == [
            m["role"] for m in original
        ]

    @settings(max_examples=100)
    @given(doc=documents_with_ordered_spans())
    def test_context_file_compressor_preserves_span_order(self, doc):
        text, markers = doc
        result = compress_context_file(text)

        # Every load-bearing span is preserved verbatim, so all markers survive;
        # the relative order of the markers that survive must be unchanged.
        input_order = _surviving_markers(markers, text)
        output_order = _surviving_markers(markers, result["text"])
        assert output_order == input_order, (
            "Context File Compressor changed the relative order of retained "
            f"spans: input {input_order!r} vs output {output_order!r}"
        )

    @settings(max_examples=100)
    @given(doc=documents_with_ordered_spans())
    def test_compressed_read_path_preserves_span_order(self, doc):
        text, markers = doc
        # compress_text is the compression behind the compressed read path
        # (tokenlens_read_file); it must preserve retained-span order too.
        result = compress_text(text)

        input_order = _surviving_markers(markers, text)
        output_order = _surviving_markers(markers, result["text"])
        assert output_order == input_order, (
            "Compressed read path changed the relative order of retained "
            f"spans: input {input_order!r} vs output {output_order!r}"
        )
