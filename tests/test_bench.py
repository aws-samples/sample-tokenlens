import json

from tokenlens.bench import default_fixtures_dir, run_bench
from tokenlens.config import Config


def _write_request(path, tool_result_content):
    body = {
        "model": "claude-3-5-sonnet",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": tool_result_content,
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def test_run_bench_reports_savings(tmp_path):
    big = json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)])
    _write_request(tmp_path / "a.json", big)
    _write_request(tmp_path / "b.json", big)

    report = run_bench([tmp_path])
    assert len(report.files) == 2
    assert report.total_saved > 0
    assert report.savings_percent > 0
    assert report.total_after < report.total_before


def test_run_bench_records_parse_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")
    report = run_bench([bad])
    assert len(report.files) == 1
    assert report.files[0].error is not None
    # Errored files do not contribute to totals.
    assert report.total_before == 0


def test_run_bench_single_file(tmp_path):
    big = json.dumps([{"id": i} for i in range(200)])
    f = tmp_path / "one.json"
    _write_request(f, big)
    report = run_bench([f])
    assert len(report.files) == 1
    assert report.files[0].tokens_saved > 0


def test_to_dict_structure(tmp_path):
    big = json.dumps([{"id": i} for i in range(200)])
    _write_request(tmp_path / "a.json", big)
    report = run_bench([tmp_path])
    d = report.to_dict()
    assert "files" in d and "totals" in d
    assert d["totals"]["tokens_saved"] > 0
    json.dumps(d)  # serializable


def test_disabled_compression_yields_no_savings(tmp_path):
    big = json.dumps([{"id": i} for i in range(200)])
    _write_request(tmp_path / "a.json", big)
    report = run_bench([tmp_path], Config(compression={"enabled": False}))
    assert report.total_saved == 0


def test_bundled_fixtures_exist_and_compress():
    fixtures = default_fixtures_dir()
    assert fixtures.is_dir()
    report = run_bench([fixtures])
    assert report.files
    assert report.total_saved > 0
