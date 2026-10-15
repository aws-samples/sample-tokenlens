"""Log output compressor.

Build/test/runtime logs are dominated by low-signal lines (passing tests,
progress ticks, info-level chatter). The load-bearing lines are errors,
warnings, failures, and tracebacks. This compressor keeps the high-signal
lines and collapses runs of low-signal lines into a short marker.
"""

from __future__ import annotations

import re

from . import Compressor

# Lines containing these markers are always kept.
_HIGH_SIGNAL = re.compile(
    r"\b(error|err|fail|failed|failure|exception|traceback|fatal|panic|"
    r"warn|warning|critical|assert|denied|refused|timeout|cannot|unable)\b",
    re.IGNORECASE,
)

# Lines that are low-signal noise. Matched anywhere in the line because real
# log lines are usually prefixed with a timestamp before the level/status.
_LOW_SIGNAL = re.compile(
    r"\b(pass|passed|passing|ok|info|debug|trace|done|success|succeeded)\b|"
    r"✓|√|\[\s*\d+%\s*\]",
    re.IGNORECASE,
)

# A line that looks like a log line at all (heuristic gate for the whole block).
_LOG_HINT = re.compile(
    r"(\bINFO\b|\bDEBUG\b|\bERROR\b|\bWARN\b|\bWARNING\b|\bTRACE\b|"
    r"Traceback \(most recent call last\)|\d{2}:\d{2}:\d{2}|PASS|FAIL)"
)

# Keep the last N lines regardless, since the tail of a log is often the result.
_TAIL_KEEP = 5

# Blocks are separated by one or more blank lines (e.g. successive tracebacks).
_BLOCK_SEPARATOR = re.compile(r"\n[ \t]*\n")

# Volatile tokens that differ between otherwise-identical repeated records
# (timestamps, request/stream IDs, line numbers). Masked only for *comparison*;
# the first occurrence of a repeated block is always emitted verbatim.
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_DIGITS = re.compile(r"\d+")

# Bound the period search so pathological inputs stay linear-ish.
_MAX_PERIOD = 500


class LogCompressor(Compressor):
    name = "logs"

    def matches(self, text: str) -> bool:
        line_count = text.count("\n")
        if line_count < 10:
            return False
        return bool(_LOG_HINT.search(text))

    def _compress(self, text: str, mode: str) -> str:
        # Repeated content (identical stack traces, repeated error lines) is the
        # dominant source of log bloat, and none of it is "low-signal" line
        # noise, so collapse repetition before the signal-based line filter.
        text = self._dedup_blocks(text)
        text = self._dedup_repeated_sequences(text)

        lines = text.splitlines()
        tail_start = max(0, len(lines) - _TAIL_KEEP)

        kept: list[str] = []
        dropped_run = 0
        # State for collapsing runs of an identical (non-blank) line.
        repeat_line: str | None = None
        repeat_count = 0

        def flush_run() -> None:
            nonlocal dropped_run
            if dropped_run > 0:
                kept.append(f"... [{dropped_run} low-signal lines omitted] ...")
                dropped_run = 0

        def flush_repeat() -> None:
            nonlocal repeat_line, repeat_count
            if repeat_line is not None and repeat_count > 1:
                kept.append(
                    f"... [previous line repeated {repeat_count - 1} more time(s)] ..."
                )
            repeat_line = None
            repeat_count = 0

        for index, line in enumerate(lines):
            in_tail = index >= tail_start
            high = _HIGH_SIGNAL.search(line)
            low = _LOW_SIGNAL.search(line) and not high

            if high or in_tail or not low:
                flush_run()
                # Collapse consecutive identical, non-blank lines to one copy.
                if line.strip() and line == repeat_line:
                    repeat_count += 1
                    continue
                flush_repeat()
                kept.append(line)
                repeat_line = line if line.strip() else None
                repeat_count = 1 if line.strip() else 0
            else:
                flush_repeat()
                dropped_run += 1

        flush_repeat()
        flush_run()
        return "\n".join(kept)

    def _dedup_blocks(self, text: str) -> str:
        """Collapse consecutive identical blocks into a single copy + marker.

        Successive stack traces and repeated multi-line records are a common,
        high-volume form of log repetition that carries no extra information
        after the first occurrence. The first copy is preserved verbatim so no
        error text is lost.
        """
        blocks = _BLOCK_SEPARATOR.split(text)
        if len(blocks) < 2:
            return text

        out: list[str] = []
        index = 0
        total = len(blocks)
        while index < total:
            block = blocks[index]
            key = block.strip()
            ahead = index + 1
            while ahead < total and blocks[ahead].strip() == key and key:
                ahead += 1
            repeats = ahead - index
            out.append(block)
            if repeats > 1:
                out.append(
                    f"... [identical block above repeated {repeats - 1} more time(s)] ..."
                )
            index = ahead
        return "\n\n".join(out)

    @staticmethod
    def _normalize(line: str) -> str:
        """Mask volatile tokens so structurally-identical lines compare equal."""
        line = _UUID.sub("<id>", line)
        line = _DIGITS.sub("#", line)
        return line

    def _dedup_repeated_sequences(self, text: str) -> str:
        """Collapse consecutive repeats of a multi-line block.

        Handles the common case where each record carries a unique timestamp or
        request id (so lines are never byte-identical) but the underlying block
        repeats, e.g. the same stack trace emitted once per request in a CSV
        log export. Volatile tokens are masked only to detect the repeat; the
        first block is kept verbatim, so no error text is fabricated or lost.
        """
        lines = text.splitlines()
        total = len(lines)
        if total < 4:
            return text

        sig = [self._normalize(line) for line in lines]
        out: list[str] = []
        index = 0
        while index < total:
            period = self._find_period(sig, index, total)
            if period:
                repeats = 2
                while (
                    index + (repeats + 1) * period <= total
                    and sig[index + repeats * period : index + (repeats + 1) * period]
                    == sig[index : index + period]
                ):
                    repeats += 1
                out.extend(lines[index : index + period])
                out.append(
                    f"... [{period}-line block above repeated {repeats - 1} "
                    "more time(s), volatile fields differ] ..."
                )
                index += repeats * period
            else:
                out.append(lines[index])
                index += 1
        return "\n".join(out)

    @staticmethod
    def _find_period(sig: list[str], start: int, total: int) -> int:
        """Return the block length that repeats at `start`, or 0 if none.

        Anchors on the next recurrence of the start line's signature (period
        >= 2), then verifies the whole block repeats. Single-line repeats are
        left to the consecutive-line collapser in the main pass.
        """
        anchor = sig[start]
        if not anchor.strip():
            return 0
        limit = min(total, start + _MAX_PERIOD + 1)
        for nxt in range(start + 2, limit):
            if sig[nxt] != anchor:
                continue
            period = nxt - start
            if start + 2 * period > total:
                return 0
            block = sig[start : start + period]
            # Runs of a single identical line are handled by the consecutive-
            # line collapser; require real multi-line structure here.
            if len(set(block)) < 2:
                continue
            if block == sig[start + period : start + 2 * period]:
                return period
        return 0
