#!/usr/bin/env python3
"""Repository wrapper for the packaged TokenLens Kiro read-routing hook."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
main = importlib.import_module("tokenlens.kiro_hook").main


if __name__ == "__main__":
    raise SystemExit(main())
