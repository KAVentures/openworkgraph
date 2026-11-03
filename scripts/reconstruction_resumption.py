"""Local-only, create-only interruption/resumption reconstruction command.

Usage: python -m scripts.reconstruction_resumption --events E --packet P --output O
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from scripts.reconstruction_review import _read_json, _write_private
from server.reconstruction_resumption import reconstruct_resumptions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-anchored local case resumption graph")
    parser.add_argument("--events", required=True, help="authorized local observation JSON")
    parser.add_argument("--packet", required=True, help="exact prepare review packet JSON")
    parser.add_argument("--output", required=True, help="new private JSON destination")
    args = parser.parse_args(argv)
    try:
        events = _read_json(args.events)
        packet = _read_json(args.packet, max_bytes=1024 * 1024)
        result = reconstruct_resumptions(events, packet)
        _write_private(args.output, result)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Resumption reconstruction refused: {exc}", file=sys.stderr)
        return 2
    print(f"Created private resumption graph: {Path(args.output).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
