"""Integration test for aggregate metrics across the three always-on stage keys.

The three Always-On Lever stage-savings keys are:
- context_file_compression      (Lever 1: steering-file compression)
- conversation_history_trimming (Lever 2: history trimming)
- context_compression           (Lever 3: file-read routing / compressed read path)

Validates: Requirements 7.1, 7.3
"""

from tokenlens.metrics import MetricsRecorder

# The three Always-On Lever stage-savings keys aggregated by stats().
STAGE_CONTEXT_FILE = "context_file_compression"
STAGE_HISTORY_TRIM = "conversation_history_trimming"
STAGE_CONTEXT_COMPRESSION = "context_compression"


def _record(recorder: MetricsRecorder, stage: str, before: int, after: int) -> None:
    """Record a single optimization event attributed to one stage."""
    event = recorder.make_event(
        model="test-model",
        input_tokens_before=before,
        input_tokens_after=after,
        stage_savings={stage: before - after},
    )
    recorder.record(event)


class TestAggregateMetricsAcrossStages:
    def test_stats_aggregates_all_three_stage_keys(self, tmp_path):
        """stats() sums input_tokens_saved and carries all three stage keys.

        Seed a mix of the three always-on stage events, then assert the
        aggregate total equals the sum of per-event savings and that
        savings_by_stage carries every stage key with its own total.
        """
        recorder = MetricsRecorder(str(tmp_path / "metrics"), enabled=True)

        # Lever 1: two context_file_compression events -> saved 800 + 300 = 1100
        _record(recorder, STAGE_CONTEXT_FILE, before=1000, after=200)
        _record(recorder, STAGE_CONTEXT_FILE, before=500, after=200)

        # Lever 2: one conversation_history_trimming event -> saved 700
        _record(recorder, STAGE_HISTORY_TRIM, before=900, after=200)

        # Lever 3: one context_compression event -> saved 400
        _record(recorder, STAGE_CONTEXT_COMPRESSION, before=600, after=200)

        expected_by_stage = {
            STAGE_CONTEXT_FILE: 1100,
            STAGE_HISTORY_TRIM: 700,
            STAGE_CONTEXT_COMPRESSION: 400,
        }
        expected_total = sum(expected_by_stage.values())  # 2200

        stats = recorder.stats()

        # Requirement 7.3: total token reduction accumulated across all events.
        assert stats["input_tokens_saved"] == expected_total

        # Requirement 7.2: savings attributed to the lever that produced them.
        savings_by_stage = stats["savings_by_stage"]
        assert set(savings_by_stage) == set(expected_by_stage)
        assert savings_by_stage == expected_by_stage

        # The aggregate before/after totals are internally consistent with the sum.
        assert (
            stats["input_tokens_before"] - stats["input_tokens_after"]
            == expected_total
        )
        assert stats["requests_optimized"] == 4

    def test_savings_by_stage_total_equals_input_tokens_saved(self, tmp_path):
        """The sum of per-stage savings equals the reported total (7.3)."""
        recorder = MetricsRecorder(str(tmp_path / "metrics"), enabled=True)

        _record(recorder, STAGE_CONTEXT_FILE, before=1200, after=400)
        _record(recorder, STAGE_HISTORY_TRIM, before=800, after=300)
        _record(recorder, STAGE_CONTEXT_COMPRESSION, before=450, after=50)

        stats = recorder.stats()

        assert sum(stats["savings_by_stage"].values()) == stats["input_tokens_saved"]
        # All three keys present.
        assert set(stats["savings_by_stage"]) == {
            STAGE_CONTEXT_FILE,
            STAGE_HISTORY_TRIM,
            STAGE_CONTEXT_COMPRESSION,
        }
