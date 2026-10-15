"""Property-based test for independent per-lever toggling (Lever config).

Covers design Property 12 for the always-on-savings feature: the three
per-lever enable flags (``always_on.steering_compression_enabled``,
``always_on.read_routing_enabled``, and ``history.enabled``) are independently
configurable, and an explicitly configured value is loaded verbatim rather than
falling back to the safe default.

The three levers hook into TokenLens at different layers, so this test
exercises the shared control surface they are all toggled through: the layered
configuration loader. For every subset of the three enable flags, an explicit
value is written to ``tokenlens.yaml`` and loaded; each loaded flag must equal
its explicit value independently of the other two flags' values.
"""

import textwrap

from hypothesis import given, settings
from hypothesis import strategies as st

from tokenlens.config import load_config


def _write_config(
    tmp_path,
    *,
    steering: bool,
    read_routing: bool,
    history: bool,
) -> str:
    """Write a tokenlens.yaml that explicitly sets all three lever flags."""
    body = textwrap.dedent(
        f"""\
        always_on:
          steering_compression_enabled: {str(steering).lower()}
          read_routing_enabled: {str(read_routing).lower()}
        history:
          enabled: {str(history).lower()}
        """
    )
    path = tmp_path / "tokenlens.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestIndependentLeverToggling:
    # Feature: always-on-savings, Property 12: For any combination of per-lever enable flags, each enabled lever applies its optimization and each disabled lever leaves its target unchanged, independent of the other levers' flags; when a setting is explicitly configured, the loaded configuration value equals the explicit value rather than the safe default.
    # **Validates: Requirements 8.4, 9.2**
    @settings(max_examples=100)
    @given(
        steering=st.booleans(),
        read_routing=st.booleans(),
        history=st.booleans(),
    )
    def test_each_lever_flag_loads_independently(
        self, tmp_path_factory, steering, read_routing, history
    ):
        tmp_path = tmp_path_factory.mktemp("lever_toggle")
        config_path = _write_config(
            tmp_path,
            steering=steering,
            read_routing=read_routing,
            history=history,
        )

        config = load_config(config_path)

        # Each lever's loaded enable flag equals the explicitly configured
        # value, independent of the other two flags (Requirement 8.4). Because
        # the safe default for every flag is True, any subset that sets a flag
        # to False also proves the explicit value wins over the safe default
        # (Requirement 9.2).
        assert config.always_on.steering_compression_enabled is steering
        assert config.always_on.read_routing_enabled is read_routing
        assert config.history.enabled is history
