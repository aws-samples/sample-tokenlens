"""Stable core API for TokenLens.

This module is the single, stable contract that every adapter (MCP server,
proxy, library embedding) builds on. It takes and returns plain
JSON-serializable Python types so it can be called from any surface without
leaking internal classes.

Nothing here performs network I/O. All optimization is local and deterministic.
"""

from __future__ import annotations

from typing import Any

from .compressors.registry import compress_block
from .config import Config
from .contextfile import ContextFileCompressor
from .pipeline import OptimizationPipeline
from .postprocess import ResponsePostProcessor
from .tokens import count_tokens


def compress_text(
    text: str, mode: str = "balanced", filename: str | None = None
) -> dict[str, Any]:
    """Compress a single block of text (for example, a tool result).

    ``filename`` is an optional routing hint: when provided, its extension can
    override content-based compressor selection (e.g. a ``.py`` file routes to
    the code compressor even if it contains log-like strings).

    Returns a dict with the compressed text and measurement fields:
        {
            "text": str,               # compressed (or original if not helpful)
            "original_tokens": int,
            "compressed_tokens": int,
            "tokens_saved": int,
            "applied": bool,           # True if compression actually shrank it
            "compressor": str,         # which compressor ran ("json"/"logs"/...)
        }

    Fails open: on any internal error, returns the original text unchanged.
    """
    original_tokens = count_tokens(text) if isinstance(text, str) else 0
    try:
        result = compress_block(text, mode, filename)
    except Exception:
        # Fail open: a compressor error must never corrupt the content.
        return {
            "text": text,
            "original_tokens": original_tokens,
            "compressed_tokens": original_tokens,
            "tokens_saved": 0,
            "applied": False,
            "compressor": "none",
        }
    return {
        "text": result.text,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "tokens_saved": result.tokens_saved,
        "applied": result.applied,
        "compressor": result.compressor,
    }


def optimize_request(
    request: dict[str, Any], config: Config | None = None
) -> dict[str, Any]:
    """Optimize a full Anthropic-style Messages request body.

    Returns:
        {
            "request": dict,           # the optimized request body
            "report": {
                "model": str,
                "input_tokens_before": int,
                "input_tokens_after": int,
                "input_tokens_saved": int,
                "stage_savings": dict[str, int],
                "routing": dict | None,   # mirrors RoutingDecision, or None
            },
        }
    """
    pipeline = OptimizationPipeline(config or Config())
    optimized, report = pipeline.process(request)
    decision = report.routing_decision
    routing = (
        {
            "tier": decision.tier,
            "confidence": decision.confidence,
            "original_model": decision.original_model,
            "routed_model": decision.routed_model,
            "applied": decision.applied,
        }
        if decision is not None
        else None
    )
    return {
        "request": optimized,
        "report": {
            "model": report.model,
            "input_tokens_before": report.input_tokens_before,
            "input_tokens_after": report.input_tokens_after,
            "input_tokens_saved": report.input_tokens_saved,
            "stage_savings": report.stage_savings,
            "routing": routing,
        },
    }


def postprocess_response(
    text: str, config: Config | None = None
) -> dict[str, Any]:
    """Strip conversational filler from a model response or agent handoff.

    Removes leading preambles and closing offers while preserving fenced code
    blocks and inline code verbatim. Returns:
        {
            "text": str,               # processed (or original) text
            "original_tokens": int,
            "processed_tokens": int,
            "tokens_saved": int,
            "applied": bool,           # True if filler was actually removed
        }

    When postprocessing is disabled in config, returns the text unchanged with
    ``applied=False``. Fails open: on any internal error, returns the original
    text unchanged.
    """
    config = config or Config()
    original_tokens = count_tokens(text) if isinstance(text, str) else 0

    def _unchanged() -> dict[str, Any]:
        return {
            "text": text,
            "original_tokens": original_tokens,
            "processed_tokens": original_tokens,
            "tokens_saved": 0,
            "applied": False,
        }

    if not config.postprocess.enabled:
        return _unchanged()

    try:
        result = ResponsePostProcessor().process(text)
    except Exception:
        # Fail open: a post-processor error must never corrupt the response.
        return _unchanged()

    return {
        "text": result.text,
        "original_tokens": result.original_tokens,
        "processed_tokens": result.processed_tokens,
        "tokens_saved": result.tokens_saved,
        "applied": result.applied,
    }


def compress_context_file(text: str) -> dict[str, Any]:
    """Compress persistent context-file text (steering, AGENTS.md, CLAUDE.md).

    Collapses redundant whitespace and repeated lines and strips leading
    filler, while preserving code blocks, inline code, file paths, URLs, and
    identifiers verbatim. Returns:
        {
            "text": str,
            "original_tokens": int,
            "compressed_tokens": int,
            "tokens_saved": int,
            "applied": bool,
        }

    Fails open: on any internal error, returns the original text unchanged.
    """
    original_tokens = count_tokens(text) if isinstance(text, str) else 0
    try:
        result = ContextFileCompressor().compress(text)
    except Exception:
        return {
            "text": text,
            "original_tokens": original_tokens,
            "compressed_tokens": original_tokens,
            "tokens_saved": 0,
            "applied": False,
        }
    return {
        "text": result.text,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "tokens_saved": result.tokens_saved,
        "applied": result.applied,
    }
