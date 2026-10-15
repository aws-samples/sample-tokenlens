"""Savings report generator.

Reads capture logs (``captures.jsonl``) produced by the CaptureRecorder and
builds an aggregate savings report suitable for sharing: totals, breakdown by
content type and by source, and a distribution of per-record savings.

This is the artifact that turns "it works on my machine" into a defensible
statement about real-world savings.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GroupStat:
    count: int = 0
    applied: int = 0
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def savings_percent(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return round(100 * self.tokens_saved / self.tokens_before, 2)


@dataclass
class Report:
    total: GroupStat = field(default_factory=GroupStat)
    by_content_type: dict[str, GroupStat] = field(default_factory=dict)
    by_source: dict[str, GroupStat] = field(default_factory=dict)
    per_record_percents: list[float] = field(default_factory=list)

    @property
    def apply_rate(self) -> float:
        if self.total.count == 0:
            return 0.0
        return round(100 * self.total.applied / self.total.count, 2)

    def _distribution(self) -> dict:
        if not self.per_record_percents:
            return {"min": 0.0, "median": 0.0, "max": 0.0}
        pts = self.per_record_percents
        return {
            "min": round(min(pts), 2),
            "median": round(statistics.median(pts), 2),
            "max": round(max(pts), 2),
        }

    def to_dict(self) -> dict:
        def grp(g: GroupStat) -> dict:
            return {
                "count": g.count,
                "applied": g.applied,
                "tokens_before": g.tokens_before,
                "tokens_after": g.tokens_after,
                "tokens_saved": g.tokens_saved,
                "savings_percent": g.savings_percent,
            }

        return {
            "total": {**grp(self.total), "apply_rate": self.apply_rate},
            "distribution_percent": self._distribution(),
            "by_content_type": {k: grp(v) for k, v in sorted(self.by_content_type.items())},
            "by_source": {k: grp(v) for k, v in sorted(self.by_source.items())},
        }

    def to_markdown(self) -> str:
        d = self.to_dict()
        t = d["total"]
        dist = d["distribution_percent"]
        lines = [
            "# TokenLens Savings Report",
            "",
            f"- **Records captured:** {t['count']}",
            f"- **Optimizations applied:** {t['applied']} ({t['apply_rate']}% of records)",
            f"- **Tokens before:** {t['tokens_before']:,}",
            f"- **Tokens after:** {t['tokens_after']:,}",
            f"- **Tokens saved:** {t['tokens_saved']:,}",
            f"- **Overall savings:** {t['savings_percent']}%",
            f"- **Per-record savings (min / median / max):** "
            f"{dist['min']}% / {dist['median']}% / {dist['max']}%",
            "",
            "## Savings by content type",
            "",
            "| content type | records | tokens saved | savings % |",
            "|---|---:|---:|---:|",
        ]
        for name, g in d["by_content_type"].items():
            lines.append(f"| {name} | {g['count']} | {g['tokens_saved']:,} | {g['savings_percent']}% |")
        lines += [
            "",
            "## Savings by source",
            "",
            "| source | records | tokens saved | savings % |",
            "|---|---:|---:|---:|",
        ]
        for name, g in d["by_source"].items():
            lines.append(f"| {name} | {g['count']} | {g['tokens_saved']:,} | {g['savings_percent']}% |")
        lines += [
            "",
            "> Token counts are estimates (tiktoken); treat the relative "
            "savings as accurate, not the absolute provider billing.",
        ]
        return "\n".join(lines)


def _iter_capture_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.jsonl")))
        elif path.is_file():
            files.append(path)
    return files


def build_report(paths: list[str | Path]) -> Report:
    """Aggregate one or more capture logs into a Report."""
    report = Report()

    for path in _iter_capture_files([Path(p) for p in paths]):
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                _accumulate(report, rec)

    return report


def _accumulate(report: Report, rec: dict) -> None:
    before = int(rec.get("original_tokens", 0))
    after = int(rec.get("compressed_tokens", 0))
    applied = bool(rec.get("applied", False))
    content_type = str(rec.get("content_type", "unknown"))
    source = str(rec.get("source", "unknown"))

    for group in (
        report.total,
        report.by_content_type.setdefault(content_type, GroupStat()),
        report.by_source.setdefault(source, GroupStat()),
    ):
        group.count += 1
        group.applied += 1 if applied else 0
        group.tokens_before += before
        group.tokens_after += after

    if before > 0:
        report.per_record_percents.append(100 * max(0, before - after) / before)
