"""JSON output compressor.

Tool calls frequently return large JSON arrays (search hits, DB rows, list
responses) where the middle of the array carries little marginal information.
This compressor keeps a sample from the head and tail of long homogeneous
arrays and records how many items were omitted, so the model still knows the
true size of the result set.
"""

from __future__ import annotations

import json

from . import Compressor

# Arrays longer than this are candidates for sampling.
_ARRAY_THRESHOLD = 20
# How many items to keep from the head and from the tail.
_HEAD_KEEP = 5
_TAIL_KEEP = 3
# In aggressive mode, keep fewer.
_HEAD_KEEP_AGGRESSIVE = 3
_TAIL_KEEP_AGGRESSIVE = 2


class JsonCompressor(Compressor):
    name = "json"

    def matches(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped or stripped[0] not in "[{":
            return False
        try:
            json.loads(stripped)
            return True
        except (ValueError, TypeError):
            return False

    def _compress(self, text: str, mode: str) -> str:
        data = json.loads(text.strip())
        head, tail = self._keep_sizes(mode)
        compacted = self._compact(data, head, tail)
        # Compact separators remove insignificant whitespace as an extra win.
        return json.dumps(compacted, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _keep_sizes(mode: str) -> tuple[int, int]:
        if mode == "aggressive":
            return _HEAD_KEEP_AGGRESSIVE, _TAIL_KEEP_AGGRESSIVE
        return _HEAD_KEEP, _TAIL_KEEP

    def _compact(self, node, head: int, tail: int):
        if isinstance(node, list):
            if len(node) > _ARRAY_THRESHOLD:
                omitted = len(node) - head - tail
                kept_head = [self._compact(item, head, tail) for item in node[:head]]
                kept_tail = [self._compact(item, head, tail) for item in node[-tail:]]
                marker = {
                    "__tokenlens_omitted__": omitted,
                    "__note__": f"{omitted} of {len(node)} items omitted for brevity",
                }
                return kept_head + [marker] + kept_tail
            return [self._compact(item, head, tail) for item in node]
        if isinstance(node, dict):
            return {key: self._compact(value, head, tail) for key, value in node.items()}
        return node
