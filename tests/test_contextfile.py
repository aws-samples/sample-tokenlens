import re
import string
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens import api as api_module
from tokenlens.api import compress_context_file
from tokenlens.contextfile import ContextFileCompressor

_PROSE = string.ascii_letters + string.digits + " .,\n"

_PATHS = ["src/tokenlens/api.py", "/etc/app/config.yaml", "./x/y/z.md"]
_URLS = ["https://example.com/docs", "http://a.b/c?d=1"]
_IDENTS = ["a1b2c3d4e5f6a7b8", "arn0aws0iam012345", "commit9f8e7d6c5b4a"]


@st.composite
def context_docs(draw):
    """Synthetic context-file text: prose, blank runs, duplicate lines,
    filler, fenced/inline code, and seeded paths/URLs/identifiers."""
    parts = []
    if draw(st.booleans()):
        parts.append("Sure, here is the guide.\n")
    dup = "This line may repeat.\n"
    if draw(st.booleans()):
        parts.append(dup + dup)  # consecutive duplicates
    parts.append(draw(st.text(alphabet=_PROSE, min_size=0, max_size=200)) + "\n")
    parts.append("\n\n\n")  # blank run
    if draw(st.booleans()):
        parts.append(f"See {draw(st.sampled_from(_URLS))} and {draw(st.sampled_from(_PATHS))}.\n")
    if draw(st.booleans()):
        parts.append(f"Token `{draw(st.sampled_from(_IDENTS))}` and id {draw(st.sampled_from(_IDENTS))}.\n")
    if draw(st.booleans()):
        parts.append("```python\ndef f():\n    return 1  # sure\n```\n")
    return "".join(parts)


def _fenced_spans(text: str) -> list[str]:
    return re.findall(r"(?ms)^[ \t]*(?:`{3,}|~{3,}).*?(?:^[ \t]*(?:`{3,}|~{3,})[ \t]*$|\Z)", text)


class TestContextFileProperties:
    # **Feature: context-file-compression, Property 1: Compression never grows the input and no-ops when unhelpful**
    @settings(max_examples=100)
    @given(text=st.text(max_size=500))
    def test_never_grows(self, text):
        r = ContextFileCompressor().compress(text)
        assert r.compressed_tokens <= r.original_tokens
        if not r.applied:
            assert r.text == text

    # **Feature: context-file-compression, Property 2: Compression is idempotent**
    @settings(max_examples=100)
    @given(text=context_docs())
    def test_idempotent(self, text):
        once = ContextFileCompressor().compress(text).text
        twice = ContextFileCompressor().compress(once).text
        assert twice == once

    # **Feature: context-file-compression, Property 3: Code spans are preserved verbatim**
    @settings(max_examples=100)
    @given(text=context_docs())
    def test_code_spans_preserved(self, text):
        out = ContextFileCompressor().compress(text).text
        for span in _fenced_spans(text):
            assert span in out

    # **Feature: context-file-compression, Property 4: Paths, URLs, and identifiers are preserved**
    @settings(max_examples=100)
    @given(text=context_docs())
    def test_paths_urls_identifiers_preserved(self, text):
        out = ContextFileCompressor().compress(text).text
        for token in _PATHS + _URLS + _IDENTS:
            if token in text:
                assert token in out


class TestStableApi:
    def test_shape(self):
        result = compress_context_file("Sure, hello world.\n\n\ndone")
        assert set(result.keys()) >= {
            "text", "original_tokens", "compressed_tokens", "tokens_saved", "applied"
        }

    # **Feature: context-file-compression, Property 5: Compression errors fail open**
    @settings(max_examples=100)
    @given(text=context_docs())
    def test_fail_open(self, text):
        def _boom(self, t):
            raise RuntimeError("boom")

        with patch.object(api_module.ContextFileCompressor, "compress", _boom):
            result = compress_context_file(text)
        assert result["text"] == text
        assert result["applied"] is False


class TestUnitExample:
    def test_preamble_dupes_blanks_code_path(self):
        text = (
            "Sure, here is the doc.\n"
            "Repeated line.\n"
            "Repeated line.\n"
            "\n\n\n"
            "Use src/tokenlens/api.py.\n"
            "```python\ndef foo():\n    return 1\n```\n"
        )
        r = ContextFileCompressor().compress(text)
        assert r.applied
        assert r.compressed_tokens < r.original_tokens
        assert "def foo():" in r.text
        assert "src/tokenlens/api.py" in r.text
        # consecutive duplicate collapsed
        assert r.text.count("Repeated line.") == 1
        # blank run collapsed (no triple newline)
        assert "\n\n\n" not in r.text


from click.testing import CliRunner

from tokenlens.cli import main

_SAMPLE = (
    "Sure, here is the doc.\n"
    "Dup line.\n"
    "Dup line.\n"
    "\n\n\n"
    "Use src/tokenlens/api.py and see https://example.com/x.\n"
    "```python\ndef foo():\n    return 1\n```\n"
)


