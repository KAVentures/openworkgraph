"""Duration bands measure retained case histories, not work or outcomes."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_duration_distribution as subject
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_data(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def imported_log():
    rows = [
        'case,activity,time,resource',
        'c0,A,2024-01-01T00:00:00Z,one',
        'c1,A,2024-01-01T00:00:00.900000Z,one',
        'c1,B,2024-01-01T01:00:01.100000+01:00,one',
        'c2,A,2024-01-01T00:00:00Z,two',
        'c2,B,2024-01-01T00:00:01Z,two',
        'c3,A,2024-01-01T00:00:00Z,two',
        'c3,B,2024-01-01T00:00:01.999999Z,two',
        'c4,A,2024-01-01T00:00:00Z,one',
        'c4,B,2024-01-01T00:00:02Z,one',
        'c5,A,2024-01-01T00:00:00Z,one',
        'c5,B,2024-01-01T00:00:05Z,one',
    ]
    return workspace.import_log(BytesIO(('\n'.join(rows)).encode()), name='duration-fixture', source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time',
                 'resource_column':'resource'})['log_id']


def test_exact_disjoint_bands_handle_boundaries_and_fractional_timestamps():
    result = subject.distribution(imported_log(), [1, 2])
    assert result['cases'] == 6 and result['single_event_cases'] == 1
    assert result['cutoffs_seconds'] == [1, 2]
    assert result['bands'] == [
        {'lower_seconds': 0, 'upper_seconds': 1, 'cases': 2, 'share': 2/6},
        {'lower_seconds': 1, 'upper_seconds': 2, 'cases': 2, 'share': 2/6},
        {'lower_seconds': 2, 'upper_seconds': None, 'cases': 2, 'share': 2/6},
    ]
    assert result['observed_duration_seconds']['minimum'] == 0
    assert result['observed_duration_seconds']['maximum'] == 5
    assert result['observed_duration_seconds']['median'] == pytest.approx(1.4999995)
    assert 'not employee work time' in result['interpretation']
    assert result['execution_authorized'] is False


def test_filtered_and_empty_populations_preserve_whole_cases():
    log = imported_log()
    result = subject.distribution(log, [1, 2], cohort={'resource': 'two'})
    assert result['cases_before_filter'] == 6
    assert result['cases'] == 2 and result['single_event_cases'] == 0
    assert [band['cases'] for band in result['bands']] == [0, 2, 0]
    assert sum(b['cases'] for b in result['bands']) == result['cases']
    follows = {'follows': [{'from': 'A', 'to': 'B', 'mode':'direct'}]}
    result = subject.distribution(log, [2], cohort=follows)
    assert result['cases_before_filter'] == 6 and result['cases'] == 5
    assert [b['cases'] for b in result['bands']] == [3, 2]
    empty = subject.distribution(log, [1], cohort={'activity': 'absent'})
    assert empty['cases'] == 0 and empty['cases_before_filter'] == 6
    assert all(b['cases'] == 0 and b['share'] is None for b in empty['bands'])
    assert all(x is None for x in empty['observed_duration_seconds'].values())


@pytest.mark.parametrize('thresholds', [None, [], [0], [-1], [1, 1], [2, 1], [True], [1.2],
                                      [1, '2'], [subject.MAX_CUTOFF_SECONDS + 1], list(range(1, 12))])
def test_malformed_or_unbounded_thresholds_are_refused(thresholds):
    with pytest.raises(workspace.EventLogWorkspaceError):
        subject.distribution('not-a-log', thresholds)


def test_dashboard_route_authentication_and_strict_input(tmp_path):
    script = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
csv = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T00:00:02Z\n"
with TestClient(app) as client:
    auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=duration&case_column=case&activity_column=activity&timestamp_column=time", content=csv, headers={**auth,"x-owg-local-storage-confirmed":"true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"] + "/duration-distribution"
    assert client.get(base).status_code in (401, 403)
    assert client.get(base, headers={"Authorization":"Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    assert client.get(base, headers=auth, params={"cutoffs_seconds":"2,1"}).status_code == 422
    assert client.get(base, headers=auth, params={"cutoffs_seconds":"1,nan"}).status_code == 422
    r = client.get(base, headers=auth, params={"cutoffs_seconds":"1,2"})
    assert r.status_code == 200, r.text
    assert [b["cases"] for b in r.json()["bands"]] == [0,0,1]
    assert r.headers["cache-control"] == "no-store"
'''
    env = os.environ.copy()
    env.update({'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'),
                'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth'),
                'WORKFLOW_OBSERVER_CONFIG': str(tmp_path / 'config.json')})
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_object_case_notion_changes_population_without_merging_objects():
    import json
    doc = {
        'objectTypes': [{'name':'order', 'attributes':[]}, {'name':'invoice', 'attributes':[]}],
        'eventTypes': [],
        'objects': [{'id':'o1', 'type':'order'}, {'id':'o2', 'type':'order'},
                    {'id':'i1', 'type':'invoice'}],
        'events': [
            {'id':'e1','type':'Open','time':'2024-01-01T00:00:00Z',
             'relationships':[{'objectId':'o1','qualifier':''},{'objectId':'i1','qualifier':''}]},
            {'id':'e2','type':'Check','time':'2024-01-01T00:00:05Z',
             'relationships':[{'objectId':'o1','qualifier':''},{'objectId':'i1','qualifier':''}]},
            {'id':'e3','type':'Open','time':'2024-01-01T00:00:09Z',
             'relationships':[{'objectId':'o2','qualifier':''}]},
            {'id':'e4','type':'Pay','time':'2024-01-01T00:00:15Z',
             'relationships':[{'objectId':'i1','qualifier':''}]},
        ]}
    ident = workspace.import_log(BytesIO(json.dumps(doc).encode()), name='objects', source_format='ocel')['log_id']
    orders = subject.distribution(ident, [5], case_type='order')
    invoices = subject.distribution(ident, [5], case_type='invoice')
    assert orders['case_notion'] == 'order' and orders['cases'] == 2
    assert orders['single_event_cases'] == 1 and [b['cases'] for b in orders['bands']] == [1, 1]
    assert invoices['case_notion'] == 'invoice' and invoices['cases'] == 1
    assert invoices['single_event_cases'] == 0 and [b['cases'] for b in invoices['bands']] == [0, 1]
