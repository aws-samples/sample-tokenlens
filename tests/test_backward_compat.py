"""Integration test for direct-API backward compatibility.

Always-On Savings is additive: enabling it must not change how the existing
public entry points behave when they are called directly. This test pins the
contracts of three direct APIs and the pipeline's input/output shape.

- ``api.compress_context_file`` — profitability-gated, fail-open, preserves
  load-bearing content, returns the documented result dict.
- ``ConversationHistoryTrimmer.trim`` — unchanged for short conversations,
  placeholder-replaces older turns while preserving role and position for long
  ones, ``applied`` reflects real savings.
- ``tokenlens_read_file`` (compressed read path) — above-threshold compresses
  when profitable, below-threshold returns the original content unchanged
  (applied false), non-UTF-8 returns the error result and never raises.
- ``OptimizationPipeline.process`` — the request in/out contract is unchanged:
  it does not mutate the caller's object and returns a new request dict plus a
  report, preserving structure when every stage is disabled.

Validates: Requirements 9.1, 9.3
"""

import asyncio
import json

from tokenlens.adapters.mcp_server import create_mcp_server
from tokenlens.api import compress_context_file
from tokenlens.config import Config
from tokenlens.history import ConversationHistoryTrimmer, HistoryTrimResult
from tokenlens.pipeline import OptimizationPipeline
from tokenlens.tokens import count_tokens


def _steering_doc() -> str:
    """A context-file document that compresses (repeated lines + filler) while
    carrying load-bearing spans that must survive verbatim."""
    body = "\n".join(
        [
            "# Steering",
            "",
            "",
            "This rule is important.   ",
            "This rule is important.   ",
            "This rule is important.   ",
            "",
            "Run `pip install tokenlens` to install.",
            "See https://example.com/docs for details.",
            "Edit the file at src/tokenlens/config.py to change defaults.",
            "```python",
            "def handler(event, context):",
            "    return {'ok': True}",
            "```",
            "Identifier: AlwaysOnConfig_read_routing_threshold_value",
        ]
    )
    # Repeat the whitespace-heavy prose so compression is reliably profitable.
    return body + ("\n\n" + "duplicate paragraph line\n" * 40)


LOAD_BEARING_SPANS = [
    "`pip install tokenlens`",
    "https://example.com/docs",
    "src/tokenlens/config.py",
    "def handler(event, context):",
    "return {'ok': True}",
    "AlwaysOnConfig_read_routing_threshold_value",
]


class TestCompressContextFileDirect:
    """api.compress_context_file behaves as before when called directly (9.3)."""

    def test_result_dict_contract(self):
        result = compress_context_file(_steering_doc())
        # The documented result shape is unchanged.
        assert set(result) == {
            "text",
            "original_tokens",
            "compressed_tokens",
            "tokens_saved",
            "applied",
        }
        # JSON-serializable plain types for cross-adapter use.
        json.dumps(result)

    def test_profitable_compression_reduces_and_preserves_load_bearing(self):
        text = _steering_doc()
        result = compress_context_file(text)

        assert result["applied"] is True
        assert result["compressed_tokens"] < result["original_tokens"]
        assert result["tokens_saved"] == (
            result["original_tokens"] - result["compressed_tokens"]
        )
        # Load-bearing content survives verbatim.
        for span in LOAD_BEARING_SPANS:
            assert span in result["text"]

    def test_unprofitable_input_returned_unchanged(self):
        # Short prose that does not shrink is returned unchanged, applied false.
        text = "hello there"
        result = compress_context_file(text)
        assert result["applied"] is False
        assert result["text"] == text
        assert result["tokens_saved"] == 0

    def test_non_string_input_fails_open(self):
        # Fail-open: a bad type must not raise; original is returned unchanged.
        result = compress_context_file(None)  # type: ignore[arg-type]
        assert result["applied"] is False
        assert result["text"] is None
        assert result["tokens_saved"] == 0


class TestConversationHistoryTrimmerDirect:
    """ConversationHistoryTrimmer behaves as before when called directly (9.3)."""

    def test_short_conversation_returned_unchanged(self):
        messages = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
        result = ConversationHistoryTrimmer(retained_window=10).trim(messages)
        assert isinstance(result, HistoryTrimResult)
        assert result.applied is False
        assert result.tokens_saved == 0
        assert result.messages is messages

    def test_long_conversation_trims_older_turns_in_place(self):
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 20}
            for i in range(8)
        ]
        window = 3
        result = ConversationHistoryTrimmer(retained_window=window).trim(messages)

        assert result.applied is True
        assert result.tokens_saved > 0
        # No turn removed or reordered.
        assert len(result.messages) == len(messages)
        # The most recent `window` turns are preserved verbatim in position.
        for original, trimmed in zip(messages[-window:], result.messages[-window:]):
            assert trimmed == original
        # Older turns keep their role and position but get a placeholder body.
        cutoff = len(messages) - window
        for index in range(cutoff):
            assert result.messages[index]["role"] == messages[index]["role"]
            assert result.messages[index]["content"] != messages[index]["content"]
            assert "omitted" in result.messages[index]["content"]

    def test_malformed_input_fails_open(self):
        # Non-list and list-with-non-dict inputs are returned unchanged.
        assert ConversationHistoryTrimmer(5).trim("not a list").applied is False  # type: ignore[arg-type]
        bad = ["not", "dicts"]
        result = ConversationHistoryTrimmer(1).trim(bad)
        assert result.applied is False
        assert result.messages is bad


