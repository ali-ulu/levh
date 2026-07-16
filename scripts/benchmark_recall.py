"""Source-tree wrapper for the packaged recall benchmark harness."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core.benchmark import DATASET, DISTRACTORS, main, run_benchmark

__all__ = ["DATASET", "DISTRACTORS", "main", "run_benchmark"]

if __name__ == "__main__":
    raise SystemExit(main())
