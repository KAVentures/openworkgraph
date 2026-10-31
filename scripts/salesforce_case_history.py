"""Offline selected Salesforce CaseHistory CSV projection; no network or credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000
MAX_CASES = 32
ID = re.compile(r'[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?\Z')
REQUIRED = {'Id', 'CaseId', 'CreatedDate', 'Field', 'NewValue'}


class ExportError(ValueError):
    """Counts-only error, never an exported business value."""


def _key(namespace: str, kind: str, identifier: str) -> str:
    return 'sf:' + hashlib.sha256((namespace + '\0' + kind + '\0' + identifier).encode()).hexdigest()


def project(raw: bytes, *, account: str, cases: list[str]) -> tuple[str, dict]:
    """Return canonical CSV and a sample-free report, or refuse the entire export."""
    if (not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', account)
            or not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES
            or any(not isinstance(c, str) or not ID.fullmatch(c) for c in cases)
            or len(set(cases)) != len(cases)):
        raise ExportError('Supply one stable account namespace and 1–32 distinct literal case IDs')
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ExportError('Input exceeds the 16 MiB export limit')
    try:
        text = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
        headers = reader.fieldnames
        if not headers or len(headers) > 64 or len(headers) != len(set(headers)) or not REQUIRED <= set(headers):
            raise ExportError('Expected unique CaseHistory column headers')
        selected = set(cases)
        events = {}
        counts = dict(input_rows=0, unselected_rows=0, excluded_fields=0, duplicate_rows=0)
        for row in reader:
            counts['input_rows'] += 1
            if counts['input_rows'] > MAX_ROWS:
                raise ExportError('Input exceeds the 100,000-row export limit')
            if None in row or any(value is None for value in row.values()):
                raise ExportError('CSV row does not match its headers')
            if row['CaseId'] not in selected:
                counts['unselected_rows'] += 1
                continue
            if row['Field'] not in {'Status', 'created'}:
                counts['excluded_fields'] += 1
                continue
            if not ID.fullmatch(row['Id']):
                raise ExportError('A selected history row has an invalid identifier')
            stamp = row['CreatedDate']
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})', stamp):
                raise ExportError('A selected history row needs an explicit timezone timestamp')
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(timezone.utc)
            activity = 'Case created'
            if row['Field'] == 'Status':
                status = row['NewValue']
                if not status or len(status) > 80 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in status):
                    raise ExportError('A selected status value is empty, too long or contains controls')
                activity = 'Status: ' + status
            event_id = _key(account, 'history', row['Id'])
            event = (_key(account, 'case', row['CaseId']), activity, moment.isoformat(), event_id)
            if event_id in events:
                if events[event_id] != event:
                    raise ExportError('A history identifier has conflicting projected values')
                counts['duplicate_rows'] += 1
            events[event_id] = event
    except (UnicodeError, csv.Error, OverflowError, ValueError) as exc:
        if isinstance(exc, ExportError):
            raise
        raise ExportError('Invalid CSV encoding, syntax or selected timestamp') from None
    if not events:
        raise ExportError('No selected creation or status events were found')
    ordered = sorted(events.values(), key=lambda event: (event[0], event[2], event[3]))
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['case_id', 'activity', 'timestamp', 'source_event_id'])
    writer.writerows(ordered)
    times = [(event[0], event[2]) for event in ordered]
    report = {'format': 'openworkgraph.salesforce-case-history-report.v1', **counts,
              'selected_cases': len(cases), 'represented_cases': len({e[0] for e in ordered}),
              'events': len(events), 'tied_timestamp_events': len(times) - len(set(times)),
              'projection': 'creation_and_status_only', 'source_completeness': 'unknown',
              'identity_basis': 'caller_declared_account_and_literal_ids',
              'business_writes_performed': False}
    return output.getvalue(), report


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
