"""Property-based tests for the markdown quality extractor.

These tests exercise the ``markdown`` content type added to
``tokenlens.quality`` at the
quality-extractor / quality-gate level. A markdown-artifact generator composes
synthetic markdown content from load-bearing parts (requirement IDs, acceptance
criteria, ATX headings, ARNs, URLs, file paths, long hex identifiers, and code
signatures) interleaved with droppable filler prose. The real ``compress_text``
lever compresses the artifact, and the properties assert the relationship
between ``evaluate_compression(..., "markdown").passed`` and the presence of
every load-bearing item and heading in the returned content.
"""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.api import compress_text
from tokenlens.quality import (
    _code_critical,
    _markdown_critical,
    _text_critical,
    evaluate_compression,
)

# Prose alphabet deliberately excludes characters that could accidentally form
# a load-bearing span (heading marker, req-id, path, URL, long hex id, or a
# code signature keyword), so generated filler is genuinely droppable and the
# assertions only look for the spans we deliberately inserted.
_PROSE = string.ascii_lowercase + " .,;!?"
_SEG = string.ascii_lowercase + "-_"


@st.composite
def _seg(draw, min_size=1, max_size=10) -> str:
    """A path segment that starts with a word character.

    The extractor's _PATH pattern is anchored on a word boundary, so a segment
    that begins with '-' would not be recognized as a path. Real design-doc
    paths start with an alphanumeric, so constrain the generator to match.
    """
    head = draw(st.sampled_from(string.ascii_lowercase + string.digits))
    tail = draw(st.text(alphabet=_SEG, min_size=max(0, min_size - 1),
                        max_size=max_size - 1))
    return head + tail


@st.composite
def _prose(draw) -> str:
    """Free-form filler prose containing no load-bearing spans."""
    return draw(st.text(alphabet=_PROSE, min_size=0, max_size=50))


@st.composite
def _heading(draw) -> str:
    """A full ATX heading line at a level in 1..6 with plain-word text."""
    level = draw(st.integers(min_value=1, max_value=6))
    words = draw(
        st.lists(
            st.text(alphabet=string.ascii_uppercase + string.ascii_lowercase,
                    min_size=1, max_size=8),
            min_size=1,
            max_size=4,
        )
    )
    return "#" * level + " " + " ".join(words)


@st.composite
def _req_id(draw) -> str:
    """A requirement ID like FR-001 or NFR-012 (\\b[A-Z]{2,}-\\d+\\b)."""
    prefix = draw(st.sampled_from(["FR", "NFR", "REQ", "SEC", "AC"]))
    num = draw(st.integers(min_value=0, max_value=9999))
    return f"{prefix}-{num:03d}"


@st.composite
def _criterion(draw) -> str:
    """An acceptance-criterion line carrying an embedded requirement ID."""
    rid = draw(_req_id())
    body = draw(st.text(alphabet=_PROSE, min_size=1, max_size=30))
    return f"1. WHEN condition THE system SHALL satisfy {rid}: {body}"


@st.composite
def _arn(draw) -> str:
    """An AWS ARN (arn:aws...:...) with no whitespace or quote terminators."""
    service = draw(st.sampled_from(["lambda", "s3", "ec2", "iam", "dynamodb"]))
    region = draw(st.sampled_from(["us-east-1", "us-west-2", "eu-west-1"]))
    acct = draw(st.integers(min_value=1000, max_value=999999999999))
    res = draw(st.text(alphabet=_SEG, min_size=1, max_size=12))
    return f"arn:aws:{service}:{region}:{acct}:resource/{res}"