def _read_file_result(config: Config, path: str) -> dict:
    """Call the tokenlens_read_file MCP tool and return its structured dict."""
    server = create_mcp_server(config)
    call_result = asyncio.run(server.call_tool("tokenlens_read_file", {"path": path}))
    if isinstance(call_result, tuple):
        return call_result[1]
    return call_result


class TestTokenlensReadFileDirect:
    """tokenlens_read_file behaves as before when called directly (9.3).

    The above-threshold and error contracts are unchanged; the below-threshold
    short-circuit returns the original content unchanged, which previously
    returned an equivalent unchanged result because compression was not
    profitable on small files.
    """

    def test_above_threshold_compresses_when_profitable(self, tmp_path):
        # Large, repetitive JSON reliably compresses.
        payload = json.dumps([{"id": i, "name": f"row-{i}"} for i in range(300)])
        target = tmp_path / "rows.json"
        target.write_text(payload, encoding="utf-8")
        tokens = count_tokens(payload)

        config = Config(
            metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
            always_on={"read_routing_threshold": 0},
        )
        result = _read_file_result(config, str(target))

        assert result["error"] is None
        assert result["path"] == str(target)
        assert result["original_tokens"] == tokens
        assert result["applied"] is True
        assert result["compressed_tokens"] < result["original_tokens"]
        assert result["tokens_saved"] > 0

    def test_below_threshold_returns_original_unchanged(self, tmp_path):
        content = "small file contents\n"
        target = tmp_path / "small.txt"
        target.write_text(content, encoding="utf-8")
        tokens = count_tokens(content)

        # Threshold above the file's token count -> pass-through unchanged.
        config = Config(
            metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
            always_on={"read_routing_threshold": tokens + 1000},
        )
        result = _read_file_result(config, str(target))

        assert result["applied"] is False
        assert result["text"] == content
        assert result["original_tokens"] == tokens
        assert result["compressed_tokens"] == tokens
        assert result["tokens_saved"] == 0
        assert result["error"] is None

    def test_non_utf8_returns_error_result_and_does_not_raise(self, tmp_path):
        target = tmp_path / "binary.bin"
        target.write_bytes(b"\xff\xfe\x00\x01\x80\x81")

        config = Config(
            metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
        )
        result = _read_file_result(config, str(target))

        assert result["applied"] is False
        assert result["error"] is not None
        assert result["compressor"] == "none"

    def test_missing_file_returns_error_result(self, tmp_path):
        config = Config(metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")})
        result = _read_file_result(config, str(tmp_path / "nope.txt"))
        assert result["applied"] is False
        assert result["error"] == "not a file"


class TestPipelineContractUnchanged:
    """OptimizationPipeline input/output contract is preserved (9.1)."""

    def test_process_returns_new_request_and_report_without_mutating_caller(self):
        request = {
            "model": "claude-3-5-sonnet",
            "system": "You are helpful.",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        before = json.loads(json.dumps(request))

        pipeline = OptimizationPipeline(Config())
        new_request, report = pipeline.process(request)

        # Caller's object is never mutated.
        assert request == before
        # Output is a distinct request dict plus a report.
        assert new_request is not request
        assert isinstance(new_request, dict)
        # The request shape is preserved: same top-level keys, same message roles.
        assert set(new_request) >= {"model", "system", "messages"}
        assert [m["role"] for m in new_request["messages"]] == [
            m["role"] for m in request["messages"]
        ]
        # Report exposes the documented accounting fields.
        assert report.input_tokens_before >= report.input_tokens_after
        assert isinstance(report.stage_savings, dict)
        assert report.input_tokens_saved == max(
            0, report.input_tokens_before - report.input_tokens_after
        )

    def test_all_stages_disabled_preserves_messages_identically(self):
        request = {
            "model": "m",
            "messages": [
                {"role": "user", "content": "keep this exactly"},
                {"role": "assistant", "content": "and this too"},
            ],
        }
        config = Config(
            routing={"enabled": False},
            history={"enabled": False},
            compression={"enabled": False},
            output_constraints={"enabled": False},
            prefix={"enabled": False},
        )
        new_request, _ = OptimizationPipeline(config).process(request)

        # With every stage off, the messages list is identical to the input.
        assert new_request["messages"] == request["messages"]
