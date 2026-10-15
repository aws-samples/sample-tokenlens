"""Regression tests for safe always-on steering integration.

Steering compression hooks were removed because their default output remains a
Markdown file under `.kiro/steering/`, which can retrigger create/save hooks and
load duplicate policy context. Large-read routing is tested separately.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / ".kiro" / "hooks"
_STEERING = _ROOT / "power" / "steering" / "tokenlens-always-on.md"


def test_recursive_legacy_steering_hooks_are_absent():
    assert not (_HOOKS / "tokenlens-compress-steering.kiro.hook").exists()
    assert not (_HOOKS / "tokenlens-compress-steering-created.kiro.hook").exists()


def test_always_on_policy_contains_tool_decision_rules():
    text = _STEERING.read_text(encoding="utf-8")
    assert "inclusion: always" in text
    assert "tokenlens_read_file" in text
    assert "tokenlens_compress" in text
    assert "tokenlens_optimize_request" in text
    assert "tokenlens_retrieve" in text
    assert "tokenlens_stats" in text
    assert "Sensitive-path refusals are intentional" in text


def test_always_on_policy_distinguishes_text_from_binary_files():
    text = _STEERING.read_text(encoding="utf-8")
    assert "binary/image/document" in text
    assert "UTF-8 text only" in text
