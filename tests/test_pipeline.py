import json

from tokenlens.config import Config
from tokenlens.metrics import MetricsRecorder
from tokenlens.pipeline import OptimizationPipeline


def _config(**overrides) -> Config:
    data = {
        "compression": {"enabled": True, "mode": "balanced"},
        "output_constraints": {"enabled": False, "level": "balanced"},
        "prefix": {"enabled": False, "min_prefix_tokens": 1024},
    }
    data.update(overrides)
    return Config(**data)


def _big_json_tool_result() -> str:
    return json.dumps([{"id": i, "name": f"row-{i}", "value": i * 7} for i in range(200)])


class TestContextCompression:
    def test_compresses_tool_result_block(self):
        request = {
            "model": "claude-3-5-sonnet",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": _big_json_tool_result(),
                        }
                    ],
                }
            ],
        }
        pipeline = OptimizationPipeline(_config())
        new_request, report = pipeline.process(request)

        assert report.input_tokens_saved > 0
        assert report.stage_savings["context_compression"] > 0
        # Original request must not be mutated.
        assert len(request["messages"][0]["content"][0]["content"]) > len(
            new_request["messages"][0]["content"][0]["content"]
        )

    def test_compresses_string_content(self):
        request = {
            "model": "m",
            "messages": [{"role": "user", "content": _big_json_tool_result()}],
        }
        pipeline = OptimizationPipeline(_config())
        _, report = pipeline.process(request)
        assert report.input_tokens_saved > 0

    def test_small_content_untouched(self):
        request = {
            "model": "m",
            "messages": [{"role": "user", "content": "hello there"}],
        }
        pipeline = OptimizationPipeline(_config())
        new_request, report = pipeline.process(request)
        assert report.input_tokens_saved == 0
        assert new_request["messages"][0]["content"] == "hello there"


class TestOutputConstraints:
    def test_injects_into_empty_system(self):
        request = {"model": "m", "messages": []}
        pipeline = OptimizationPipeline(
            _config(output_constraints={"enabled": True, "level": "balanced"})
        )
        new_request, _ = pipeline.process(request)
        assert "concise" in new_request["system"].lower()

    def test_appends_to_string_system(self):
        request = {"model": "m", "system": "You are helpful.", "messages": []}
        pipeline = OptimizationPipeline(
            _config(output_constraints={"enabled": True, "level": "aggressive"})
        )
        new_request, _ = pipeline.process(request)
        assert new_request["system"].startswith("You are helpful.")
        assert len(new_request["system"]) > len("You are helpful.")

    def test_appends_block_to_list_system(self):
        request = {
            "model": "m",
            "system": [{"type": "text", "text": "base"}],
            "messages": [],
        }
        pipeline = OptimizationPipeline(
            _config(output_constraints={"enabled": True, "level": "minimal"})
        )
        new_request, _ = pipeline.process(request)
        assert len(new_request["system"]) == 2


class TestPrefixStabilization:
    def test_tags_large_string_system(self):
        big_system = "You are an expert assistant. " * 200  # well over 1024 tokens
        request = {"model": "m", "system": big_system, "messages": []}
        pipeline = OptimizationPipeline(
            _config(prefix={"enabled": True, "min_prefix_tokens": 1024})
        )
        new_request, _ = pipeline.process(request)
        assert isinstance(new_request["system"], list)
        assert new_request["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_ignores_small_system(self):
        request = {"model": "m", "system": "short system", "messages": []}
        pipeline = OptimizationPipeline(
            _config(prefix={"enabled": True, "min_prefix_tokens": 1024})
        )
        new_request, _ = pipeline.process(request)
        assert new_request["system"] == "short system"


class TestMetrics:
    def test_record_and_stats(self, tmp_path):
        recorder = MetricsRecorder(str(tmp_path / "m"), enabled=True)
        recorder.record(
            recorder.make_event("claude", 1000, 400, {"context_compression": 600})
        )
        recorder.record(
            recorder.make_event("claude", 500, 500, {})
        )
        stats = recorder.stats()
        assert stats["requests_optimized"] == 2
        assert stats["input_tokens_before"] == 1500
        assert stats["input_tokens_after"] == 900
        assert stats["input_tokens_saved"] == 600
        assert stats["savings_by_stage"]["context_compression"] == 600
        assert stats["savings_percent"] == 40.0

    def test_disabled_recorder_writes_nothing(self, tmp_path):
        recorder = MetricsRecorder(str(tmp_path / "m"), enabled=False)
        recorder.record(recorder.make_event("claude", 100, 50, {}))
        assert recorder.stats()["requests_optimized"] == 0
