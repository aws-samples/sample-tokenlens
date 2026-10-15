import copy
import string
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from tokenlens import history as history_module
from tokenlens.config import Config, HistoryConfig
from tokenlens.history import ConversationHistoryTrimmer
from tokenlens.pipeline import OptimizationPipeline

_TEXT = string.ascii_letters + string.digits + " .,"


@st.composite
def message_lists(draw, min_turns: int = 0, max_turns: int = 40):
    n = draw(st.integers(min_value=min_turns, max_value=max_turns))
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = draw(st.text(alphabet=_TEXT, min_size=0, max_size=120))
        messages.append({"role": role, "content": content})
    return messages


windows = st.integers(min_value=1, max_value=20)


def _history_config(**overrides) -> Config:
    hist = {"enabled": True, "retained_window": 5}
    hist.update(overrides)
    return Config(
        compression={"enabled": False, "mode": "balanced"},
        output_constraints={"enabled": False, "level": "balanced"},
        prefix={"enabled": False, "min_prefix_tokens": 1024},
        history=hist,
    )


class TestMalformedInput:
    def test_non_list_returned_unchanged(self):
        result = ConversationHistoryTrimmer(3).trim("not a list")
        assert result.applied is False
        assert result.messages == "not a list"

    def test_non_dict_turn_returned_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}, "oops", {"role": "user", "content": "x"}]
        result = ConversationHistoryTrimmer(1).trim(msgs)
        assert result.applied is False
        assert result.messages == msgs


class TestRetainedWindowValidation:
    def test_window_one_accepted(self):
        assert HistoryConfig(retained_window=1).retained_window == 1

    def test_window_zero_rejected(self):
        with pytest.raises(ValidationError):
            HistoryConfig(retained_window=0)

    def test_window_zero_rejected_at_config_load(self):
        with pytest.raises(ValidationError):
            Config(history={"retained_window": 0})


class TestHistoryProperties:
    # **Feature: context-compressors-v2, Property 7: Turns within the retained window survive verbatim**
    @settings(max_examples=100)
    @given(messages=message_lists(), window=windows)
    def test_retained_window_verbatim(self, messages, window):
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)
        keep = min(window, len(original))
        if keep == 0:
            return
        # The last `keep` turns must be unchanged, in the same positions.
        assert result.messages[-keep:] == original[-keep:]

    # **Feature: context-compressors-v2, Property 8: Turns outside the retained window are replaced, not their role**
    @settings(max_examples=100)
    @given(messages=message_lists(min_turns=1), window=windows)
    def test_outside_window_replaced_role_preserved(self, messages, window):
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)
        cutoff = len(original) - window
        # When trimming is not profitable (e.g. out-of-window turns are empty
        # or tiny, so placeholders would not reduce tokens), the trimmer
        # returns the conversation unchanged per the profitability gate
        # (always-on-savings Requirement 2.5). Placeholder replacement is only
        # guaranteed on the profitable path.
        if not result.applied:
            assert result.messages == original
            return
        for i in range(max(0, cutoff)):
            assert result.messages[i]["role"] == original[i]["role"]
            # Content replaced with a shorter placeholder.
            assert result.messages[i]["content"] != original[i]["content"]
            assert result.messages[i]["content"].startswith("[Earlier ")

    # **Feature: context-compressors-v2, Property 9: Trimming preserves turn order**
    @settings(max_examples=100)
    @given(messages=message_lists(), window=windows)
    def test_order_and_length_preserved(self, messages, window):
        original = copy.deepcopy(messages)
        result = ConversationHistoryTrimmer(window).trim(messages)
        assert len(result.messages) == len(original)
        assert [m["role"] for m in result.messages] == [m["role"] for m in original]

    # **Feature: context-compressors-v2, Property 6: Disabled trimming preserves messages**
    @settings(max_examples=100)
    @given(messages=message_lists())
    def test_disabled_preserves_messages(self, messages):
        request = {"model": "m", "messages": copy.deepcopy(messages)}
        pipeline = OptimizationPipeline(_history_config(enabled=False))
        new_request, _ = pipeline.process(request)
        assert new_request["messages"] == messages

    # **Feature: context-compressors-v2, Property 10: Recorded trimming savings reflect an actual reduction**
    @settings(max_examples=100)
    @given(window=windows, extra=st.integers(min_value=1, max_value=20))
    def test_recorded_savings_reflect_reduction(self, window, extra):
        # Build a list guaranteed longer than the window with sizable content.
        n = window + extra
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": "padding content that is clearly worth trimming " * 3}
            for i in range(n)
        ]
        request = {"model": "m", "messages": messages}
        pipeline = OptimizationPipeline(_history_config(retained_window=window))
        _, report = pipeline.process(request)
        saved = report.stage_savings.get("conversation_history_trimming")
        assert saved is not None
        assert saved > 0

    # **Feature: context-compressors-v2, Property 11: Trimmer errors fail open**
    @settings(max_examples=100)
    @given(messages=message_lists(min_turns=1))
    def test_trimmer_errors_fail_open(self, messages):
        def _boom(self, m):
            raise RuntimeError("trimmer exploded")

        original = copy.deepcopy(messages)
        request = {"model": "m", "messages": copy.deepcopy(messages)}
        pipeline = OptimizationPipeline(_history_config(retained_window=1))
        with patch.object(
            history_module.ConversationHistoryTrimmer, "trim", _boom
        ):
            new_request, report = pipeline.process(request)
        # Messages preserved and no trimming savings recorded.
        assert new_request["messages"] == original
        assert "conversation_history_trimming" not in report.stage_savings


class TestDisabledHistorySmoke:
    def test_disabled_history_never_changes_messages(self):
        messages = [
            {"role": "user", "content": f"turn {i} content here"}
            for i in range(12)
        ]
        request = {"model": "claude", "messages": copy.deepcopy(messages)}
        pipeline = OptimizationPipeline(_history_config(enabled=False))
        new_request, _ = pipeline.process(request)
        assert new_request["messages"] == messages
