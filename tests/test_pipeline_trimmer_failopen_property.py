"""Property-based test for pipeline-level trimmer fail-open (Property 7).

When the conversation history trimmer raises, the Optimization Pipeline must
leave the request's messages intact AND still run every other enabled stage.
This is distinct from the function-level fail-open in test_history.py: here we
assert the *pipeline* both preserves history and keeps applying other stages
(compression), not just that messages survive.
"""

import copy
import string
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens import history as history_module
from tokenlens.config import Config
from tokenlens.pipeline import OptimizationPipeline

_TEXT = string.ascii_letters + string.digits + " .,"

# A unified diff whose context run is far longer than any compressor run
# threshold, so the compression stage reliably reduces tokens. This gives us a
# concrete "other enabled stage" to prove still runs when the trimmer explodes.
_CONTEXT_RUN = "\n".join(f" ctx line {i} kept as context" for i in range(40))
_COMPRESSIBLE_DIFF = (
    "diff --git a/f.py b/f.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,40 +1,40 @@\n"
    f"{_CONTEXT_RUN}\n"
    "-old value here\n"
    "+new value here\n"
)


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 20):
    """Generate alternating user/assistant turns of varying content sizes."""
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages


def _config(history_enabled: bool) -> Config:
    """History + compression on (except history toggled), other stages off."""
    return Config(
        compression={"enabled": True, "mode": "balanced"},
        output_constraints={"enabled": False, "level": "balanced"},
        prefix={"enabled": False, "min_prefix_tokens": 1024},
        history={"enabled": history_enabled, "retained_window": 1},
        routing={"enabled": False},
    )


def _boom(self, messages):
    raise RuntimeError("trimmer exploded")


class TestPipelineTrimmerFailOpen:
    # Feature: always-on-savings, Property 7: For any request, if ConversationHistoryTrimmer.trim raises, the Optimization Pipeline preserves the request's original messages list unchanged and still applies every other enabled pipeline stage.
    # **Validates: Requirements 2.8, 6.2**
    @settings(max_examples=100)
    @given(messages=message_lists())
    def test_trimmer_error_preserves_history_and_runs_other_stages(self, messages):
        # Append a reliably-compressible turn so the compression stage (the
        # "other enabled stage") has something to reduce. This is the most
        # recent turn, so a working trimmer with window=1 would keep it verbatim
        # and the comparison below stays meaningful.
        full_messages = messages + [{"role": "user", "content": _COMPRESSIBLE_DIFF}]

        # Baseline: same pipeline with the trimmer stage disabled. This is
        # exactly "history intact + every other enabled stage applied".
        baseline_request = {"model": "m", "messages": copy.deepcopy(full_messages)}
        baseline_pipeline = OptimizationPipeline(_config(history_enabled=False))
        baseline_new, baseline_report = baseline_pipeline.process(baseline_request)

        # Trimmer enabled but forced to raise on every call.
        request = {"model": "m", "messages": copy.deepcopy(full_messages)}
        pipeline = OptimizationPipeline(_config(history_enabled=True))
        with patch.object(
            history_module.ConversationHistoryTrimmer, "trim", _boom
        ):
            new_request, report = pipeline.process(request)

        # 1) History preserved: the trimmer's failure discards/corrupts nothing.
        #    The messages are identical to the trimmer-disabled run, so no turn
        #    was removed, reordered, or placeholder-replaced by the failed trim.
        assert new_request["messages"] == baseline_new["messages"]
        assert [m["role"] for m in new_request["messages"]] == [
            m["role"] for m in full_messages
        ]
        assert len(new_request["messages"]) == len(full_messages)
        # No trimmer placeholder leaked into any turn.
        for turn in new_request["messages"]:
            assert not turn["content"].startswith("[Earlier ")
        # No trimming savings recorded, since the trim never succeeded.
        assert "conversation_history_trimming" not in report.stage_savings

        # 2) Every other enabled stage still applied: compression ran and
        #    recorded the same savings it records when the trimmer is absent.
        assert "context_compression" in report.stage_savings
        assert report.stage_savings["context_compression"] > 0
        assert (
            report.stage_savings["context_compression"]
            == baseline_report.stage_savings["context_compression"]
        )
