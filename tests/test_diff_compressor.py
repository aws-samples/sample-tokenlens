import re
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.compressors.code import CodeCompressor
from tokenlens.compressors.diff import (
    _EDGE_KEEP,
    _RUN_THRESHOLD,
    DiffCompressor,
)
from tokenlens.compressors.registry import select_compressor

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
_TEXT = string.ascii_letters + string.digits + " _().;="


def _content_line() -> st.SearchStrategy:
    return st.text(alphabet=_TEXT, min_size=0, max_size=40)


@st.composite
def unified_diffs(draw, min_context_run: int = 0, max_context_run: int = 15):
    """Build a synthetic unified diff with controllable context-run lengths.

    Each hunk gets a leading context run, a block of changed lines, and a
    trailing context run, so run lengths can be driven above and below the
    collapse threshold deliberately.
    """
    n_hunks = draw(st.integers(min_value=1, max_value=3))
    lines = ["diff --git a/f.py b/f.py", "index 1111111..2222222 100644",
             "--- a/f.py", "+++ b/f.py"]
    changed_lines: list[str] = []
    header_lines: list[str] = []
    for h in range(n_hunks):
        start = 1 + h * 100
        header = f"@@ -{start},10 +{start},10 @@"
        header_lines.append(header)
        lines.append(header)
        lead = draw(st.integers(min_value=min_context_run, max_value=max_context_run))
        for _ in range(lead):
            lines.append(" " + draw(_content_line()))
        n_changes = draw(st.integers(min_value=1, max_value=4))
        for _ in range(n_changes):
            sign = draw(st.sampled_from(["+", "-"]))
            changed = sign + draw(_content_line())
            changed_lines.append(changed)
            lines.append(changed)
        trail = draw(st.integers(min_value=min_context_run, max_value=max_context_run))
        for _ in range(trail):
            lines.append(" " + draw(_content_line()))
    return "\n".join(lines), changed_lines, header_lines


class TestDiffDetectionUnit:
    def test_matches_minimal_single_hunk_diff(self):
        diff = "@@ -1,2 +1,2 @@\n old\n-a\n+b\n"
        assert DiffCompressor().matches(diff)

    def test_does_not_match_prose(self):
        assert not DiffCompressor().matches("just some prose with an @ sign here")

    def test_does_not_match_json(self):
        assert not DiffCompressor().matches('[{"id": 1}, {"id": 2}]')

    def test_does_not_match_plain_code(self):
        assert not DiffCompressor().matches("def foo():\n    return 1\n")


class TestShortContextRunUntouched:
    def test_short_run_left_unmodified(self):
        # A context run at the threshold is not collapsed.
        thr = _RUN_THRESHOLD["balanced"]
        body = "\n".join(f" ctx{i}" for i in range(thr))
        diff = f"@@ -1,{thr} +1,{thr} @@\n{body}\n-old\n+new\n"
        result = DiffCompressor().compress(diff, "balanced")
        assert "omitted" not in result.text
        for i in range(thr):
            assert f" ctx{i}" in result.text


class TestDiffProperties:
    # **Feature: context-compressors-v2, Property 1: Diff detection matches only diff-shaped text**
    @settings(max_examples=100)
    @given(text=st.text(max_size=400))
    def test_detection_matches_iff_hunk_header(self, text):
        has_header = bool(_HUNK_HEADER.search(text) and re.search(
            r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", text, re.MULTILINE))
        assert DiffCompressor().matches(text) == has_header

    # **Feature: context-compressors-v2, Property 3: Load-bearing diff lines are preserved**
    @settings(max_examples=100)
    @given(data=unified_diffs())
    def test_load_bearing_lines_preserved(self, data):
        diff, changed_lines, header_lines = data
        out = DiffCompressor().compress(diff, "balanced").text
        out_lines = out.splitlines()
        # Every hunk header and changed line survives, in order.
        expected = header_lines + changed_lines
        # Check each load-bearing line is present.
        for line in header_lines:
            assert line in out_lines
        for line in changed_lines:
            assert line in out_lines
        # Relative order of load-bearing lines is preserved.
        positions = [out_lines.index(h) for h in header_lines]
        assert positions == sorted(positions)

    # **Feature: context-compressors-v2, Property 4: Context runs are collapsed only when they exceed the threshold**
    @settings(max_examples=100)
    @given(run_len=st.integers(min_value=0, max_value=30))
    def test_context_run_collapsed_iff_over_threshold(self, run_len):
        thr = _RUN_THRESHOLD["balanced"]
        edge = _EDGE_KEEP["balanced"]
        body = "\n".join(f" ctx{i}" for i in range(run_len))
        diff = f"@@ -1,{run_len} +1,{run_len} @@\n{body}\n-old\n+new\n"
        out = DiffCompressor()._compress(diff, "balanced")
        marker = re.search(r"\.\.\. \[(\d+) unchanged lines omitted\] \.\.\.", out)
        if run_len > thr:
            assert marker is not None
            assert int(marker.group(1)) == run_len - 2 * edge
        else:
            assert marker is None

    # **Feature: context-compressors-v2, Property 5: Diff Compressor never grows its input**
    @settings(max_examples=100)
    @given(data=unified_diffs())
    def test_never_grows_input(self, data):
        diff = data[0]
        result = DiffCompressor().compress(diff, "balanced")
        assert result.compressed_tokens <= result.original_tokens
        if not result.applied:
            assert result.text == diff


class TestRegistryPreference:
    # **Feature: context-compressors-v2, Property 2: Diff Compressor is preferred over Code Compressor for diffs**
    @settings(max_examples=100)
    @given(data=unified_diffs())
    def test_diff_preferred_over_code(self, data):
        diff = data[0]
        selected = select_compressor(diff)
        assert isinstance(selected, DiffCompressor)
        assert not isinstance(selected, CodeCompressor)
