"""Configuration loading for TokenLens.

Configuration resolves in this order (later overrides earlier):
1. Built-in defaults
2. YAML config file (if provided or found at ./tokenlens.yaml)
3. Environment variables (TOKENLENS_*)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class CompressionConfig(BaseModel):
    enabled: bool = True
    # conservative | balanced | aggressive
    mode: str = "balanced"


class OutputConstraintsConfig(BaseModel):
    enabled: bool = False
    # minimal | balanced | aggressive
    level: str = "balanced"


class PrefixConfig(BaseModel):
    enabled: bool = True
    # Minimum tokens a stable prefix must have before we tag it for caching.
    min_prefix_tokens: int = 1024


class ProxyConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9470
    # Direct Anthropic Messages API base for the keyed proxy. For production AWS
    # workloads, prefer the TokenLens library with Amazon Bedrock Converse API,
    # or configure an approved gateway that implements Anthropic compatibility.
    upstream_base_url: str = "https://api.anthropic.com"
    # Request timeout to the upstream provider, in seconds.
    timeout_seconds: float = 600.0


class MetricsConfig(BaseModel):
    enabled: bool = True
    # Directory for the JSONL event log and stats.
    dir: str = ".tokenlens"


class CaptureConfig(BaseModel):
    # Opt-in capture of per-optimization records for building savings reports.
    enabled: bool = False
    # Directory for the capture log.
    dir: str = ".tokenlens/captures"
    # When True, also store (sanitized) raw content alongside measurements.
    # OFF by default: measurement-only capture is safe for customer
    # environments and is sufficient for a savings report.
    store_content: bool = False


class RCSConfig(BaseModel):
    # Reversible Content Store: keep originals so the model can retrieve full
    # detail on demand. Storage is bounded so it can never fill the disk.
    enabled: bool = False
    dir: str = ".tokenlens/rcs"
    # Entries older than this are purged (originals are only useful during the
    # active task).
    ttl_seconds: int = 86_400  # 24 hours
    # Hard ceiling on total store size; least-recently-used entries evicted.
    max_bytes: int = 200 * 1024 * 1024  # 200 MB
    # Hard ceiling on entry count; least-recently-used entries evicted.
    max_entries: int = 1000
    # Blobs larger than this are not stored (retrieval would not help).
    max_entry_bytes: int = 1024 * 1024  # 1 MB


class RoutingConfig(BaseModel):
    enabled: bool = False
    # Model name to substitute for each tier. Absent/None entries mean
    # "do not rewrite the model for this tier."
    trivial_model: str | None = None
    moderate_model: str | None = None
    # Confidence threshold in [0, 1]. Below this, the router forces `complex`.
    confidence_threshold: float = 0.6

    @field_validator("confidence_threshold")
    @classmethod
    def _validate_confidence_threshold(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1 inclusive")
        return value


class HistoryConfig(BaseModel):
    enabled: bool = True
    # Number of most-recent turns always preserved verbatim.
    retained_window: int = 10

    @field_validator("retained_window")
    @classmethod
    def _validate_retained_window(cls, value: int) -> int:
        if value < 1:
            raise ValueError("retained_window must be at least 1")
        return value


class PostProcessConfig(BaseModel):
    # Opt-in: strip conversational filler from model responses.
    enabled: bool = False


class AlwaysOnConfig(BaseModel):
    # Legacy compatibility setting. Recursive steering save hooks are no longer
    # shipped because generated Markdown can retrigger them and duplicate context.
    steering_compression_enabled: bool = True
    # Route generic large file reads through the compressed read path.
    read_routing_enabled: bool = True
    # Lever 3: minimum file Token Count (inclusive) to route/compress a read.
    read_routing_threshold: int = 2000

    @field_validator("read_routing_threshold")
    @classmethod
    def _validate_threshold(cls, value: int) -> int:
        if value < 0:
            raise ValueError("read_routing_threshold must be non-negative")
        return value


class Config(BaseModel):
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    output_constraints: OutputConstraintsConfig = Field(
        default_factory=OutputConstraintsConfig
    )
    prefix: PrefixConfig = Field(default_factory=PrefixConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    rcs: RCSConfig = Field(default_factory=RCSConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    postprocess: PostProcessConfig = Field(default_factory=PostProcessConfig)
    always_on: AlwaysOnConfig = Field(default_factory=AlwaysOnConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base``, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_overrides() -> dict[str, Any]:
    """Translate TOKENLENS_* environment variables into a nested config dict.

    Examples:
        TOKENLENS_PROXY_PORT=8080         -> {"proxy": {"port": "8080"}}
        TOKENLENS_COMPRESSION_MODE=aggressive
        TOKENLENS_OUTPUT_CONSTRAINTS_ENABLED=true
    """
    # Known two-word section prefixes that must not be split on the first "_".
    compound_sections = {"output_constraints", "always_on"}
    overrides: dict[str, Any] = {}

    for raw_key, raw_value in os.environ.items():
        if not raw_key.startswith("TOKENLENS_"):
            continue
        remainder = raw_key[len("TOKENLENS_") :].lower()

        section = None
        field = None
        for compound in compound_sections:
            prefix = compound + "_"
            if remainder.startswith(prefix):
                section = compound
                field = remainder[len(prefix) :]
                break
        if section is None:
            parts = remainder.split("_", 1)
            if len(parts) != 2:
                continue
            section, field = parts

        overrides.setdefault(section, {})[field] = _coerce(raw_value)

    return overrides


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def load_config(config_path: str | None = None) -> Config:
    """Load configuration from defaults, an optional YAML file, and env vars."""
    data: dict[str, Any] = {}

    path = Path(config_path) if config_path else Path("tokenlens.yaml")
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            file_data = yaml.safe_load(handle) or {}
        if not isinstance(file_data, dict):
            raise ValueError(
                f"Config file {path} must contain a mapping at the top level"
            )
        data = _deep_merge(data, file_data)

    data = _deep_merge(data, _env_overrides())
    return Config(**data)
