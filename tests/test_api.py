import json

from tokenlens.api import compress_text, optimize_request
from tokenlens.config import Config


def _big_json() -> str:
    return json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)])


class TestCompressText:
    def test_compresses_and_reports(self):
        result = compress_text(_big_json())
        assert result["applied"]
        assert result["compressor"] == "json"
        assert result["tokens_saved"] > 0
        assert result["compressed_tokens"] < result["original_tokens"]

    def test_plain_text_not_applied(self):
        result = compress_text("hello there")
        assert not result["applied"]
        assert result["text"] == "hello there"
        assert result["tokens_saved"] == 0

    def test_result_is_json_serializable(self):
        # The contract must be plain JSON types for cross-adapter use.
        json.dumps(compress_text(_big_json()))


class TestOptimizeRequest:
    def test_optimizes_request_body(self):
        request = {
            "model": "claude-3-5-sonnet",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": _big_json()}
                    ],
                }
            ],
        }
        result = optimize_request(request)
        assert result["report"]["input_tokens_saved"] > 0
        assert result["report"]["model"] == "claude-3-5-sonnet"
        # Returns a usable request body.
        assert "messages" in result["request"]
        json.dumps(result)  # serializable

    def test_respects_disabled_compression(self):
        config = Config(compression={"enabled": False})
        request = {
            "model": "m",
            "messages": [{"role": "user", "content": _big_json()}],
        }
        result = optimize_request(request, config)
        assert result["report"]["input_tokens_saved"] == 0

    def test_report_includes_routing_field(self):
        # A short question with a low threshold routes to the trivial model.
        config = Config(
            compression={"enabled": False},
            routing={
                "enabled": True,
                "trivial_model": "claude-haiku",
                "moderate_model": "claude-haiku",
                "confidence_threshold": 0.0,
            },
        )
        request = {
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "What is 2 + 2?"}],
        }
        result = optimize_request(request, config)
        routing = result["report"]["routing"]
        assert routing is not None
        assert routing["original_model"] == "claude-3-5-sonnet"
        assert routing["routed_model"] == result["request"]["model"]
        assert routing["applied"] is True
        assert routing["tier"] in ("trivial", "moderate")
