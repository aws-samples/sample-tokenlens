"""Optimization pipeline orchestrator.

Transforms an Anthropic Messages API request body by applying enabled stages:

1. Context compression - shrink large tool results, file reads, and pasted
   blocks in the conversation using type-aware compressors.
2. Output constraints - inject a concise-output instruction into the system
   prompt to reduce response tokens.
3. Prefix stabilization - tag a large, stable system prompt with
   ``cache_control`` so the provider can serve it from cache at a discount.

The pipeline never mutates the caller's object; it works on a deep copy and
returns a new request dict plus a report describing what changed. Any
unexpected structure is passed through untouched (fail-open).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .compressors.registry import compress_block
from .config import Config
from .history import ConversationHistoryTrimmer
from .routing import RoutingDecision, SmartRouter
from .tokens import count_tokens

# Output-constraint instructions by level. Kept short so they cost few input
# tokens relative to the output tokens they save.
_OUTPUT_INSTRUCTIONS = {
    "minimal": (
        "Be concise. Skip preamble and acknowledgments. "
        "Preserve all code, commands, and error messages exactly."
    ),
    "balanced": (
        "Respond concisely. Omit filler, restatements, and hedging. "
        "Prefer short sentences and bullet points. "
        "Never abbreviate code, commands, file paths, or error messages."
    ),
    "aggressive": (
        "Answer in the fewest words that remain correct and unambiguous. "
        "Use fragments over full sentences for prose. No preamble, no summary. "
        "Reproduce code, commands, paths, and errors verbatim."
    ),
}


@dataclass
class PipelineReport:
    """Summary of what the pipeline did to one request."""

    input_tokens_before: int = 0
    input_tokens_after: int = 0
    stage_savings: dict[str, int] = field(default_factory=dict)
    model: str = "unknown"
    routing_decision: RoutingDecision | None = None

    @property
    def input_tokens_saved(self) -> int:
        return max(0, self.input_tokens_before - self.input_tokens_after)


def _request_input_tokens(request: dict) -> int:
    """Estimate total input tokens for an Anthropic request body."""
    total = 0
    system = request.get("system")
    total += _content_tokens(system)
    for message in request.get("messages", []) or []:
        total += _content_tokens(message.get("content"))
    return total


def _content_tokens(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return count_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += count_tokens(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    total += _content_tokens(block.get("content"))
                elif "text" in block and isinstance(block["text"], str):
                    total += count_tokens(block["text"])
            elif isinstance(block, str):
                total += count_tokens(block)
        return total
    return 0


class OptimizationPipeline:
    def __init__(self, config: Config):
        self.config = config

    def process(self, request: dict) -> tuple[dict, PipelineReport]:
        report = PipelineReport(model=request.get("model", "unknown"))
        try:
            new_request = copy.deepcopy(request)
        except Exception:
            # If the body cannot be copied, do not risk mutating it.
            return request, report

        report.input_tokens_before = _request_input_tokens(new_request)

        # ---- Stage 0: smart routing (runs first, on the original shape) ----
        if self.config.routing.enabled:
            try:
                router = SmartRouter(
                    tier_models={
                        "trivial": self.config.routing.trivial_model,
                        "moderate": self.config.routing.moderate_model,
                    },
                    confidence_threshold=self.config.routing.confidence_threshold,
                )
                decision = router.classify(new_request)
                if decision.applied:
                    new_request["model"] = decision.routed_model
                report.routing_decision = decision
            except Exception:
                # Fail open: a classifier error must never block the request.
                report.routing_decision = None

        # ---- Stage 0.5: conversation history trimming ----------------------
        if self.config.history.enabled:
            try:
                trimmer = ConversationHistoryTrimmer(
                    self.config.history.retained_window
                )
                result = trimmer.trim(new_request.get("messages", []))
                if result.applied:
                    new_request["messages"] = result.messages
                    report.stage_savings["conversation_history_trimming"] = (
                        result.tokens_saved
                    )
            except Exception:
                # Fail open: a trimmer error must never discard or corrupt
                # conversation history; leave messages untouched.
                pass

        if self.config.compression.enabled:
            saved = self._compress_context(new_request, self.config.compression.mode)
            if saved:
                report.stage_savings["context_compression"] = saved

        if self.config.output_constraints.enabled:
            self._apply_output_constraints(
                new_request, self.config.output_constraints.level
            )
            # Output-constraint savings are on the response side and cannot be
            # measured here; recorded as 0 for input accounting.
            report.stage_savings.setdefault("output_constraints", 0)

        if self.config.prefix.enabled:
            self._stabilize_prefix(new_request, self.config.prefix.min_prefix_tokens)
            report.stage_savings.setdefault("prefix_stabilization", 0)

        report.input_tokens_after = _request_input_tokens(new_request)
        return new_request, report

    # ---- Stage 1: context compression -------------------------------------

    def _compress_context(self, request: dict, mode: str) -> int:
        total_saved = 0
        messages = request.get("messages")
        if not isinstance(messages, list):
            return 0

        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                result = compress_block(content, mode)
                if result.applied:
                    message["content"] = result.text
                    total_saved += result.tokens_saved
            elif isinstance(content, list):
                for block in content:
                    total_saved += self._compress_block_in_place(block, mode)
        return total_saved

    def _compress_block_in_place(self, block, mode: str) -> int:
        if not isinstance(block, dict):
            return 0
        block_type = block.get("type")

        if block_type == "text" and isinstance(block.get("text"), str):
            result = compress_block(block["text"], mode)
            if result.applied:
                block["text"] = result.text
                return result.tokens_saved
            return 0

        if block_type == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                result = compress_block(inner, mode)
                if result.applied:
                    block["content"] = result.text
                    return result.tokens_saved
                return 0
            if isinstance(inner, list):
                saved = 0
                for inner_block in inner:
                    saved += self._compress_block_in_place(inner_block, mode)
                return saved
        return 0

    # ---- Stage 2: output constraints --------------------------------------

    def _apply_output_constraints(self, request: dict, level: str) -> None:
        instruction = _OUTPUT_INSTRUCTIONS.get(level, _OUTPUT_INSTRUCTIONS["balanced"])
        system = request.get("system")

        if system is None or system == "":
            request["system"] = instruction
        elif isinstance(system, str):
            request["system"] = f"{system}\n\n{instruction}"
        elif isinstance(system, list):
            system.append({"type": "text", "text": instruction})

    # ---- Stage 3: prefix stabilization ------------------------------------

    def _stabilize_prefix(self, request: dict, min_tokens: int) -> None:
        system = request.get("system")
        if system is None:
            return

        if isinstance(system, str):
            if count_tokens(system) < min_tokens:
                return
            request["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            return

        if isinstance(system, list) and system:
            # Tag the last text block that has no cache_control yet, provided the
            # cumulative system prompt is large enough to be cacheable.
            if _content_tokens(system) < min_tokens:
                return
            for block in reversed(system):
                if isinstance(block, dict) and block.get("type") == "text":
                    block.setdefault("cache_control", {"type": "ephemeral"})
                    break
