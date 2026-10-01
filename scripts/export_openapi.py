#!/usr/bin/env python3
"""Regenerate the committed OpenAPI contract from the live app.

The contract is frozen by ``tests/test_openapi_contract.py``: it fails when
``app.openapi()`` differs from the committed ``openapi.json``. This script is
how you make them agree again after a deliberate API change — the regeneration
belongs in the same commit as the change that alters the schema.

Usage:
    python scripts/export_openapi.py           # rewrite openapi.json
    python scripts/export_openapi.py --check   # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "openapi.json"


def _current() -> dict:
    from server.api import app

    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if openapi.json differs from the live schema, instead of rewriting it",
    )
    args = parser.parse_args(argv)

    current = _current()
    committed = json.loads(CONTRACT.read_text(encoding="utf-8"))

    if json.dumps(current, sort_keys=True) == json.dumps(committed, sort_keys=True):
        print("openapi.json is up to date.")
        return 0

    if args.check:
        print(
            "openapi.json is stale relative to the live schema; "
            "run `python scripts/export_openapi.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    # The contract is committed with LF endings. It was briefly CRLF (a Windows
    # checkout wrote it) and this script kept emitting CRLF for it long after,
    # which turned every regeneration into a whole-file diff and hid the real
    # schema change. The freeze test compares parsed JSON, so match the tree's
    # actual ending to keep the diff readable.
    text = json.dumps(current, indent=2) + "\n"
    CONTRACT.write_bytes(text.encode("utf-8"))
    print(f"openapi.json regenerated ({len(current['paths'])} paths).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
