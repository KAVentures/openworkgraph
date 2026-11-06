"""Offline selected Salesforce CaseHistory CSV projection; no network or credentials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Keep direct `python scripts/salesforce_case_history.py` usable, including
# isolated mode. The shared projector is included in desktop installations.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.salesforce_case_history import (
    ID, MAX_BYTES, MAX_CASES, MAX_ROWS, REQUIRED, ExportError, _key, project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--account', required=True, help='Stable, caller-declared account namespace')
    parser.add_argument('--case', action='append', required=True, dest='cases', help='Literal CaseId; repeat up to 32')
    parser.add_argument('--output', type=Path, required=True, help='New CSV path; existing files are refused')
    args = parser.parse_args()
    try:
        with args.input.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        content, report = project(raw, account=args.account, cases=args.cases)
        with args.output.open('x', encoding='utf-8', newline='') as handle:
            handle.write(content)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ExportError):
        print(json.dumps({'status': 'refused', 'reason': 'Check export columns, selected rows, bounds and a new output path'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
