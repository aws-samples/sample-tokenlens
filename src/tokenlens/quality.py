"""Quality evaluation for compression.

The quality risk in compression is *information loss*: did the compressed form
drop something the model needed? Because our compressors are deterministic, we
can measure this offline — no API key, no model call — by checking that the
**critical** information in the original survives in the compressed output.

Compression is *supposed* to drop noise (passing log lines, the middle of a big
array, function bodies in background files). The eval does not penalize that.
It only checks that the load-bearing items are preserved:

    logs  -> every error / warning / failure line
    code  -> every function / class signature
    json  -> valid JSON, top-level keys, and recoverable true item counts
    text  -> file paths, ARNs, URLs, long identifiers/hashes

Any critical item that goes missing is a quality regression (a failure), which
makes this harness usable as a CI gate.

An optional LLM-judge layer (does the model still produce a correct answer with
compressed context?) can be layered on top via ``JudgeProtocol`` when a model
endpoint is available, but is intentionally not required to run the suite.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

# ---- critical-item extractors (per content type) -------------------------

_ERROR_LINE = re.compile(
    r"\b(error|fail|failed|failure|exception|traceback|fatal|panic|critical|"
    r"denied|refused|timeout)\b",
    re.IGNORECASE,
)
_WARN_LINE = re.compile(r"\b(warn|warning)\b", re.IGNORECASE)
_SIGNATURE = re.compile(
    r"^\s*(def |async def |class |function |func |public |private |"
    r"protected |interface |struct |enum )",
)
_ARN = re.compile(r"arn:aws[a-z\-]*:[^\s\"',}]+")
_URL = re.compile(r"https?://[^\s\"',}]+")
_PATH = re.compile(r"\b(?:[\w.\-]+/)+[\w.\-]+\.\w{1,8}\b")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_AWS_KEYLIKE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_HEADING = re.compile(r"^(#{1,6})[ \t]+\S.*$")  # ATX markdown heading line
_REQ_ID = re.compile(r"\b[A-Z]{2,}-\d+\b")  # FR-001, NFR-002, ...


def _log_critical(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and (_ERROR_LINE.search(stripped) or _WARN_LINE.search(stripped)):
            items.append(stripped)
    return items


def _code_critical(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _SIGNATURE.match(line)]


def _text_critical(text: str) -> list[str]:
    items: list[str] = []
    for pattern in (_ARN, _URL, _PATH, _LONG_HEX, _AWS_KEYLIKE):
        items.extend(pattern.findall(text))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _markdown_critical(text: str) -> list[str]:
    """Load-bearing items for a design artifact:
      - every ATX heading LINE, verbatim (preserves level + text => anchor)
      - every requirement ID (FR-001, ...)
      - paths, ARNs, URLs, long identifiers/hashes (reuse _text_critical)
      - code/schema signatures (reuse _code_critical)
    Deduplicated, preserving first-seen order.
    """
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _HEADING.match(stripped):
            items.append(stripped)
    items.extend(_REQ_ID.findall(text))
    items.extend(_text_critical(text))
    items.extend(_code_critical(text))
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


_EXTRACTORS = {
    "logs": _log_critical,
    "code": _code_critical,
    "text": _text_critical,
    "markdown": _markdown_critical,  # NEW
}


# ---- result types ---------------------------------------------------------


@dataclass
class QualityResult:
    name: str
    content_type: str
    critical_total: int
    critical_preserved: int
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def retention(self) -> float:
        if self.critical_total == 0:
            return 1.0
        return round(self.critical_preserved / self.critical_total, 4)

    @property
    def passed(self) -> bool:
        # Critical information must be preserved 100%. Anything less is a
        # quality regression.
        return self.critical_preserved == self.critical_total and not self.notes_failed

    @property
    def notes_failed(self) -> bool:
        return any(note.startswith("FAIL:") for note in self.notes)


# ---- the evaluator --------------------------------------------------------


def evaluate_compression(
    name: str, original: str, compressed: str, content_type: str
) -> QualityResult:
    """Check that critical information in ``original`` survives in ``compressed``."""
    if content_type == "json":
        return _evaluate_json(name, original, compressed)

    extractor = _EXTRACTORS.get(content_type, _text_critical)
    critical = extractor(original)
    missing = [item for item in critical if item not in compressed]
    return QualityResult(
        name=name,
        content_type=content_type,
        critical_total=len(critical),
        critical_preserved=len(critical) - len(missing),
        missing=missing,
    )


def _evaluate_json(name: str, original: str, compressed: str) -> QualityResult:
    result = QualityResult(name=name, content_type="json", critical_total=0, critical_preserved=0)

    # 1. Compressed output must still be valid JSON (no corruption).
    try:
        orig = json.loads(original)
    except json.JSONDecodeError:
        result.notes.append("SKIP: original is not valid JSON")
        return result
    try:
        comp = json.loads(compressed)
    except json.JSONDecodeError:
        result.notes.append("FAIL: compressed output is not valid JSON")
        result.critical_total = 1
        return result

    checks_total = 0
    checks_ok = 0

    # 2. Top-level dict keys must be preserved.
    if isinstance(orig, dict):
        checks_total += len(orig)
        for key in orig:
            if isinstance(comp, dict) and key in comp:
                checks_ok += 1
            else:
                result.missing.append(f"key:{key}")

    # 3. Top-level array true count must be recoverable.
    if isinstance(orig, list):
        checks_total += 1
        if _array_count_recoverable(orig, comp):
            checks_ok += 1
        else:
            result.notes.append("FAIL: true item count not recoverable from compressed array")

    result.critical_total = checks_total
    result.critical_preserved = checks_ok
    return result


def _array_count_recoverable(original: list, compressed) -> bool:
    """Return True if the original array length can be recovered from the compressed form."""
    if not isinstance(compressed, list):
        return False
    if len(compressed) == len(original):
        return True
    omitted = 0
    kept = 0
    for item in compressed:
        if isinstance(item, dict) and "__tokenlens_omitted__" in item:
            omitted += int(item["__tokenlens_omitted__"])
        else:
            kept += 1
    return (kept + omitted) == len(original)


# ---- optional LLM-judge extension point -----------------------------------


class JudgeProtocol(Protocol):
    """Optional hook for answer-quality evaluation with a model endpoint.

    Implementations run a task with compressed vs. original context and return
    a score in [0, 1]. Not used by the offline suite; provided so a model-backed
    judge can be plugged in where an endpoint/key is available.
    """

    def score(self, task: str, original_context: str, compressed_context: str) -> float:
        ...


# ---- eval cases and suite runner ------------------------------------------


@dataclass
class EvalCase:
    name: str
    content_type: str
    text: str


@dataclass
class CaseOutcome:
    result: QualityResult
    original_tokens: int
    compressed_tokens: int
    compressor: str

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def savings_percent(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return round(100 * self.tokens_saved / self.original_tokens, 2)


@dataclass
class SuiteReport:
    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(o.result.passed for o in self.outcomes)

    @property
    def total_critical(self) -> int:
        return sum(o.result.critical_total for o in self.outcomes)

    @property
    def total_preserved(self) -> int:
        return sum(o.result.critical_preserved for o in self.outcomes)

    @property
    def retention(self) -> float:
        if self.total_critical == 0:
            return 1.0
        return round(self.total_preserved / self.total_critical, 4)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "overall_critical_retention": self.retention,
            "cases": [
                {
                    "name": o.result.name,
                    "content_type": o.result.content_type,
                    "passed": o.result.passed,
                    "critical_total": o.result.critical_total,
                    "critical_preserved": o.result.critical_preserved,
                    "retention": o.result.retention,
                    "missing": o.result.missing,
                    "notes": o.result.notes,
                    "tokens_saved": o.tokens_saved,
                    "savings_percent": o.savings_percent,
                    "compressor": o.compressor,
                }
                for o in self.outcomes
            ],
        }


def run_quality_suite(cases: list[EvalCase] | None = None, mode: str = "balanced") -> SuiteReport:
    """Run the real compressors over eval cases and check critical retention."""
    from .compressors.registry import compress_block

    cases = cases if cases is not None else builtin_cases()
    report = SuiteReport()
    for case in cases:
        result = compress_block(case.text, mode)
        outcome = CaseOutcome(
            result=evaluate_compression(
                case.name, case.text, result.text, case.content_type
            ),
            original_tokens=result.original_tokens,
            compressed_tokens=result.compressed_tokens,
            compressor=result.compressor,
        )
        report.outcomes.append(outcome)
    return report


def builtin_cases() -> list[EvalCase]:
    """A representative offline eval suite with embedded critical items."""
    # Log with errors buried in passing noise + a file path in the error.
    log_lines = [f"12:00:{i % 60:02d} INFO step {i} ... passed" for i in range(80)]
    log_lines.insert(40, "12:00:40 ERROR failed to open src/config/loader.py: permission denied")
    log_lines.insert(41, "12:00:41 WARNING retry limit approaching for arn:aws:s3:::my-bucket")
    logs = "\n".join(log_lines)

    # Large JSON array with nested keys.
    rows = [{"id": i, "arn": f"arn:aws:ec2:us-east-1:1234:instance/i-{i}", "state": "on"} for i in range(120)]
    json_text = json.dumps(rows)

    # Code file with several signatures and long bodies.
    code_parts = ["import os", "", "class Engine:"]
    for m in range(6):
        code_parts.append(f"    def handler_{m}(self, payload):")
        for line in range(12):
            code_parts.append(f"        v_{line} = transform(payload) + compute({line})")
        code_parts.append("        return v_0")
    code = "\n".join(code_parts)

    # Prose/text carrying critical identifiers that must survive.
    text = (
        "The deployment uses arn:aws:lambda:us-west-2:9999:function/processor and "
        "reads from config/settings.yaml. Commit 3fa9c2b18ee4 introduced the change. "
        "See https://internal.example.com/runbook for the rollback steps.\n" * 3
    )

    return [
        EvalCase("logs-with-errors", "logs", logs),
        EvalCase("json-array-with-arns", "json", json_text),
        EvalCase("code-with-signatures", "code", code),
        EvalCase("text-with-identifiers", "text", text),
    ]
