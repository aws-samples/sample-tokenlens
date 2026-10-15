"""Smart Routing: local, deterministic request-complexity classification.

The Smart Router classifies an outbound request into a Complexity Tier
(``trivial``, ``moderate``, or ``complex``) using local heuristics only (no
network call, no bundled ML model) and, where configured, indicates that the
request's target model should be rewritten to a cheaper model appropriate
for that tier.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tokens import count_message_tokens, count_tokens

# Complexity-score band boundaries. Scores in [0, LOW) are trivial,
# [LOW, HIGH) are moderate, and [HIGH, 1] are complex.
_TRIVIAL_MAX = 0.34
_MODERATE_MAX = 0.67

# Signal-normalization scales. A request at or above the scale contributes the
# full weight of that signal to the complexity score.
_TOKEN_SCALE = 4000.0
_MESSAGE_SCALE = 20.0


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of Smart Router processing for one request."""

    tier: str  # "trivial" | "moderate" | "complex"
    confidence: float  # 0.0 - 1.0
    original_model: str
    routed_model: str  # == original_model if no mapping or tier == "complex"
    applied: bool  # True if routed_model != original_model


class SmartRouter:
    """Local, deterministic request-complexity classifier.

    Classification is a pure function of the request content: the same request
    and configuration always yield the same tier and confidence. Malformed
    input degrades to the ``complex`` tier with confidence ``0.0`` rather than
    raising, so callers always receive a safe decision object.
    """

    def __init__(self, tier_models: dict[str, str], confidence_threshold: float):
        # Only trivial/moderate tiers may map to a substitute model; a missing
        # or None entry means "do not rewrite this tier."
        self._tier_models = {
            tier: model
            for tier, model in (tier_models or {}).items()
            if model
        }
        self._confidence_threshold = confidence_threshold

    def classify(self, request: dict) -> RoutingDecision:
        """Classify a request into a Complexity Tier and apply tier routing."""
        original_model = self._extract_model(request)
        if original_model is None:
            # Non-dict request, or missing/non-string model: low-signal input.
            return RoutingDecision(
                tier="complex",
                confidence=0.0,
                original_model=self._model_or_empty(request),
                routed_model=self._model_or_empty(request),
                applied=False,
            )

        messages = request.get("messages")
        if not isinstance(messages, list) or len(messages) == 0:
            # No conversation to classify: treat as low-signal input.
            return RoutingDecision(
                tier="complex",
                confidence=0.0,
                original_model=original_model,
                routed_model=original_model,
                applied=False,
            )

        score = self._complexity_score(request, messages)
        tier = self._score_to_tier(score)
        confidence = self._confidence(score)

        # Force the safe tier whenever the classifier is not confident enough.
        if confidence < self._confidence_threshold:
            tier = "complex"

        routed_model = original_model
        if tier in ("trivial", "moderate"):
            mapped = self._tier_models.get(tier)
            if mapped:
                routed_model = mapped

        return RoutingDecision(
            tier=tier,
            confidence=confidence,
            original_model=original_model,
            routed_model=routed_model,
            applied=routed_model != original_model,
        )

    # ---- Malformed-input helpers ------------------------------------------

    @staticmethod
    def _extract_model(request) -> str | None:
        if not isinstance(request, dict):
            return None
        model = request.get("model")
        if not isinstance(model, str) or not model:
            return None
        return model

    @staticmethod
    def _model_or_empty(request) -> str:
        if isinstance(request, dict):
            model = request.get("model")
            if isinstance(model, str):
                return model
        return ""

    # ---- Heuristic scoring ------------------------------------------------

    def _complexity_score(self, request: dict, messages: list) -> float:
        """Combine local signals into a complexity score in [0, 1]."""
        tokens = count_message_tokens(
            [m for m in messages if isinstance(m, dict)]
        )
        system = request.get("system")
        if isinstance(system, str):
            tokens += count_tokens(system)

        num_messages = sum(1 for m in messages if isinstance(m, dict))

        score = 0.0
        score += min(tokens / _TOKEN_SCALE, 1.0) * 0.5
        score += min(num_messages / _MESSAGE_SCALE, 1.0) * 0.2
        if self._has_code_fence(messages):
            score += 0.2
        if self._has_tool_result(messages):
            score += 0.2
        # A short trailing question is a signal of a simple, answerable ask.
        if self._ends_with_short_question(messages, tokens):
            score -= 0.15

        return _clamp(score, 0.0, 1.0)

    @staticmethod
    def _score_to_tier(score: float) -> str:
        if score < _TRIVIAL_MAX:
            return "trivial"
        if score < _MODERATE_MAX:
            return "moderate"
        return "complex"

    @staticmethod
    def _confidence(score: float) -> float:
        """Confidence is how far the score sits from the nearest band boundary.

        Scores deep inside a band are classified with high confidence; scores
        near a boundary are ambiguous and yield low confidence.
        """
        distance = min(
            abs(score - _TRIVIAL_MAX),
            abs(score - _MODERATE_MAX),
        )
        # Half a band width is the deepest a score can sit inside a band.
        half_band = _TRIVIAL_MAX / 2.0
        return _clamp(distance / half_band, 0.0, 1.0)

    # ---- Signal detection -------------------------------------------------

    @classmethod
    def _has_code_fence(cls, messages: list) -> bool:
        for text in cls._iter_text(messages):
            if "```" in text:
                return True
        return False

    @staticmethod
    def _has_tool_result(messages: list) -> bool:
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        return True
        return False

    @classmethod
    def _ends_with_short_question(cls, messages: list, tokens: int) -> bool:
        last_user_text = None
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                text = cls._message_text(message)
                if text:
                    last_user_text = text
        if last_user_text is None:
            return False
        return tokens < _TOKEN_SCALE and last_user_text.strip().endswith("?")

    @classmethod
    def _iter_text(cls, messages: list):
        for message in messages:
            if isinstance(message, dict):
                text = cls._message_text(message)
                if text:
                    yield text

    @staticmethod
    def _message_text(message: dict) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    inner = block.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        for inner_block in inner:
                            if isinstance(inner_block, dict) and isinstance(
                                inner_block.get("text"), str
                            ):
                                parts.append(inner_block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return ""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
