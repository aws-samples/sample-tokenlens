"""Opt-in capture of optimization records for building savings reports.

Capture is measurement-first: by default it records only token counts, the
content type, and which compressor ran — never raw content. This is safe to
run in customer environments and is sufficient to produce a credible savings
report.

Raw-content capture is available (``store_content=True``) for debugging, but is
OFF by default and passes content through a best-effort secret redactor first.
Even redacted, storing customer content is a liability — enable it only in
environments you control.

Records are appended as JSON lines to ``<dir>/captures.jsonl``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import Lock

# Best-effort secret redaction for the opt-in store_content path. This is a
# safety net, not a guarantee — measurement-only capture remains the safe mode.
_REDACTIONS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b(x-api-key|api[_-]?key|authorization|password|secret|token)"
                r"\b([\"']?\s*[:=]\s*[\"']?)([^\s\"',}]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
]

# Cap stored content length so captures never bloat.
_MAX_CONTENT_CHARS = 2000


def redact(text: str) -> str:
    """Apply best-effort secret redaction to a string."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class CaptureRecorder:
    """Appends optimization records to a JSONL capture log."""

    def __init__(self, directory: str, enabled: bool = False, store_content: bool = False):
        self.enabled = enabled
        self.store_content = store_content
        self.dir = Path(directory)
        self.path = self.dir / "captures.jsonl"
        self._lock = Lock()
        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def record(
        self,
        source: str,
        content_type: str,
        original_tokens: int,
        compressed_tokens: int,
        applied: bool,
        mode: str = "balanced",
        content: str | None = None,
    ) -> None:
        """Record one optimization outcome.

        Args:
            source: Where the record came from (e.g. "mcp:compress", "proxy").
            content_type: The compressor that ran, or a request marker.
            original_tokens: Tokens before optimization.
            compressed_tokens: Tokens after optimization.
            applied: Whether optimization actually reduced tokens.
            mode: Compression mode used.
            content: Optional raw content; stored (redacted, truncated) only if
                ``store_content`` is enabled.
        """
        if not self.enabled:
            return

        record: dict = {
            "ts": time.time(),
            "source": source,
            "content_type": content_type,
            "mode": mode,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "tokens_saved": max(0, original_tokens - compressed_tokens),
            "applied": applied,
        }

        if self.store_content and content is not None:
            snippet = redact(content)[:_MAX_CONTENT_CHARS]
            record["content_sample"] = snippet

        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
