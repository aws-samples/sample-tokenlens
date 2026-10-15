import pytest
from pydantic import ValidationError

from tokenlens.config import (
    AlwaysOnConfig,
    Config,
    HistoryConfig,
    RoutingConfig,
    load_config,
)


class TestRoutingConfigValidation:
    def test_boundary_values_accepted(self):
        # Both inclusive boundaries are valid.
        assert RoutingConfig(confidence_threshold=0.0).confidence_threshold == 0.0
        assert RoutingConfig(confidence_threshold=1.0).confidence_threshold == 1.0

    def test_below_range_rejected(self):
        with pytest.raises(ValidationError):
            RoutingConfig(confidence_threshold=-0.1)

    def test_above_range_rejected(self):
        with pytest.raises(ValidationError):
            RoutingConfig(confidence_threshold=1.5)

    def test_out_of_range_rejected_at_config_load(self):
        # An out-of-range value surfaces when the full Config is built.
        with pytest.raises(ValidationError):
            Config(routing={"confidence_threshold": 2.0})


class TestAlwaysOnConfigDefaults:
    def test_read_routing_threshold_defaults_to_2000(self):
        # Requirement 3.4: default routing threshold is 2000 tokens.
        assert Config().always_on.read_routing_threshold == 2000
        assert AlwaysOnConfig().read_routing_threshold == 2000

    def test_lever_flags_default_to_true(self):
        # Requirements 8.1, 8.2: each lever has a safe default of enabled.
        always_on = Config().always_on
        assert always_on.steering_compression_enabled is True
        assert always_on.read_routing_enabled is True

    def test_three_independent_booleans_alongside_history_enabled(self):
        # Requirement 8.2: the three per-lever enable flags are independent
        # booleans (two on always_on, one on history) and can be toggled
        # without affecting one another.
        config = Config(
            always_on={
                "steering_compression_enabled": False,
                "read_routing_enabled": True,
            },
            history={"enabled": False},
        )
        assert config.always_on.steering_compression_enabled is False
        assert config.always_on.read_routing_enabled is True
        assert config.history.enabled is False

        flipped = Config(
            always_on={
                "steering_compression_enabled": True,
                "read_routing_enabled": False,
            },
            history={"enabled": True},
        )
        assert flipped.always_on.steering_compression_enabled is True
        assert flipped.always_on.read_routing_enabled is False
        assert flipped.history.enabled is True


class TestAlwaysOnConfigValidation:
    def test_zero_threshold_accepted(self):
        # Non-negative boundary is valid.
        assert AlwaysOnConfig(read_routing_threshold=0).read_routing_threshold == 0

    def test_negative_threshold_rejected(self):
        # Requirement 3.4: a negative threshold is rejected.
        with pytest.raises(ValidationError):
            AlwaysOnConfig(read_routing_threshold=-1)

    def test_negative_threshold_rejected_at_config_load(self):
        # Requirement 3.4: the rejection surfaces when the full Config is built.
        with pytest.raises(ValidationError):
            Config(always_on={"read_routing_threshold": -1})


class TestHistoryConfigDefaults:
    def test_enabled_defaults_to_true(self):
        # Requirement 2.1: history trimming is active by default.
        assert Config().history.enabled is True
        assert HistoryConfig().enabled is True

    def test_retained_window_defaults_to_10(self):
        # Requirement 2.6: default retained window is 10 turns.
        assert Config().history.retained_window == 10
        assert HistoryConfig().retained_window == 10


class TestHistoryConfigValidation:
    def test_retained_window_zero_rejected(self):
        # Requirement 2.6: retained window must be an integer of at least 1.
        with pytest.raises(ValidationError):
            HistoryConfig(retained_window=0)

    def test_retained_window_zero_rejected_at_config_load(self):
        # Requirement 2.6: the rejection surfaces when the full Config is built.
        with pytest.raises(ValidationError):
            Config(history={"retained_window": 0})


class TestEnvAndYamlOverridesAcrossLevers:
    """Requirements 8.3, 9.2: every lever setting is overridable via
    tokenlens.yaml and via TOKENLENS_* env vars, and an explicit value wins
    over the safe default. Env vars for the two-word ``always_on`` section
    must parse through the compound-section path rather than being mis-split
    on the first underscore."""

    def _write_yaml(self, tmp_path, body: str):
        path = tmp_path / "tokenlens.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    # --- YAML overrides -----------------------------------------------------

    def test_yaml_overrides_each_lever_setting(self, tmp_path):
        path = self._write_yaml(
            tmp_path,
            "always_on:\n"
            "  steering_compression_enabled: false\n"
            "  read_routing_enabled: false\n"
            "  read_routing_threshold: 500\n"
            "history:\n"
            "  enabled: false\n"
            "  retained_window: 3\n",
        )
        config = load_config(str(path))
        assert config.always_on.steering_compression_enabled is False
        assert config.always_on.read_routing_enabled is False
        assert config.always_on.read_routing_threshold == 500
        assert config.history.enabled is False
        assert config.history.retained_window == 3

    # --- Env overrides (always_on compound section) -------------------------

    def test_env_overrides_always_on_steering_compression_enabled(
        self, tmp_path, monkeypatch
    ):
        # The compound section must not be mis-split into section "always" /
        # field "on_steering_compression_enabled"; it belongs to always_on.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOKENLENS_ALWAYS_ON_STEERING_COMPRESSION_ENABLED", "false")
        config = load_config()
        assert config.always_on.steering_compression_enabled is False

    def test_env_overrides_always_on_read_routing_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOKENLENS_ALWAYS_ON_READ_ROUTING_ENABLED", "false")
        config = load_config()
        assert config.always_on.read_routing_enabled is False

    def test_env_overrides_always_on_read_routing_threshold(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOKENLENS_ALWAYS_ON_READ_ROUTING_THRESHOLD", "750")
        config = load_config()
        assert config.always_on.read_routing_threshold == 750

    def test_env_overrides_history_lever(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOKENLENS_HISTORY_ENABLED", "false")
        monkeypatch.setenv("TOKENLENS_HISTORY_RETAINED_WINDOW", "4")
        config = load_config()
        assert config.history.enabled is False
        assert config.history.retained_window == 4

    # --- Explicit value wins over the safe default (backward compatibility) -

    def test_explicit_value_wins_over_safe_default(self, tmp_path, monkeypatch):
        # Requirement 9.2: an explicitly configured value is honored over the
        # safe default. The always_on defaults are all enabled/2000, so an
        # explicit disable/override must survive load.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TOKENLENS_ALWAYS_ON_READ_ROUTING_THRESHOLD", "1")
        monkeypatch.setenv("TOKENLENS_HISTORY_ENABLED", "false")
        config = load_config()
        assert config.always_on.read_routing_threshold == 1
        assert (
            config.always_on.read_routing_threshold
            != AlwaysOnConfig().read_routing_threshold
        )
        assert config.history.enabled is False
        assert config.history.enabled != HistoryConfig().enabled

    def test_env_overrides_yaml_for_always_on(self, tmp_path, monkeypatch):
        # Precedence: defaults -> yaml -> TOKENLENS_*. The env var wins.
        path = self._write_yaml(
            tmp_path,
            "always_on:\n  read_routing_threshold: 500\n",
        )
        monkeypatch.setenv("TOKENLENS_ALWAYS_ON_READ_ROUTING_THRESHOLD", "999")
        config = load_config(str(path))
        assert config.always_on.read_routing_threshold == 999