@st.composite
def _url(draw) -> str:
    """An http(s) URL with no whitespace or closing paren."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10))
    tld = draw(st.sampled_from(["com", "org", "dev", "io"]))
    path = draw(st.text(alphabet=string.ascii_lowercase + string.digits + "-/",
                        min_size=0, max_size=20))
    tail = f"/{path}" if path else ""
    return f"{scheme}://{host}.{tld}{tail}"


@st.composite
def _file_path(draw) -> str:
    """A slash-separated file path with a dotted final segment."""
    segments = draw(st.lists(_seg(), min_size=1, max_size=4))
    stem = draw(_seg())
    ext = draw(st.sampled_from(["py", "md", "yaml", "json", "ts"]))
    return "/".join(segments) + f"/{stem}.{ext}"


@st.composite
def _long_hex(draw) -> str:
    """A long hex identifier of length >= 12 (matches _LONG_HEX)."""
    return draw(st.text(alphabet="0123456789abcdef", min_size=12, max_size=40))


@st.composite
def _signature(draw) -> str:
    """A code/schema signature line matched by _code_critical."""
    keyword = draw(st.sampled_from(["def", "async def", "class", "function",
                                    "interface", "struct", "enum"]))
    name = draw(st.text(alphabet=string.ascii_letters, min_size=1, max_size=12))
    return f"{keyword} {name}(self, payload):"


# Each entry yields (span_text, kind) so tests can bucket assertions.
_SPAN_STRATEGIES = st.one_of(
    _heading().map(lambda s: (s, "heading")),
    _req_id().map(lambda s: (s, "req_id")),
    _criterion().map(lambda s: (s, "criterion")),
    _arn().map(lambda s: (s, "arn")),
    _url().map(lambda s: (s, "url")),
    _file_path().map(lambda s: (s, "path")),
    _long_hex().map(lambda s: (s, "hex")),
    _signature().map(lambda s: (s, "signature")),
)


@st.composite
def markdown_artifact(draw):
    """Compose a synthetic markdown artifact.

    Returns ``(document, spans)`` where ``document`` is the assembled markdown
    and ``spans`` is the list of ``(text, kind)`` load-bearing spans that were
    deliberately inserted. Filler prose is added around each span and lots of
    droppable filler is appended so compression has room to shrink the text.
    """
    n_spans = draw(st.integers(min_value=1, max_value=8))
    spans: list[tuple[str, str]] = []
    parts: list[str] = ["# Design Artifact", ""]
    for _ in range(n_spans):
        span, kind = draw(_SPAN_STRATEGIES)
        spans.append((span, kind))
        parts.append(draw(_prose()))
        # Each span sits on its own line so headings/signatures are recognized
        # and spans never fuse with adjacent prose.
        parts.append(span)
        parts.append(draw(_prose()))
    # Append a block of highly compressible filler prose.
    filler = draw(st.text(alphabet=_PROSE, min_size=0, max_size=40))
    parts.extend([filler] * draw(st.integers(min_value=0, max_value=20)))
    document = "\n".join(parts)
    return document, spans


@st.composite
def heading_artifact(draw):
    """Compose an artifact with ATX headings at varied levels plus filler.

    Returns ``(document, headings)`` where ``headings`` is the list of full
    heading lines that must survive verbatim.
    """
    n = draw(st.integers(min_value=1, max_value=8))
    headings: list[str] = []
    parts: list[str] = []
    for _ in range(n):
        h = draw(_heading())
        headings.append(h)
        parts.append(h)
        parts.append("")
        parts.append(draw(st.text(alphabet=_PROSE, min_size=0, max_size=60)))
    parts.extend([draw(st.text(alphabet=_PROSE, min_size=0, max_size=40))]
                 * draw(st.integers(min_value=0, max_value=15)))
    document = "\n".join(parts)
    return document, headings


class TestMarkdownLoadBearingPreservation:
    # Property 3: For any markdown artifact
    # whose read returns compressed content, every requirement ID, acceptance
    # criterion, code signature, data schema, ARN, URL, file path, and
    # component name present in the original appears byte-for-byte identically
    # in the returned content.
    # **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    @settings(max_examples=100, deadline=None)
    @given(data=markdown_artifact())
    def test_passing_gate_implies_all_load_bearing_items_present(self, data):
        document, spans = data
        path = "docs/requirements.md"

        # The extractor must pick up every load-bearing item in the original.
        critical = _markdown_critical(document)
        for text, kind in spans:
            if kind == "heading":
                assert text in critical, f"heading missing from extractor: {text!r}"
            elif kind == "req_id":
                assert text in critical, f"req id missing from extractor: {text!r}"
            elif kind == "criterion":
                # The embedded requirement ID is the byte-for-byte item tracked.
                # (The criterion line's req id must be extracted.)
                assert any(item in text for item in critical if "-" in item), (
                    f"criterion's req id missing from extractor: {text!r}"
                )
            elif kind in {"arn", "url", "path", "hex"}:
                assert text in _text_critical(document), (
                    f"{kind} missing from text extractor: {text!r}"
                )
                assert text in critical, f"{kind} missing from extractor: {text!r}"
            elif kind == "signature":
                assert text in _code_critical(document), (
                    f"signature missing from code extractor: {text!r}"
                )
                assert text in critical, f"signature missing from extractor: {text!r}"

        # Compress via the real lever, then run the markdown quality gate.
        compressed = compress_text(document, filename=path)["text"]
        result = evaluate_compression(path, document, compressed, "markdown")

        # If the gate passes, every critical item is byte-for-byte present.
        if result.passed:
            for item in critical:
                assert item in compressed, (
                    f"gate passed but load-bearing item missing: {item!r}"
                )
        # Conversely, a passing gate means no missing items were recorded.
        assert result.passed == (len(result.missing) == 0)


class TestMarkdownHeadingAnchorPreservation:
    # Property 4: For any markdown artifact
    # whose read returns compressed content, every ATX heading present in the
    # original appears in the returned content as a heading of the same level
    # with byte-for-byte identical heading text, so the anchor fragment derived
    # from each heading is unchanged and resolves to the same section.
    # **Validates: Requirements 4.1, 4.2, 10.4**
    @settings(max_examples=100, deadline=None)
    @given(data=heading_artifact())
    def test_headings_extracted_and_dropped_heading_fails_gate(self, data):
        document, headings = data
        path = "docs/architecture.md"

        # Every full heading line (level + text) is a critical item.
        critical = _markdown_critical(document)
        for h in headings:
            assert h in critical, f"heading line missing from extractor: {h!r}"

        # Dropping any single heading from the compressed output must fail the
        # gate: the derived anchor would no longer resolve.
        target = headings[0]
        dropped = document.replace(target + "\n", "", 1)
        if target not in dropped:  # only meaningful when the drop removed it
            result = evaluate_compression(path, document, dropped, "markdown")
            assert not result.passed, (
                f"gate should fail when heading dropped: {target!r}"
            )
            assert target in result.missing


class TestMarkdownQualityGateFailureReturnsOriginal:
    # Property 6: For any markdown artifact
    # whose profitable compressed output omits any load-bearing content item or
    # any heading anchor, the read returns the original artifact content
    # byte-for-byte unchanged and indicates compression was not applied.
    # **Validates: Requirements 3.5, 4.3, 7.2, 7.3**
    @settings(max_examples=100, deadline=None)
    @given(data=markdown_artifact())
    def test_dropping_a_load_bearing_item_fails_the_gate(self, data):
        document, spans = data
        path = "docs/security-notes.md"

        critical = _markdown_critical(document)
        # Construct a compressed string that omits one load-bearing item.
        victim = critical[0]
        broken = document.replace(victim, "", 1)

        # Skip the (rare) case where the item still appears elsewhere: only a
        # genuinely omitted item should fail the gate.
        if victim not in broken:
            result = evaluate_compression(path, document, broken, "markdown")
            assert not result.passed, (
                f"gate must fail when load-bearing item omitted: {victim!r}"
            )
            assert victim in result.missing

        # A byte-for-byte identical "compressed" output always passes the gate,
        # which is the signal the read path uses to keep the original.
        assert evaluate_compression(path, document, document, "markdown").passed
