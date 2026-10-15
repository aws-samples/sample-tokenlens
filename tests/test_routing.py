import string
from dataclasses import asdict

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tokenlens.routing import RoutingDecision, SmartRouter

_MODEL_ALPHABET = string.ascii_lowercase + string.digits + "-"
_TEXT_ALPHABET = string.ascii_letters + string.digits + " .,?"

model_names = st.text(alphabet=_MODEL_ALPHABET, min_size=1, max_size=24)


def _words(min_size: int, max_size: int) -> st.SearchStrategy:
    return st.text(alphabet=_TEXT_ALPHABET, min_size=min_size, max_size=max_size)


@st.composite
def requests(draw, min_messages: int = 0):
    """Generate realistic Anthropic-style request bodies.

    Messages alternate user/assistant roles and vary between short text, long
    text, code-fence blocks, and tool_result blocks so tier assignment is
    exercised across its full range.
    """
    model = draw(model_names)
    n = draw(st.integers(min_value=min_messages, max_value=20))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        kind = draw(st.integers(min_value=0, max_value=3))
        if kind == 0:
            content = draw(_words(0, 40))
        elif kind == 1:
            content = draw(_words(200, 600))
        elif kind == 2:
            content = "```python\n" + draw(_words(0, 120)) + "\n```"
        else:
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": "t",
                    "content": draw(_words(0, 500)),
                }
            ]
        messages.append({"role": role, "content": content})
    return {"model": model, "messages": messages}


_VALID_TIERS = {"trivial", "moderate", "complex"}


class TestRoutingProperties:
    # **Feature: smart-routing, Property 1: Classification is always a valid tier**
    @settings(max_examples=100)
    @given(request=requests())
    def test_classification_is_always_valid_tier(self, request):
        router = SmartRouter(tier_models={}, confidence_threshold=0.6)
        decision = router.classify(request)
        assert decision.tier in _VALID_TIERS

    # **Feature: smart-routing, Property 2: Classification is deterministic**
    @settings(max_examples=100)
    @given(request=requests())
    def test_classification_is_deterministic(self, request):
        router = SmartRouter(tier_models={}, confidence_threshold=0.6)
        first = router.classify(request)
        second = router.classify(request)
        assert first.tier == second.tier
        assert first.confidence == second.confidence

    # **Feature: smart-routing, Property 3: Low confidence forces the complex tier**
    @settings(max_examples=100)
    @given(request=requests(), threshold=st.floats(min_value=0.0, max_value=1.0))
    def test_low_confidence_forces_complex(self, request, threshold):
        router = SmartRouter(tier_models={}, confidence_threshold=threshold)
        decision = router.classify(request)
        if decision.confidence < threshold:
            assert decision.tier == "complex"

    # **Feature: smart-routing, Property 4: Enabled routing rewrites the model for mapped trivial/moderate tiers**
    @settings(max_examples=100)
    @given(request=requests(min_messages=1))
    def test_enabled_routing_rewrites_mapped_tiers(self, request):
        tier_models = {
            "trivial": "trivial-cheap-model",
            "moderate": "moderate-cheap-model",
        }
        assume(request["model"] not in tier_models.values())
        # Threshold 0.0 lets trivial/moderate classifications survive so the
        # rewrite path is exercised.
        router = SmartRouter(tier_models=tier_models, confidence_threshold=0.0)
        decision = router.classify(request)
        if decision.tier in ("trivial", "moderate"):
            assert decision.routed_model == tier_models[decision.tier]
            assert decision.applied is True

    # **Feature: smart-routing, Property 5: Complex tier or disabled routing preserves the original model**
    @settings(max_examples=100)
    @given(request=requests())
    def test_complex_tier_preserves_original_model(self, request):
        router = SmartRouter(
            tier_models={"trivial": "cheap", "moderate": "mid"},
            confidence_threshold=0.6,
        )
        decision = router.classify(request)
        if decision.tier == "complex":
            assert decision.routed_model == decision.original_model
            assert decision.applied is False

    # **Feature: smart-routing, Property 6: Missing tier mapping preserves the original model**
    @settings(max_examples=100)
    @given(request=requests(), threshold=st.floats(min_value=0.0, max_value=1.0))
    def test_missing_tier_mapping_preserves_original_model(self, request, threshold):
        # No tier models configured: no tier may be rewritten.
        router = SmartRouter(tier_models={}, confidence_threshold=threshold)
        decision = router.classify(request)
        assert decision.routed_model == decision.original_model
        assert decision.applied is False


