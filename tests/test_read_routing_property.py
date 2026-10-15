"""Property-based test for Lever 3 (File-Read Routing) threshold gating.

Covers design Property 11 for the always-on-savings feature: the compressed
read path (``tokenlens_read_file``) compresses only when the file's Token Count
is at or above the configured routing threshold and compression is profitable;
below the threshold it returns the original content unchanged (``applied``
false).
"""

import asyncio
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.adapters.mcp_server import create_mcp_server
from tokenlens.config import Config
from tokenlens.tokens import count_tokens

_TEXT = string.ascii_letters + string.digits + " \n\t.,:{}[]\"'-_/"


def _config(tmp_path, threshold: int) -> Config:
    return Config(
        metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
        always_on={"read_routing_threshold": threshold},
    )


def _structured(call_result):
    """Extract the structured dict from a FastMCP call_tool return value."""
    if isinstance(call_result, tuple):
        return call_result[1]
    return call_result


@st.composite
def _prose(draw):
    """Free-form text that generally does not compress."""
    return draw(st.text(alphabet=_TEXT, min_size=0, max_size=4000))


@st.composite
def _compressible(draw):
    """Repetitive / structured content that the compressors reliably shrink,
    so the at/above-threshold branch can exercise a profitable compression."""
    n = draw(st.integers(min_value=50, max_value=400))
    key = draw(st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=8))
    rows = [{"id": i, key: f"{key}-value-{i}"} for i in range(n)]
    import json as _json

    return _json.dumps(rows)


@st.composite
def content_and_threshold(draw):
    """Generate file content paired with a routing threshold placed either
    below or at/above the content's Token Count, so both sides of the
    threshold boundary are exercised. Content is a mix of free-form prose
    (usually not profitable to compress) and repetitive structured content
    (profitable), so both the compressed-output and unchanged-output branches
    at/above the threshold are covered."""
    text = draw(st.one_of(_prose(), _compressible()))
    tokens = count_tokens(text)
    # Bias the threshold to straddle the content's token count in both
    # directions, plus exact-boundary and zero cases.
    threshold = draw(
        st.one_of(
            st.just(0),  # everything is at/above -> compression path
            st.just(tokens),  # exactly at the boundary -> at/above
            st.just(tokens + 1),  # just above -> below-threshold pass-through
            st.integers(min_value=0, max_value=max(tokens, 1)),
            st.integers(min_value=tokens + 1, max_value=tokens + 500),
        )
    )
    return text, threshold


class TestReadRoutingThresholdProperty:
    # Feature: always-on-savings, Property 11: For any file content and routing threshold, the compressed read path returns compressed content only when the file's Token Count is at or above the threshold and compression is profitable; when the Token Count is below the threshold, it returns the original content unchanged (applied false).
    # **Validates: Requirements 3.2, 3.3**
    @settings(max_examples=100, deadline=None)
    @given(data=content_and_threshold())
    def test_threshold_gated_read_routing(self, tmp_path_factory, data):
        text, threshold = data
        tmp_path = tmp_path_factory.mktemp("read_routing")
        target = tmp_path / "file.txt"
        target.write_text(text, encoding="utf-8")

        server = create_mcp_server(_config(tmp_path, threshold))
        result = _structured(
            asyncio.run(
                server.call_tool("tokenlens_read_file", {"path": str(target)})
            )
        )

        tokens = count_tokens(text)

        if tokens < threshold:
            # Below threshold: original content returned unchanged, applied false.
            assert result["applied"] is False
            assert result["text"] == text
            assert result["original_tokens"] == tokens
            assert result["compressed_tokens"] == tokens
            assert result["tokens_saved"] == 0
        else:
            # At or above threshold: compression runs. It is applied only when
            # profitable; otherwise the original content is returned unchanged.
            if result["applied"]:
                assert result["compressed_tokens"] < result["original_tokens"]
                assert result["tokens_saved"] > 0
            else:
                assert result["text"] == text
                assert result["tokens_saved"] == 0
