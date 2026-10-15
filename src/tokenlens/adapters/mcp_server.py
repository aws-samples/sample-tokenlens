"""Model Context Protocol (MCP) server adapter.

This is the primary cross-IDE integration surface. Any MCP-capable agent
(Kiro, Cursor, Claude Code, Amazon Q, Copilot) can call these tools to
compress token-heavy content before it reaches the model. It requires no model
credentials and performs no network I/O, so it works in customer environments
and behind internal gateways where the keyed proxy cannot be used.

Exposed tools:
    tokenlens_compress          - compress a block of text (a tool result)
    tokenlens_optimize_request  - optimize a full Anthropic request body
    tokenlens_stats             - report aggregate savings recorded so far

Usage pattern for automatic savings: wrap your high-volume tools (file reads,
search, shell, DB queries) so their output is passed through
``tokenlens_compress`` before being returned to the model.
"""

from __future__ import annotations

from typing import Any

from ..api import compress_text, optimize_request, postprocess_response
from ..capture import CaptureRecorder
from ..config import Config
from ..metrics import MetricsRecorder
from ..pathguard import is_sensitive_path
from ..rcs import RCSStore
from ..tokens import count_tokens


def _import_fastmcp():
    """Import FastMCP with an actionable message on an unsupported SDK version.

    TokenLens targets the MCP Python SDK 1.x server API. `pip install mcp` now
    resolves to 2.x by default, where `mcp.server.fastmcp` is gone, so a fresh
    machine could install TokenLens successfully and then fail to start the MCP
    server. Declared metadata caps the SDK below 2, and this guard turns any
    remaining mismatch into a message that names the fix.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("mcp")
        except PackageNotFoundError:
            installed = "not installed"
        raise ImportError(
            "TokenLens requires the MCP Python SDK 1.x server API "
            f"(mcp>=1.2,<2); installed version is {installed}. "
            "Reinstall with the supported range, for example `pipx install "
            '--force "aws-samples-tokenlens[mcp,tokenizers]"` applying '
            "constraints-release.txt, then rerun `tokenlens kiro install`. "
            f"Original error: {exc}"
        ) from exc
    return FastMCP


def create_mcp_server(config: Config | None = None):
    """Build the TokenLens FastMCP server.

    Imported lazily so the core package does not depend on the MCP SDK unless
    this adapter is actually used.
    """
    FastMCP = _import_fastmcp()

    config = config or Config()
    metrics = MetricsRecorder(config.metrics.dir, config.metrics.enabled)
    capture = CaptureRecorder(
        config.capture.dir, config.capture.enabled, config.capture.store_content
    )
    rcs = RCSStore(
        config.rcs.dir,
        config.rcs.enabled,
        config.rcs.ttl_seconds,
        config.rcs.max_bytes,
        config.rcs.max_entries,
        config.rcs.max_entry_bytes,
    )
    server = FastMCP("tokenlens")

    def _offer_retrieval(result: dict, original: str) -> None:
        """If the store is on and compression applied, store the original and
        add a retrieval reference the model can act on."""
        if not (rcs.enabled and result.get("applied")):
            return
        key = rcs.put(original)
        if key:
            result["retrieve_id"] = key
            result["text"] = (
                result["text"]
                + f"\n\n[TokenLens: content compressed. Full original available via "
                f'tokenlens_retrieve(retrieve_id="{key}").]'
            )

    @server.tool()
    def tokenlens_compress(text: str, mode: str = "balanced") -> dict[str, Any]:
        """Compress a block of text (for example a tool result, file read, or log).

        Args:
            text: The content to compress.
            mode: Compression aggressiveness - "conservative", "balanced", or
                "aggressive".

        Returns the compressed text plus token measurements. If compression
        would not reduce tokens, the original text is returned unchanged
        (``applied`` will be false).
        """
        result = compress_text(text, mode)
        capture.record(
            source="mcp:compress",
            content_type=result["compressor"],
            original_tokens=result["original_tokens"],
            compressed_tokens=result["compressed_tokens"],
            applied=result["applied"],
            mode=mode,
            content=text,
        )
        if result["applied"]:
            metrics.record(
                metrics.make_event(
                    model="mcp:compress",
                    input_tokens_before=result["original_tokens"],
                    input_tokens_after=result["compressed_tokens"],
                    stage_savings={"context_compression": result["tokens_saved"]},
                )
            )
        _offer_retrieval(result, text)
        return result

    @server.tool()
    def tokenlens_optimize_request(request: dict[str, Any]) -> dict[str, Any]:
        """Optimize a full Anthropic-style Messages request body.

        Args:
            request: The request body (model, system, messages, ...).

        Returns the optimized request and a report of tokens saved by stage.
        """
        result = optimize_request(request, config)
        report = result["report"]
        capture.record(
            source="mcp:optimize_request",
            content_type=f"request:{report['model']}",
            original_tokens=report["input_tokens_before"],
            compressed_tokens=report["input_tokens_after"],
            applied=report["input_tokens_saved"] > 0,
            content=None,
        )
        if report["input_tokens_saved"] > 0:
            routing = report.get("routing")
            metrics.record(
                metrics.make_event(
                    model=report["model"],
                    input_tokens_before=report["input_tokens_before"],
                    input_tokens_after=report["input_tokens_after"],
                    stage_savings=report["stage_savings"],
                    routing_tier=routing["tier"] if routing else None,
                    routed_model=routing["routed_model"] if routing else None,
                )
            )
        return result

    @server.tool()
    def tokenlens_postprocess_response(text: str) -> dict[str, Any]:
        """Strip conversational filler from a model response or agent handoff.

        Removes leading preambles ("Sure, I'd be happy to help") and closing
        offers ("Let me know if you need anything else") while preserving
        fenced code blocks and inline code verbatim. Returns the processed
        text plus token measurements; if no filler is found, returns the text
        unchanged (``applied`` will be false).
        """
        result = postprocess_response(text, config)
        if result["applied"]:
            metrics.record(
                metrics.make_event(
                    model="mcp:postprocess",
                    input_tokens_before=result["original_tokens"],
                    input_tokens_after=result["processed_tokens"],
                    stage_savings={"response_postprocess": result["tokens_saved"]},
                )
            )
        return result

    @server.tool()
    def tokenlens_stats() -> dict[str, Any]:
        """Report aggregate token savings recorded by this TokenLens instance."""
        return metrics.stats()

    @server.tool()
    def tokenlens_retrieve(retrieve_id: str) -> dict[str, Any]:
        """Retrieve the full original content for a compressed block.

        Use this when a compressed result omitted a detail you need (a dropped
        log line, a collapsed function body, sampled array items). Pass the
        ``retrieve_id`` that appeared in the compressed output.

        Returns {"found": bool, "retrieve_id": str, "content": str,
        "error": str | None}. Content may be unavailable if it expired or was
        evicted from the bounded store.
        """
        content = rcs.get(retrieve_id)
        if content is None:
            return {
                "found": False,
                "retrieve_id": retrieve_id,
                "content": "",
                "error": "not found (expired, evicted, or store disabled)",
            }
        return {
            "found": True,
            "retrieve_id": retrieve_id,
            "content": content,
            "error": None,
        }

    @server.tool()
    def tokenlens_read_file(
        path: str, mode: str = "balanced", max_bytes: int = 5_000_000
    ) -> dict[str, Any]:
        """Read a text file and return its contents already compressed.

        Use this instead of a raw file read when pulling large files into
        context: the token-heavy content is compressed at the source before it
        reaches the model. Read-only — it never writes or executes.

        Args:
            path: Path to the file to read.
            mode: Compression mode - "conservative", "balanced", "aggressive".
            max_bytes: Refuse files larger than this (default 5 MB).

        Returns:
            {
                "path": str,
                "text": str,          # compressed file contents
                "original_tokens": int,
                "compressed_tokens": int,
                "tokens_saved": int,
                "applied": bool,
                "compressor": str,
                "error": str | None,  # set if the file could not be read
            }
        """
        import os

        base = {
            "path": path,
            "text": "",
            "original_tokens": 0,
            "compressed_tokens": 0,
            "tokens_saved": 0,
            "applied": False,
            "compressor": "none",
            "error": None,
        }

        # Deny reads of well-known sensitive files (keys, .env, cloud
        # credentials) and malformed paths before any file access. This is a
        # hardening measure for customer environments; it returns the structured
        # error result rather than raising, preserving the fail-open contract.
        if is_sensitive_path(path):
            base["error"] = "refused: path points at a sensitive or disallowed file"
            return base

        try:
            if not os.path.isfile(path):
                base["error"] = "not a file"
                return base
            if os.path.getsize(path) > max_bytes:
                base["error"] = f"file exceeds max_bytes ({max_bytes})"
                return base
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except UnicodeDecodeError:
            base["error"] = "binary or non-UTF-8 file; not compressed"
            return base
        except OSError as exc:
            base["error"] = f"read error: {exc}"
            return base

        # Disabled generic routing returns original content unchanged.
        original_tokens = count_tokens(content)
        if not config.always_on.read_routing_enabled:
            return {
                **base,
                "text": content,
                "original_tokens": original_tokens,
                "compressed_tokens": original_tokens,
            }

        # Below-threshold pass-through: skip compression work for small files and
        # return the original content unchanged (applied=False).
        if original_tokens < config.always_on.read_routing_threshold:
            base["original_tokens"] = original_tokens
            base["compressed_tokens"] = original_tokens
            base["text"] = content
            return base

        result = compress_text(content, mode, filename=path)
        result["path"] = path
        result["error"] = None
        capture.record(
            source="mcp:read_file",
            content_type=result["compressor"],
            original_tokens=result["original_tokens"],
            compressed_tokens=result["compressed_tokens"],
            applied=result["applied"],
            mode=mode,
            content=content,
        )
        if result["applied"]:
            metrics.record(
                metrics.make_event(
                    model="mcp:read_file",
                    input_tokens_before=result["original_tokens"],
                    input_tokens_after=result["compressed_tokens"],
                    stage_savings={"context_compression": result["tokens_saved"]},
                )
            )
        _offer_retrieval(result, content)
        return result

    return server
