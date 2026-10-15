"""Property-based test for the always-on-savings output-derivation guarantee.

Covers design Property 3 for the always-on-savings feature: no Always-On Lever
fabricates content. A content-producing lever (the Context File Compressor and
the compressed read path behind ``tokenlens_read_file``) introduces no token
that was absent from its input. The History Trimmer is allowed exactly one
exception: the fixed structural placeholder markers it inserts for omitted
turns.
"""

import copy
import re
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_context_file, compress_text
from tokenlens.history import ConversationHistoryTrimmer

# Tokenization scheme: words / identifiers / numbers. This is a reasonable,
# language-agnostic split that treats each maximal run of word characters as a
# token and ignores whitespace and punctuation, so we compare the *vocabulary*
# a lever emits against the vocabulary present in its input.
_TOKEN_RE = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text))


# Content alphabet: prose plus punctuation and the structural characters used
# by fenced code, inline code, paths, URLs, and identifiers, so generated
# documents both do and do not compress.
_TEXT = string.ascii_letters + string.digits + " \n\t.,:;!?{}[]()\"'-_/`#"


@st.composite
def _load_bearing_span(draw) -> str:
    """A single embedded load-bearing span (code, path, URL, or identifier)."""
    word = draw(st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=8))
    num = draw(st.integers(min_value=0, max_value=9999))
    kind = draw(st.integers(min_value=0, max_value=4))
    if kind == 0:
        return f"```\ncode {word} = {num}\nmore {word}\n```"
    if kind == 1:
        return f"`{word}_{num}`"
    if kind == 2:
        return f"src/tokenlens/{word}_{num}.py"
    if kind == 3:
        return f"https://example.com/{word}/{num}"
    return f"{word}{num}deadbeefcafe"


@st.composite
def documents(draw) -> str:
    """Prose interleaved with embedded load-bearing spans.

    Blank lines and duplicate lines are included so the prose compressor has
    something to collapse, exercising the compressed branch as well as the
    unchanged branch.
    """
    parts = draw(st.lists(st.text(alphabet=_TEXT, min_size=0, max_size=80), max_size=8))
    spans = draw(st.lists(_load_bearing_span(), max_size=4))
    chunks: list[str] = []
    for i, part in enumerate(parts):
        chunks.append(part)
        chunks.append("")  # blank line (collapsible)
        chunks.append(part)  # duplicate line (droppable)
        if i < len(spans):
            chunks.append(spans[i])
    for span in spans[len(parts):]:
        chunks.append(span)
    return "\n".join(chunks)


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

# The fixed vocabulary the History Trimmer's placeholder can introduce:
# "[Earlier {role} turn omitted: ~{turn_tokens} tokens]". Role words and the
# numeric token counts are also allowed, since the marker is structural and the
# numbers are derived from (not fabricated beyond) the trimmed content.
_PLACEHOLDER_MARKER_WORDS = {"Earlier", "turn", "omitted", "tokens"}
_ROLE_WORDS = {"user", "assistant", "unknown", "system", "tool"}


class TestOutputDerivationProperty:
    # Feature: always-on-savings, Property 3: For any input content, a content-producing lever's output introduces no token that was absent from the input, except the fixed structural placeholder markers the History Trimmer inserts for omitted turns.
    # **Validates: Requirements 4.2**

    @settings(max_examples=100)
    @given(text=documents())
    def test_context_file_compressor_introduces_no_new_token(self, text):
        result = compress_context_file(text)
        new_tokens = _tokens(result["text"]) - _tokens(text)
        assert new_tokens == set(), (
            "Context File Compressor introduced tokens absent from input: "
            f"{sorted(new_tokens)!r}"
        )

    @settings(max_examples=100)
    @given(text=documents())
    def test_compressed_read_path_introduces_no_new_token(self, text):
        # compress_text is the compression behind the compressed read path
        # (tokenlens_read_file). It must not fabricate tokens either.
        result = compress_text(text)
        new_tokens = _tokens(result["text"]) - _tokens(text)
        assert new_tokens == set(), (
            "Compressed read path introduced tokens absent from input: "
            f"{sorted(new_tokens)!r}"
        )

    @settings(max_examples=100)
    @given(messages=message_lists(), window=windows)
    def test_history_trimmer_only_introduces_fixed_placeholder_tokens(
        self, messages, window
    ):
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)

        input_tokens: set[str] = set()
        for turn in original:
            content = turn.get("content")
            if isinstance(content, str):
                input_tokens |= _tokens(content)

        output_tokens: set[str] = set()
        for turn in result.messages:
            content = turn.get("content")
            if isinstance(content, str):
                output_tokens |= _tokens(content)

        # Any token in the output that was not in the input must come from the
        # trimmer's fixed placeholder marker: the marker words, a role word, or
        # a numeric token-count. Nothing else may be fabricated.
        introduced = output_tokens - input_tokens
        allowed = _PLACEHOLDER_MARKER_WORDS | _ROLE_WORDS
        residual = {tok for tok in introduced if tok not in allowed and not tok.isdigit()}
        assert residual == set(), (
            "History Trimmer introduced tokens beyond the fixed placeholder "
            f"markers: {sorted(residual)!r}"
        )
