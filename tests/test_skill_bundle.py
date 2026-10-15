import importlib.util
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / "skill"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_skill", _SKILL / "scripts" / "validate_skill.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _skill_text() -> str:
    return (_SKILL / "tokenlens" / "SKILL.md").read_text(encoding="utf-8")


class TestManifest:
    def test_skill_md_exists_in_tokenlens_dir(self):
        assert (_SKILL / "tokenlens" / "SKILL.md").is_file()

    def test_frontmatter_name_and_description(self):
        data = validator.parse_frontmatter(_skill_text())
        assert data["name"] == "tokenlens"
        assert data["description"]

    def test_name_format_and_description_length(self):
        data = validator.parse_frontmatter(_skill_text())
        assert re.match(r"^[a-z0-9-]{1,64}$", data["name"])
        assert len(data["description"]) <= 1024


class TestInstructions:
    def test_required_tool_instructions_present(self):
        body = _skill_text()
        for tool in ("tokenlens_compress", "tokenlens_read_file", "tokenlens_postprocess_response"):
            assert tool in body

    def test_mentions_mcp_server_prerequisite(self):
        assert "MCP server" in _skill_text()


class TestDocumentation:
    def test_readme_covers_install_and_import(self):
        # Normalize whitespace so line wrapping doesn't break substring checks.
        readme = re.sub(r"\s+", " ", (_SKILL / "README.md").read_text(encoding="utf-8").lower())
        assert "~/.kiro/skills/" in readme
        assert ".kiro/skills/" in readme
        assert "github" in readme
        assert "local folder" in readme


class TestValidator:
    def test_bundle_passes(self):
        validator.validate()  # should not raise

    def test_bad_name_caught(self):
        with pytest.raises(validator.ValidationError):
            validator.check_name({"name": "TokenLens_Bad"})

    def test_missing_instruction_caught(self):
        with pytest.raises(validator.ValidationError):
            validator.check_instructions("only tokenlens_compress here")

    def test_long_description_caught(self):
        with pytest.raises(validator.ValidationError):
            validator.check_description({"description": "x" * 1025})
