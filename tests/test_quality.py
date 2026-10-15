import json

from tokenlens.quality import (
    builtin_cases,
    evaluate_compression,
    run_quality_suite,
)


class TestEvaluateLogs:
    def test_preserved_errors_pass(self):
        original = "INFO ok\nERROR disk full at /var/data\nINFO ok\nWARNING low mem"
        compressed = "ERROR disk full at /var/data\nWARNING low mem"
        r = evaluate_compression("t", original, compressed, "logs")
        assert r.passed
        assert r.retention == 1.0

    def test_dropped_error_fails(self):
        original = "INFO ok\nERROR disk full at /var/data\nINFO ok"
        compressed = "INFO ok\nINFO ok"  # error dropped
        r = evaluate_compression("t", original, compressed, "logs")
        assert not r.passed
        assert any("disk full" in m for m in r.missing)


class TestEvaluateCode:
    def test_signatures_preserved_pass(self):
        original = "def foo(x):\n    return x + 1\ndef bar(y):\n    return y"
        compressed = "def foo(x):\n    ... [body omitted] ...\ndef bar(y):\n    ... [body omitted] ..."
        r = evaluate_compression("t", original, compressed, "code")
        assert r.passed

    def test_dropped_signature_fails(self):
        original = "def foo(x):\n    return x\ndef bar(y):\n    return y"
        compressed = "def foo(x):\n    return x"  # bar signature lost
        r = evaluate_compression("t", original, compressed, "code")
        assert not r.passed
        assert any("bar" in m for m in r.missing)


class TestEvaluateText:
    def test_identifiers_preserved(self):
        original = "see arn:aws:s3:::bucket and config/app.yaml and hash 3fa9c2b18ee4"
        r = evaluate_compression("t", original, original, "text")
        assert r.passed
        assert r.critical_total >= 3

    def test_dropped_arn_fails(self):
        original = "resource arn:aws:s3:::bucket must stay"
        compressed = "resource must stay"
        r = evaluate_compression("t", original, compressed, "text")
        assert not r.passed


class TestEvaluateJson:
    def test_valid_sampled_array_passes(self):
        original = json.dumps([{"id": i} for i in range(100)])
        compressed = json.dumps(
            [{"id": 0}, {"__tokenlens_omitted__": 98}, {"id": 99}]
        )
        r = evaluate_compression("t", original, compressed, "json")
        assert r.passed  # 1 + 98 + 1 == 100

    def test_wrong_count_fails(self):
        original = json.dumps([{"id": i} for i in range(100)])
        compressed = json.dumps(
            [{"id": 0}, {"__tokenlens_omitted__": 5}, {"id": 99}]
        )  # 1 + 5 + 1 != 100
        r = evaluate_compression("t", original, compressed, "json")
        assert not r.passed

    def test_corrupted_json_fails(self):
        original = json.dumps([{"id": i} for i in range(100)])
        r = evaluate_compression("t", original, "not json at all", "json")
        assert not r.passed

    def test_dict_keys_preserved(self):
        original = json.dumps({"a": 1, "b": 2, "c": 3})
        r = evaluate_compression("t", original, json.dumps({"a": 1, "b": 2, "c": 3}), "json")
        assert r.passed

    def test_dropped_key_fails(self):
        original = json.dumps({"a": 1, "b": 2, "c": 3})
        r = evaluate_compression("t", original, json.dumps({"a": 1}), "json")
        assert not r.passed


class TestSuite:
    def test_real_compressors_preserve_critical_info(self):
        # The core guarantee: our actual compressors pass the quality gate.
        report = run_quality_suite(mode="balanced")
        assert report.passed, report.to_dict()
        assert report.retention == 1.0

    def test_aggressive_mode_also_preserves(self):
        report = run_quality_suite(mode="aggressive")
        assert report.passed, report.to_dict()

    def test_suite_reports_savings_alongside_quality(self):
        report = run_quality_suite()
        d = report.to_dict()
        # Every case should preserve critical info AND show real savings.
        for case in d["cases"]:
            assert case["passed"]
        assert any(c["tokens_saved"] > 0 for c in d["cases"])

    def test_harness_detects_a_lossy_compressor(self):
        # Prove the gate works: a compressor that drops errors must FAIL.
        cases = [c for c in builtin_cases() if c.content_type == "logs"]
        case = cases[0]
        # Simulate a lossy result: strip every line containing ERROR.
        lossy = "\n".join(
            line for line in case.text.splitlines() if "ERROR" not in line
        )
        result = evaluate_compression(case.name, case.text, lossy, "logs")
        assert not result.passed
