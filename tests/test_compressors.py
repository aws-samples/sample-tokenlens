import json

from tokenlens.compressors.code import CodeCompressor
from tokenlens.compressors.json_compressor import JsonCompressor
from tokenlens.compressors.logs import LogCompressor
from tokenlens.compressors.registry import compress_block, select_compressor


class TestLogCompressor:
    def _make_log(self) -> str:
        lines = []
        for i in range(60):
            lines.append(f"12:00:{i:02d} INFO processing item {i} ... passed")
        lines.insert(30, "12:00:30 ERROR failed to connect to database: timeout")
        return "\n".join(lines)

    def test_matches_log_output(self):
        assert LogCompressor().matches(self._make_log())

    def test_does_not_match_short_text(self):
        assert not LogCompressor().matches("just one line")

    def test_preserves_error_line(self):
        result = LogCompressor().compress(self._make_log())
        assert result.applied
        assert "ERROR failed to connect" in result.text
        assert result.compressed_tokens < result.original_tokens

    def test_omission_marker_present(self):
        result = LogCompressor().compress(self._make_log())
        assert "low-signal lines omitted" in result.text

    def _make_repeated_traceback(self, copies: int = 20) -> str:
        block = (
            "Traceback (most recent call last):\n"
            '  File "/app/main.py", line 2, in <module>\n'
            "    from models.migration_models import Base\n"
            "ModuleNotFoundError: No module named 'models'"
        )
        return "\n\n".join([block] * copies)

    def test_collapses_repeated_blocks(self):
        result = LogCompressor().compress(self._make_repeated_traceback())
        assert result.applied
        # The first copy of the error is preserved verbatim.
        assert "ModuleNotFoundError: No module named 'models'" in result.text
        # Repetition is collapsed with a marker instead of 20 full copies.
        assert "identical block above repeated" in result.text
        assert result.text.count("Traceback (most recent call last):") == 1
        assert result.compressed_tokens < result.original_tokens

    def test_collapses_repeated_lines(self):
        text = "\n".join(["ERROR: import failed"] * 30)
        result = LogCompressor().compress(text)
        assert result.applied
        # Error text kept once, verbatim.
        assert "ERROR: import failed" in result.text
        assert "previous line repeated" in result.text
        assert result.text.count("ERROR: import failed") == 1
        assert result.compressed_tokens < result.original_tokens


class TestJsonCompressor:
    def _make_array(self) -> str:
        return json.dumps([{"id": i, "name": f"item-{i}"} for i in range(100)])

    def test_matches_json(self):
        assert JsonCompressor().matches(self._make_array())

    def test_does_not_match_prose(self):
        assert not JsonCompressor().matches("this is not json")

    def test_samples_long_array(self):
        result = JsonCompressor().compress(self._make_array())
        assert result.applied
        assert result.compressed_tokens < result.original_tokens
        parsed = json.loads(result.text)
        # head + marker + tail
        assert any("__tokenlens_omitted__" in item for item in parsed if isinstance(item, dict))

    def test_preserves_true_size_information(self):
        result = JsonCompressor().compress(self._make_array())
        parsed = json.loads(result.text)
        marker = next(item for item in parsed if isinstance(item, dict) and "__tokenlens_omitted__" in item)
        assert marker["__tokenlens_omitted__"] == 100 - 5 - 3

    def test_short_array_not_compressed(self):
        small = json.dumps([{"id": i} for i in range(5)])
        result = JsonCompressor().compress(small)
        # Compaction of whitespace may or may not help; must never grow.
        assert result.compressed_tokens <= result.original_tokens


class TestCodeCompressor:
    def _make_code(self) -> str:
        lines = ["import os", "import sys", "", "class Foo:"]
        lines.append("    def method_one(self):")
        for i in range(20):
            lines.append(f"        x_{i} = compute({i}) + transform({i})")
        lines.append("    def method_two(self):")
        for i in range(20):
            lines.append(f"        y_{i} = process({i})")
        return "\n".join(lines)

    def test_matches_code(self):
        assert CodeCompressor().matches(self._make_code())

    def test_collapses_bodies(self):
        result = CodeCompressor().compress(self._make_code())
        assert result.applied
        assert "body of" in result.text
        assert "class Foo:" in result.text
        assert "def method_one" in result.text
        assert result.compressed_tokens < result.original_tokens


class TestRegistry:
    def test_selects_json_first(self):
        arr = json.dumps([{"id": i} for i in range(50)])
        assert isinstance(select_compressor(arr), JsonCompressor)

    def test_returns_none_for_plain_text(self):
        assert select_compressor("hello there, this is prose") is None

    def test_compress_block_unmatched_is_safe(self):
        result = compress_block("hello there")
        assert not result.applied
        assert result.text == "hello there"

    def _code_with_log_strings(self) -> str:
        # Source code that embeds log-like tokens (uppercase ERROR and an
        # HH:MM:SS timestamp) as literals -- exactly what tripped the log
        # detector on quality.py.
        lines = ["import re", "", "class Checker:"]
        for m in range(8):
            lines.append(f"    def rule_{m}(self, payload):")
            lines.append('        pattern = "ERROR|WARNING at 12:00:40"')
            for line in range(8):
                lines.append(f"        value_{line} = transform(payload, {line})")
            lines.append("        return value_0")
        return "\n".join(lines)

    def test_content_scan_misroutes_code_with_log_strings_to_logs(self):
        # Without a filename hint, the log detector claims it (documents the
        # exact misroute we are fixing).
        text = self._code_with_log_strings()
        assert isinstance(select_compressor(text), LogCompressor)

    def test_py_extension_routes_to_code(self):
        text = self._code_with_log_strings()
        assert isinstance(select_compressor(text, filename="quality.py"), CodeCompressor)

    def test_extension_hint_respects_path(self):
        text = self._code_with_log_strings()
        chosen = select_compressor(text, filename="/repo/src/pkg/module.ts")
        assert isinstance(chosen, CodeCompressor)

    def test_log_extension_still_routes_to_logs(self):
        text = self._code_with_log_strings()
        assert isinstance(select_compressor(text, filename="server.log"), LogCompressor)

    def test_unknown_extension_falls_back_to_content_scan(self):
        arr = json.dumps([{"id": i} for i in range(50)])
        # .txt is not mapped; content scan still finds JSON.
        assert isinstance(select_compressor(arr, filename="data.txt"), JsonCompressor)

    def test_compress_block_uses_filename_hint(self):
        text = self._code_with_log_strings()
        result = compress_block(text, filename="quality.py")
        assert result.compressor == "code"
        assert result.applied
