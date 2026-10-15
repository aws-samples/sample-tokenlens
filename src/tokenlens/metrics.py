"""Metrics and event logging.

Every optimized request appends one JSON line to an event log. Aggregate
stats are computed by reading that log. This keeps the MVP dependency-free and
local-first: no database, no network, no telemetry.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock


@dataclass
class RequestEvent:
    """A single optimized request's before/after measurements."""

    timestamp: float
    model: str
    input_tokens_before: int
    input_tokens_after: int
    stage_savings: dict[str, int] = field(default_factory=dict)
    routing_tier: str | None = None
    routed_model: str | None = None

    @property
    def input_tokens_saved(self) -> int:
        return max(0, self.input_tokens_before - self.input_tokens_after)

    @property
    def savings_percent(self) -> float:
        if self.input_tokens_before == 0:
            return 0.0
        return round(100 * self.input_tokens_saved / self.input_tokens_before, 2)


class MetricsRecorder:
    """Appends request events to a JSONL log and computes aggregate stats."""

    def __init__(self, directory: str, enabled: bool = True):
        self.enabled = enabled
        self.dir = Path(directory)
        self.events_path = self.dir / "events.jsonl"
        self._lock = Lock()
        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def record(self, event: RequestEvent) -> None:
        if not self.enabled:
            return
        line = json.dumps(asdict(event), ensure_ascii=False)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def make_event(
        self,
        model: str,
        input_tokens_before: int,
        input_tokens_after: int,
        stage_savings: dict[str, int],
        routing_tier: str | None = None,
        routed_model: str | None = None,
    ) -> RequestEvent:
        return RequestEvent(
            timestamp=time.time(),
            model=model,
            input_tokens_before=input_tokens_before,
            input_tokens_after=input_tokens_after,
            stage_savings=stage_savings,
            routing_tier=routing_tier,
            routed_model=routed_model,
        )

    def stats(self) -> dict:
        """Compute aggregate savings across all recorded events."""
        if not self.events_path.exists():
            return _empty_stats()

        total_before = 0
        total_after = 0
        request_count = 0
        stage_totals: dict[str, int] = {}
        routing_by_tier = {"trivial": 0, "moderate": 0, "complex": 0}

        with self.events_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                request_count += 1
                total_before += event.get("input_tokens_before", 0)
                total_after += event.get("input_tokens_after", 0)
                for stage, saved in event.get("stage_savings", {}).items():
                    stage_totals[stage] = stage_totals.get(stage, 0) + saved
                tier = event.get("routing_tier")
                if tier in routing_by_tier:
                    routing_by_tier[tier] += 1

        saved = max(0, total_before - total_after)
        percent = round(100 * saved / total_before, 2) if total_before else 0.0
        return {
            "requests_optimized": request_count,
            "input_tokens_before": total_before,
            "input_tokens_after": total_after,
            "input_tokens_saved": saved,
            "savings_percent": percent,
            "savings_by_stage": stage_totals,
            "routing_by_tier": routing_by_tier,
        }


def _empty_stats() -> dict:
    return {
        "requests_optimized": 0,
        "input_tokens_before": 0,
        "input_tokens_after": 0,
        "input_tokens_saved": 0,
        "savings_percent": 0.0,
        "savings_by_stage": {},
        "routing_by_tier": {"trivial": 0, "moderate": 0, "complex": 0},
    }
