import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_POWER = _ROOT / "power"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_power", _POWER / "scripts" / "validate_power.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _power_text() -> str:
    return (_POWER / "POWER.md").read_text(encoding="utf-8")


def _mcp() -> dict:
    return json.loads((_POWER / "mcp.json").read_text(encoding="utf-8"))


def _usage_text() -> str:
    return (_POWER / "steering" / "tokenlens-usage.md").read_text(encoding="utf-8")


def _always_on_text() -> str:
    return (_POWER / "steering" / "tokenlens-always-on.md").read_text(encoding="utf-8")


class TestBundleStructure:
    def test_power_md_exists(self):
        assert (_POWER / "POWER.md").is_file()

    def test_mcp_json_exists_and_valid(self):
        assert (_POWER / "mcp.json").is_file()
        assert "tokenlens" in _mcp()["mcpServers"]

    def test_steering_files_exist(self):
        assert (_POWER / "steering" / "tokenlens-usage.md").is_file()
        assert (_POWER / "steering" / "tokenlens-always-on.md").is_file()


class TestFrontmatter:
    def test_frontmatter_has_required_fields_and_keywords(self):
        data = validator.parse_frontmatter(_power_text())
        validator.check_frontmatter(data)
        assert data["name"] == "tokenlens"
        assert data["keywords"]

    def test_keyword_categories_present(self):
        data = validator.parse_frontmatter(_power_text())
        # Should not raise.
        validator.check_keyword_categories(data["keywords"])

    def test_empty_keywords_rejected(self):
        with pytest.raises(validator.ValidationError):
            validator.check_frontmatter(
                {
                    "name": "x",
                    "displayName": "x",
                    "description": "x",
                    "author": "x",
                    "keywords": [],
                }
            )


class TestConsistency:
    def test_server_names_agree(self):
        validator.check_server_names(_power_text(), _mcp())

    def test_no_machine_paths_in_bundle(self):
        validator.check_no_machine_paths(
            json.dumps(_mcp()), _usage_text(), _always_on_text()
        )

    def test_machine_path_would_be_caught(self):
        with pytest.raises(validator.ValidationError):
            validator.check_no_machine_paths("command: /Users/someone/.venv/bin/python")


class TestInstructions:
    def test_onboarding_has_version_check_and_install(self):
        validator.check_onboarding(_power_text())

    def test_all_tool_instructions_present(self):
        validator.check_tool_instructions(_power_text(), _usage_text())

    def test_missing_tool_would_be_caught(self):
        with pytest.raises(validator.ValidationError):
            validator.check_tool_instructions("only tokenlens_compress here")

    def test_always_on_keyword_independent(self):
        validator.check_always_on(_always_on_text())


class TestDocumentation:
    def test_readme_covers_install_verify_stats(self):
        readme = (_POWER / "README.md").read_text(encoding="utf-8").lower()
        assert "tokenlens kiro install" in readme
        assert "tokenlens kiro doctor" in readme
        assert "mcp.json" in readme
        assert "tokenlens stats" in readme


class TestStandaloneRegistration:
    def test_standalone_file_exists_and_declares_tokenlens(self):
        path = _POWER / "mcp-standalone.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "tokenlens" in data["mcpServers"]

    def test_standalone_matches_power_configuration(self):
        standalone = json.loads(
            (_POWER / "mcp-standalone.json").read_text(encoding="utf-8")
        )
        validator.check_standalone(_mcp(), standalone)
        assert standalone == _mcp()

    def test_readme_documents_installer_and_power_tradeoff(self):
        readme = (_POWER / "README.md").read_text(encoding="utf-8").lower()
        assert "mcp-standalone.json" in readme
        assert "keyword" in readme
        assert "tokenlens kiro install" in readme


class TestFullValidation:
    def test_bundle_passes_full_validation(self):
        # Should not raise.
        validator.validate()
