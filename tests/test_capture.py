import json

from tokenlens.capture import CaptureRecorder, redact

_FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


class TestRedact:
    def test_redacts_aws_key(self):
        assert "AKIA" not in redact(f"key {_FAKE_AWS_KEY} here")

    def test_redacts_sk_key(self):
        assert "sk-" not in redact("token sk-abcdef1234567890abcdef")

    def test_redacts_bearer(self):
        assert "REDACTED_TOKEN" in redact("Authorization: Bearer abc.def.ghi")

    def test_redacts_email(self):
        assert "@" not in redact("contact me at jane.doe@example.com please")

    def test_redacts_key_value(self):
        out = redact('{"api_key": "supersecretvalue"}')
        assert "supersecretvalue" not in out


class TestCaptureRecorder:
    def test_disabled_writes_nothing(self, tmp_path):
        rec = CaptureRecorder(str(tmp_path / "cap"), enabled=False)
        rec.record("mcp:compress", "json", 1000, 400, True)
        assert not (tmp_path / "cap" / "captures.jsonl").exists()

    def test_records_measurement_only_by_default(self, tmp_path):
        rec = CaptureRecorder(str(tmp_path / "cap"), enabled=True)
        rec.record("mcp:compress", "json", 1000, 400, True, content="secret content")
        line = (tmp_path / "cap" / "captures.jsonl").read_text().strip()
        data = json.loads(line)
        assert data["tokens_saved"] == 600
        assert data["content_type"] == "json"
        assert data["source"] == "mcp:compress"
        # Content must NOT be stored by default.
        assert "content_sample" not in data

    def test_stores_redacted_content_when_enabled(self, tmp_path):
        rec = CaptureRecorder(str(tmp_path / "cap"), enabled=True, store_content=True)
        rec.record(
            "mcp:compress",
            "json",
            1000,
            400,
            True,
            content=f"email jane@example.com and {_FAKE_AWS_KEY}",
        )
        data = json.loads((tmp_path / "cap" / "captures.jsonl").read_text().strip())
        assert "content_sample" in data
        assert "jane@example.com" not in data["content_sample"]
        assert "AKIA" not in data["content_sample"]

    def test_appends_multiple_records(self, tmp_path):
        rec = CaptureRecorder(str(tmp_path / "cap"), enabled=True)
        rec.record("mcp:compress", "json", 1000, 400, True)
        rec.record("mcp:compress", "logs", 500, 100, True)
        lines = (tmp_path / "cap" / "captures.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
