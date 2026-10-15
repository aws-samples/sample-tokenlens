import json

from tokenlens.capture import CaptureRecorder
from tokenlens.report import build_report


def _seed_captures(tmp_path):
    rec = CaptureRecorder(str(tmp_path / "cap"), enabled=True)
    rec.record("mcp:compress", "json", 1000, 200, True)
    rec.record("mcp:compress", "logs", 800, 100, True)
    rec.record("mcp:read_file", "code", 600, 300, True)
    rec.record("mcp:compress", "none", 50, 50, False)  # not applied
    return tmp_path / "cap"


class TestBuildReport:
    def test_totals(self, tmp_path):
        cap_dir = _seed_captures(tmp_path)
        report = build_report([cap_dir])
        d = report.to_dict()
        assert d["total"]["count"] == 4
        assert d["total"]["applied"] == 3
        assert d["total"]["tokens_before"] == 2450
        assert d["total"]["tokens_after"] == 650
        assert d["total"]["tokens_saved"] == 1800
        assert d["total"]["apply_rate"] == 75.0

    def test_by_content_type(self, tmp_path):
        cap_dir = _seed_captures(tmp_path)
        d = build_report([cap_dir]).to_dict()
        assert d["by_content_type"]["json"]["tokens_saved"] == 800
        assert d["by_content_type"]["logs"]["tokens_saved"] == 700
        assert d["by_content_type"]["code"]["tokens_saved"] == 300

    def test_by_source(self, tmp_path):
        cap_dir = _seed_captures(tmp_path)
        d = build_report([cap_dir]).to_dict()
        assert d["by_source"]["mcp:compress"]["count"] == 3
        assert d["by_source"]["mcp:read_file"]["count"] == 1

    def test_distribution(self, tmp_path):
        cap_dir = _seed_captures(tmp_path)
        d = build_report([cap_dir]).to_dict()
        dist = d["distribution_percent"]
        assert dist["min"] == 0.0  # the not-applied record
        assert dist["max"] == 87.5  # logs: 700/800

    def test_markdown_output(self, tmp_path):
        cap_dir = _seed_captures(tmp_path)
        md = build_report([cap_dir]).to_markdown()
        assert "# TokenLens Savings Report" in md
        assert "Savings by content type" in md
        assert "json" in md

    def test_empty_report(self, tmp_path):
        (tmp_path / "empty").mkdir()
        report = build_report([tmp_path / "empty"])
        assert report.total.count == 0
        # to_dict/markdown must not raise on empty input.
        report.to_markdown()
        assert report.to_dict()["total"]["count"] == 0

    def test_skips_malformed_lines(self, tmp_path):
        cap = tmp_path / "captures.jsonl"
        cap.write_text(
            json.dumps({"original_tokens": 100, "compressed_tokens": 40, "applied": True,
                        "content_type": "json", "source": "mcp:compress"})
            + "\nthis is not json\n"
        )
        report = build_report([cap])
        assert report.total.count == 1