class TestMalformedRequests:
    """Unit tests: malformed input degrades to complex/0.0 without raising."""

    def _router(self) -> SmartRouter:
        return SmartRouter(
            tier_models={"trivial": "cheap", "moderate": "mid"},
            confidence_threshold=0.6,
        )

    def test_missing_model_key(self):
        decision = self._router().classify({"messages": [{"role": "user", "content": "hi"}]})
        assert decision.tier == "complex"
        assert decision.confidence == 0.0
        assert decision.applied is False

    def test_non_string_model(self):
        decision = self._router().classify(
            {"model": 123, "messages": [{"role": "user", "content": "hi"}]}
        )
        assert decision.tier == "complex"
        assert decision.confidence == 0.0
        assert decision.applied is False

    def test_empty_messages(self):
        decision = self._router().classify({"model": "m", "messages": []})
        assert decision.tier == "complex"
        assert decision.confidence == 0.0
        assert decision.applied is False

    def test_non_dict_request(self):
        decision = self._router().classify("not a dict")
        assert decision.tier == "complex"
        assert decision.confidence == 0.0
        assert decision.applied is False


import json as _json
from unittest.mock import patch

from tokenlens.config import Config
from tokenlens.pipeline import OptimizationPipeline
from tokenlens import routing as routing_module


def _routing_config(**overrides) -> Config:
    routing = {
        "enabled": True,
        "trivial_model": "trivial-cheap",
        "moderate_model": "moderate-cheap",
        "confidence_threshold": 0.0,
    }
    routing.update(overrides)
    return Config(
        compression={"enabled": True, "mode": "balanced"},
        output_constraints={"enabled": False, "level": "balanced"},
        prefix={"enabled": False, "min_prefix_tokens": 1024},
        routing=routing,
    )


class TestPipelineRoutingProperties:
    # **Feature: smart-routing, Property 7: Pipeline routing decision matches standalone classification**
    @settings(max_examples=100)
    @given(request=requests())
    def test_pipeline_decision_matches_standalone(self, request):
        config = _routing_config()
        pipeline = OptimizationPipeline(config)
        _, report = pipeline.process(request)

        standalone = SmartRouter(
            tier_models={
                "trivial": config.routing.trivial_model,
                "moderate": config.routing.moderate_model,
            },
            confidence_threshold=config.routing.confidence_threshold,
        ).classify(request)

        assert report.routing_decision == standalone

    # **Feature: smart-routing, Property 9: Classifier errors fail open**
    @settings(max_examples=100)
    @given(request=requests())
    def test_classifier_errors_fail_open(self, request):
        def _boom(self, req):
            raise RuntimeError("classifier exploded")

        original_model = request.get("model")
        # A large JSON tool_result so compression measurably applies.
        big = _json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)])
        request = dict(request)
        request["messages"] = list(request.get("messages", [])) + [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": big}
            ]}
        ]

        pipeline = OptimizationPipeline(_routing_config())
        with patch.object(routing_module.SmartRouter, "classify", _boom):
            new_request, report = pipeline.process(request)

        # Model preserved and no routing decision recorded.
        assert new_request.get("model") == original_model
        assert report.routing_decision is None
        # Remaining stages still ran: compression shrank the big tool result.
        assert report.input_tokens_saved > 0


class TestDisabledRoutingSmoke:
    def test_disabled_routing_never_changes_model(self):
        request = {
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "What is 2 + 2?"}],
        }
        config = _routing_config(enabled=False)
        new_request, report = OptimizationPipeline(config).process(request)
        assert new_request["model"] == "claude-3-5-sonnet"
        assert report.routing_decision is None


from tokenlens.metrics import MetricsRecorder


class TestRoutingMetricsProperties:
    # **Feature: smart-routing, Property 8: Recorded routing metrics match the routing decision**
    @settings(max_examples=100)
    @given(request=requests(min_messages=1))
    def test_recorded_metrics_match_decision(self, request, tmp_path_factory):
        tier_models = {"trivial": "trivial-cheap", "moderate": "moderate-cheap"}
        assume(request["model"] not in tier_models.values())
        router = SmartRouter(tier_models=tier_models, confidence_threshold=0.0)
        decision = router.classify(request)
        assume(decision.applied)

        directory = tmp_path_factory.mktemp("metrics")
        recorder = MetricsRecorder(str(directory), enabled=True)
        event = recorder.make_event(
            model=decision.original_model,
            input_tokens_before=100,
            input_tokens_after=100,
            stage_savings={},
            routing_tier=decision.tier,
            routed_model=decision.routed_model,
        )
        assert event.routing_tier == decision.tier
        assert event.routed_model == decision.routed_model


class TestStatsPerTierCounts:
    def test_stats_counts_events_by_tier(self, tmp_path):
        recorder = MetricsRecorder(str(tmp_path / "m"), enabled=True)
        tiers = ["trivial", "trivial", "moderate", "complex", "complex", "complex"]
        for tier in tiers:
            recorder.record(
                recorder.make_event(
                    "claude", 100, 80, {}, routing_tier=tier, routed_model="x"
                )
            )
        counts = recorder.stats()["routing_by_tier"]
        assert counts == {"trivial": 2, "moderate": 1, "complex": 3}
