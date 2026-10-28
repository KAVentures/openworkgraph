"""Owned synthetic bounded Jira rescan/replay oracle; no live provider calls."""
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

histories = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
items = int(sys.argv[2]) if len(sys.argv) > 2 else 12
assert 1 <= histories <= 1000 and 1 <= items <= 30
with tempfile.TemporaryDirectory(prefix='owg-jira-oracle-') as directory:
    os.environ.update(WORKFLOW_OBSERVER_DATA=directory, WORKFLOW_OBSERVER_AUTH_DIR=str(Path(directory)/'auth'))
    from test_jira_retained_ingestion import Provider, ENV, HOST, change
    from server import event_log_workspace as workspace
    from server import jira_retained_profiles as profiles
    from server.jira_retained_source import JiraIngestionError
    workspace.DATA_DIR = Path(directory)
    os.environ.update(ENV)
    profiles.ensure_api_token = lambda: 'owned-benchmark-hmac-key'
    refs = ['owg:r:' + format(i, '032x') for i in range(1, 9)]
    profiles.resolve_resource_reference = lambda ref: {'provider': 'jira', 'resource_kind': 'issue',
        'host': HOST, 'resolver_locator': 'OWG-' + str(refs.index(ref) + 1)}
    provider = Provider(); provider.page_size = 100; provider.histories = {}
    expected = Counter()
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    for case in range(1, 9):
        key = 'OWG-' + str(case)
        provider.histories[key] = []
        expected[(str(case), 'Issue created', '2026-01-01T00:00:00+00:00')] += 1
        for history in range(histories):
            stamp = (base + timedelta(seconds=history)).isoformat()
            row = change(str(history + 1), at=stamp)
            row['items'] = [dict(fieldId='status', field='status', fieldtype='jira',
                                 **{'from': str(item + 1), 'to': str(item + 2), 'toString': 'State ' + str(item)})
                            for item in range(items)]
            provider.histories[key].append(row)
            for item in range(items):
                expected[(str(case), 'Status: State ' + str(item), stamp)] += 1
    start = time.monotonic()
    profile = profiles.create(refs, confirm_reads=True, confirm_retention=True, confirm_status_labels=True)['profile_id']
    first = profiles.run(profile, confirm_reads=True, client_factory=provider.client)
    first_seconds = time.monotonic() - start
    output = StringIO(newline='');workspace.export_log(first['log_id'], 'sourcecsv', output, 'source_case')
    rows = list(csv.DictReader(StringIO(output.getvalue())))
    actual = Counter((r['case_id'], r['activity'], datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00')).isoformat()) for r in rows)
    if actual != expected:
        print({'missing_examples': list((expected-actual).items())[:2], 'extra_examples': list((actual-expected).items())[:2]}, file=sys.stderr)
    assert actual == expected
    assert len({(r['source_namespace'], r['source_event_id']) for r in rows}) == len(rows)
    start = time.monotonic()
    replay = profiles.run(profile, confirm_reads=True, client_factory=provider.client)
    replay_seconds = time.monotonic() - start
    assert replay['added_events'] == 0 and replay['duplicate_events_skipped'] == len(rows)
    provider.histories['OWG-8'][0]['items'][0]['toString'] = 'Corrected'
    try:
        profiles.run(profile, confirm_reads=True, client_factory=provider.client)
        raise AssertionError('correction accepted')
    except JiraIngestionError as error:
        assert str(error) == 'retained_source_payload_conflict'
    assert workspace.list_logs()['logs'][0]['events'] == len(rows)
    print(json.dumps({'synthetic': True, 'provider_network_calls': 0,
        'cases': 8, 'histories_per_case': histories, 'status_items_per_history': items, 'events': len(rows),
        'read_retain_seconds': round(first_seconds, 3), 'full_rescan_replay_seconds': round(replay_seconds, 3),
        'requests_per_run': first['checkpoint']['requests'],
        'max_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        'whole_export_oracle_passed': True, 'replay_passed': True, 'correction_refused': True}))
