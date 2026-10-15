"""Property test for always-on-savings Property 10.

Disabled history trimming must leave the conversation's ``messages`` list
identical to the request's original ``messages`` list.
"""

import copy
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.config import Config
from tokenlens.pipeline import OptimizationPipeline

_TEXT = string.ascii_letters + string.digits + " .,"


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 40):
    """Generate 0-40 alternating user/assistant turns with varied content."""
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages


def _history_disabled_config() -> Config:
    """Config with history trimming disabled and all other stages off.

    Isolating history behavior mirrors ``test_history.py``'s ``_history_config``
    so the assertion measures only the disabled-trimming path.
    """
    return Config(
        compression={"enabled": False, "mode": "balanced"},
        output_constraints={"enabled": False, "level": "balanced"},
        prefix={"enabled": False, "min_prefix_tokens": 1024},
        history={"enabled": False, "retained_window": 5},
    )


class TestDisabledHistoryPreservesConversation:
    # Feature: always-on-savings, Property 10: For any request, if history trimming is disabled, the messages list produced by the Optimization Pipeline is identical to the request's original messages list.
    @settings(max_examples=100)
    @given(messages=message_lists())
    def test_disabled_history_preserves_messages(self, messages):
        request = {"model": "m", "messages": copy.deepcopy(messages)}
        pipeline = OptimizationPipeline(_history_disabled_config())
        new_request, _ = pipeline.process(request)
        assert new_request["messages"] == messages
