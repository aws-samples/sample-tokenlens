"""Offline savings benchmark.

Measures token savings by running captured or sample request bodies through the
optimization pipeline and comparing before/after token counts. Requires no API
key and makes no network calls, so it works anywhere — including behind an
internal gateway where the keyed proxy cannot run.

Token counts are estimates (see ``tokens.py``); treat the *relative* savings as
accurate, not the absolute provider billing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .api import optimize_request
from .config import Config


@dataclass
class FileResult:
    path: str
    input_tokens_before: int
    input_tokens_after: int
    stage_savings: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def tokens_saved(self) -> int:
        return max(0, self.input_tokens_before - self.input_tokens_after)

    @property
    def savings_percent(self) -> float:
        if self.input_tokens_before == 0:
            return 0.0
        return round(100 * self.tokens_saved / self.input_tokens_before, 2)


@dataclass
class BenchReport:
    files: list[FileResult] = field(default_factory=list)

    @property
    def total_before(self) -> int:
        return sum(f.input_tokens_before for f in self.files if f.error is None)

    @property
    def total_after(self) -> int:
        return sum(f.input_tokens_after for f in self.files if f.error is None)

    @property
    def total_saved(self) -> int:
        return max(0, self.total_before - self.total_after)

    @property
    def savings_percent(self) -> float:
        if self.total_before == 0:
            return 0.0
        return round(100 * self.total_saved / self.total_before, 2)

    def to_dict(self) -> dict:
        return {
            "files": [
                {
                    "path": f.path,
                    "input_tokens_before": f.input_tokens_before,
                    "input_tokens_after": f.input_tokens_after,
                    "tokens_saved": f.tokens_saved,
                    "savings_percent": f.savings_percent,
                    "stage_savings": f.stage_savings,
                    "error": f.error,
                }
                for f in self.files
            ],
            "totals": {
                "files": len([f for f in self.files if f.error is None]),
                "input_tokens_before": self.total_before,
                "input_tokens_after": self.total_after,
                "tokens_saved": self.total_saved,
                "savings_percent": self.savings_percent,
            },
        }


def _iter_json_files(paths: list[Path]) -> list[Path]:
    """Expand the given paths into a sorted list of .json files."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
    return files


def run_bench(paths: list[str | Path], config: Config | None = None) -> BenchReport:
    """Run the pipeline over request-body JSON files and report savings.

    Each file must contain a single Anthropic-style Messages request body.
    Files that cannot be parsed are recorded with an error and skipped in the
    totals.
    """
    config = config or Config()
    report = BenchReport()

    for path in _iter_json_files([Path(p) for p in paths]):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            report.files.append(
                FileResult(str(path), 0, 0, error=f"parse error: {exc}")
            )
            continue

        result = optimize_request(body, config)
        rpt = result["report"]
        report.files.append(
            FileResult(
                path=str(path),
                input_tokens_before=rpt["input_tokens_before"],
                input_tokens_after=rpt["input_tokens_after"],
                stage_savings=rpt["stage_savings"],
            )
        )

    return report


def default_fixtures_dir() -> Path:
    """Return the installed sample-fixtures directory."""
    from importlib.resources import files

    resource = files("tokenlens.resources").joinpath("fixtures")
    return Path(str(resource))
