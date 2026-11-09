"""Explicit local-only numeric aggregation of a user-supplied authorized JSON cohort.

This CLI is NOT a saved-log authorization endpoint. It never fetches logs, opens
the OWG database, sends data to a provider, or grants access to another user.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .event_log_metric_projection import summarize_projected

MAX_INPUT_BYTES = 256 * 1024


def aggregate_file(path: Path) -> dict:
    """Read a bounded local JSON projection request; return aggregate only."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("input must be a regular local file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input exceeds local projection byte budget")
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds local projection byte budget")
    request = json.loads(data)
    if not isinstance(request, dict) or set(request) != {"definition", "numeric_fields", "rows"}:
        raise ValueError("expected definition, numeric_fields and rows only")
    fields = request["numeric_fields"]
    if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields) or len(fields) != len(set(fields)):
        raise ValueError("numeric_fields must be distinct strings")
    return summarize_projected(request["definition"], request["rows"], set(fields))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate a manually authorized local numeric cohort; no network or saved-log access")
    parser.add_argument("input", type=Path, help="local JSON projection request")
    args = parser.parse_args(argv)
    try:
        result = aggregate_file(args.input)
    except (ValueError, TypeError, OSError, UnicodeError, OverflowError, KeyError, ArithmeticError):
        # Never echo source paths, values, or exception messages: these may contain private data.
        print("Projection refused: invalid, unsafe or inaccessible local input", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
