"""Fail-open behavior for MetricsRecorder when its storage dir can't be created."""

from pathlib import Path

from tokenlens.metrics import MetricsRecorder


def test_metrics_recorder_fails_open_when_mkdir_raises(tmp_path, monkeypatch):
    def _raise_mkdir(self, *args, **kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)

    recorder = MetricsRecorder(str(tmp_path / "metrics"), enabled=True)

    assert recorder.enabled is False

    event = recorder.make_event(
        model="test-model",
        input_tokens_before=100,
        input_tokens_after=50,
        stage_savings={},
    )
    recorder.record(event)

    assert not recorder.events_path.exists()