class TestCompressFileCLI:
    def test_default_writes_separate_file_and_keeps_original(self, tmp_path):
        src = tmp_path / "AGENTS.md"
        src.write_text(_SAMPLE, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compress-file", str(src), "--config", str(tmp_path / "none.yaml")],
        )
        assert result.exit_code == 0, result.output
        # Original untouched
        assert src.read_text(encoding="utf-8") == _SAMPLE
        # Separate output created
        out = tmp_path / "AGENTS.md.tokenlens.md"
        assert out.exists()
        assert "def foo():" in out.read_text(encoding="utf-8")
        assert "saved" in result.output

    def test_out_option(self, tmp_path):
        src = tmp_path / "steering.md"
        src.write_text(_SAMPLE, encoding="utf-8")
        dest = tmp_path / "steering.min.md"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compress-file", str(src), "--out", str(dest), "--config", str(tmp_path / "n.yaml")],
        )
        assert result.exit_code == 0, result.output
        assert dest.exists()

    def test_in_place_overwrites(self, tmp_path):
        src = tmp_path / "CLAUDE.md"
        src.write_text(_SAMPLE, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compress-file", str(src), "--in-place", "--config", str(tmp_path / "n.yaml")],
        )
        assert result.exit_code == 0, result.output
        # Original overwritten (dup collapsed), code preserved
        new = src.read_text(encoding="utf-8")
        assert new.count("Dup line.") == 1
        assert "def foo():" in new
        assert not (tmp_path / "CLAUDE.md.tokenlens.md").exists()

    def test_missing_file_errors(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["compress-file", str(tmp_path / "nope.md")])
        assert result.exit_code != 0
        assert "not a file" in result.output


class TestCompressFileArtifactAndMetrics:
    """Task 6.1: compress-file (Lever 1) writes a separate artifact, leaves
    the source byte-for-byte identical, and records a
    ``context_file_compression`` metrics event.

    Requirements: 1.1, 1.3, 7.2
    """

    def test_writes_artifact_preserves_source_and_records_metric(self, tmp_path, monkeypatch):
        import json

        # A steering file with content that actually compresses (leading
        # filler, consecutive duplicate lines, and a blank run) while carrying
        # load-bearing spans that must survive.
        source_bytes = (
            "Sure, here is the steering guide.\n"
            "Duplicated instruction line.\n"
            "Duplicated instruction line.\n"
            "\n\n\n"
            "Always read src/tokenlens/api.py and see https://example.com/docs.\n"
            "```python\ndef handler():\n    return 1\n```\n"
        ).encode("utf-8")

        src = tmp_path / ".kiro" / "steering" / "guide.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(source_bytes)

        # Point metrics at a temp dir hermetically. Env vars have the highest
        # config precedence, so this wins over any ambient TOKENLENS_METRICS_*
        # in the developer/CI environment.
        metrics_dir = tmp_path / "metrics"
        monkeypatch.setenv("TOKENLENS_METRICS_ENABLED", "true")
        monkeypatch.setenv("TOKENLENS_METRICS_DIR", str(metrics_dir))

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compress-file", str(src), "--config", str(tmp_path / "none.yaml")],
        )
        assert result.exit_code == 0, result.output

        # Requirement 1.3: the original source is unchanged byte-for-byte.
        assert src.read_bytes() == source_bytes

        # Requirement 1.1: a separate {source}.tokenlens.md artifact is written.
        artifact = tmp_path / ".kiro" / "steering" / "guide.md.tokenlens.md"
        assert artifact.exists()
        # The artifact is a compressed form: smaller, load-bearing content kept.
        artifact_text = artifact.read_text(encoding="utf-8")
        assert artifact_text != source_bytes.decode("utf-8")
        assert "def handler():" in artifact_text
        assert "src/tokenlens/api.py" in artifact_text
        assert "https://example.com/docs" in artifact_text

        # Requirement 7.2: a context_file_compression metrics event is recorded
        # and attributed to this file.
        events_path = metrics_dir / "events.jsonl"
        assert events_path.exists()
        lines = [
            line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["model"] == "context-file:guide.md"
        assert "context_file_compression" in event["stage_savings"]
        # Content that compresses yields a positive attributed saving.
        assert event["stage_savings"]["context_file_compression"] > 0
        assert event["input_tokens_before"] > event["input_tokens_after"]



def test_repeated_leading_filler_is_stripped_once_and_never_empties_file():
    """Regression: compression used to keep shrinking across passes.

    The filler pattern is anchored at the start of the text, so removing one
    phrase could expose the next. Applying it a single time per compression made
    "GREAT QUESTION\\nGREAT QUESTION" collapse to one line on the first pass and
    to an empty file on the second.
    """
    compressor = ContextFileCompressor()

    first = compressor.compress("\nGREAT QUESTION\nGREAT QUESTION\n").text
    assert first == "GREAT QUESTION"
    assert compressor.compress(first).text == first

    # Consecutive distinct filler phrases are peeled in one pass.
    stacked = compressor.compress("Absolutely\nCertainly\nReal content here\n").text
    assert stacked == "Real content here"
    assert compressor.compress(stacked).text == stacked

    # Text that is nothing but filler is preserved rather than deleted.
    assert compressor.compress("Sure\n").text.strip() == "Sure"
