"""Property-based test for Lever 1 and Lever 3 verbatim preservation.

Covers design Property 1 for the always-on-savings feature: every item of
load-bearing content present in the input (fenced code blocks, inline code,
file paths, URLs, and long identifiers) appears verbatim in the output of each
content-producing lever (the Context File Compressor behind
``compress_context_file`` and the compressed read path behind
``compress_text``).
"""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_context_file, compress_text

# Prose alphabet deliberately excludes backticks, slashes, colons, and digits
# so generated prose cannot accidentally form a load-bearing span (path, URL,
# inline/fenced code, or long identifier) that the assertions would then look
# for and fail to find because it appears more than intended.
_PROSE = string.ascii_letters + " \n\t.,;!?()"
_IDENT_CHARS = string.ascii_letters + string.digits + "-_"
_PATH_SEG = string.ascii_letters + "_-"
_URL_CHARS = string.ascii_lowercase + string.digits + "-./"


@st.composite
def _prose(draw) -> str:
    """Free-form prose containing no load-bearing spans."""
    return draw(st.text(alphabet=_PROSE, min_size=0, max_size=60))


@st.composite
def _fenced_code(draw) -> str:
    """A fenced code block delimited by triple backticks on their own lines."""
    lang = draw(st.sampled_from(["", "python", "bash", "json", "text"]))
    body_lines = draw(
        st.lists(
            st.text(alphabet=string.ascii_letters + string.digits + " =+(){}._",
                    min_size=0, max_size=30),
            min_size=1,
            max_size=4,
        )
    )
    body = "\n".join(body_lines)
    return f"```{lang}\n{body}\n```"


@st.composite
def _inline_code(draw) -> str:
    """A single-line inline code span."""
    inner = draw(
        st.text(
            alphabet=string.ascii_letters + string.digits + " =+().-_",
            min_size=1,
            max_size=20,
        )
    )
    return f"`{inner}`"


@st.composite
def _file_path(draw) -> str:
    """A slash-separated file path with a dotted final segment."""
    segments = draw(
        st.lists(
            st.text(alphabet=_PATH_SEG, min_size=1, max_size=10),
            min_size=1,
            max_size=4,
        )
    )
    stem = draw(st.text(alphabet=_PATH_SEG, min_size=1, max_size=10))
    ext = draw(st.sampled_from(["py", "md", "yaml", "json", "txt", "ts"]))
    prefix = draw(st.sampled_from(["", "./", "/"]))
    return prefix + "/".join(segments) + f"/{stem}.{ext}"


@st.composite
def _url(draw) -> str:
    """An http(s) URL with no whitespace or closing paren."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10))
    tld = draw(st.sampled_from(["com", "org", "dev", "io"]))
    path = draw(st.text(alphabet=_URL_CHARS, min_size=0, max_size=20))
    tail = f"/{path}" if path else ""
    return f"{scheme}://{host}.{tld}{tail}"


@st.composite
def _long_identifier(draw) -> str:
    """A long identifier of length >= 12 that contains at least one digit."""
    # Build a 12+ char token from an alphabetic head, at least one digit, and
    # a filler tail, so it reliably matches the identifier masker.
    head = draw(st.text(alphabet=string.ascii_letters, min_size=1, max_size=6))
    digit = draw(st.sampled_from(string.digits))
    tail = draw(st.text(alphabet=_IDENT_CHARS, min_size=12, max_size=24))
    token = head + digit + tail
    # Guarantee length >= 12 (head+digit+tail is already >= 14, but be explicit).
    return token


_SPAN_STRATEGIES = st.one_of(
    _fenced_code(),
    _inline_code(),
    _file_path(),
    _url(),
    _long_identifier(),
)


@st.composite
def interleaved_content(draw):
    """Interleave random prose with load-bearing spans.

    Returns a tuple ``(document, spans)`` where ``document`` is the assembled
    text and ``spans`` is the list of load-bearing substrings that must survive
    verbatim.
    """
    n_spans = draw(st.integers(min_value=0, max_value=6))
    spans: list[str] = []
    parts: list[str] = [draw(_prose())]
    for _ in range(n_spans):
        span = draw(_SPAN_STRATEGIES)
        spans.append(span)
        # Separate the span from surrounding text with newlines so fenced
        # blocks (which must start on their own line) are recognized, and so
        # spans do not fuse with adjacent prose.
        parts.append("\n" + span + "\n")
        parts.append(draw(_prose()))
    document = "".join(parts)
    return document, spans


class TestVerbatimPreservationProperty:
    # Feature: always-on-savings, Property 1: For any input content processed by a content-producing lever (the Context File Compressor or the compressed read path), every item of load-bearing content present in the input (fenced code blocks, inline code, file paths, URLs, and long identifiers) appears verbatim in the produced output.
    # **Validates: Requirements 1.2, 3.5, 4.1**
    @settings(max_examples=100, deadline=None)
    @given(data=interleaved_content())
    def test_load_bearing_content_preserved_verbatim(self, data):
        document, spans = data

        # Lever 1: Context File Compressor.
        context_out = compress_context_file(document)["text"]
        # Lever 3: compressed read path (compress_text behind tokenlens_read_file).
        read_out = compress_text(document)["text"]

        for span in spans:
            assert span in context_out, (
                f"load-bearing span missing from context-file output: {span!r}"
            )
            assert span in read_out, (
                f"load-bearing span missing from read-path output: {span!r}"
            )
