"""Milestone funnel over an imported event log."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_funnel as fn
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(cases):
    """``cases`` is a list of [(activity, hour offset), …]; case i starts on day i."""
    rows = ["case,activity,time"]
    for i, steps in enumerate(cases):
        for activity, hour in steps:
            day, h = divmod(i * 24 + hour, 24)
            rows.append(f"c{i},{activity},2024-01-{1 + day:02d}T{h:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_steps_conversion_timing_and_stops():
    full = [("Lead", 0), ("Call", 2), ("Offer", 6), ("Won", 10)]
    cases = [full] * 6 + [[("Lead", 0), ("Call", 4), ("Lost", 5)]] * 3 + [[("Lead", 0), ("Spam", 1)]] * 2 \
        + [[("Offer", 0), ("Lead", 1), ("Call", 2)]]  # Offer before Lead does not count for step 3
    log = _import(cases)
    r = fn.funnel(log, ["Lead", "Call", "Offer", "Won"])
    assert r["cases"] == 12
    assert [s["cases"] for s in r["steps"]] == [12, 10, 6, 6]
    lead, call, offer, won = r["steps"]
    assert call["conversion_from_previous"] == round(10 / 12, 4) and call["dropped_after_previous"] == 2
    assert call["stopped_cases_last_activity"] == [{"activity": "Spam", "cases": 2}]
    assert offer["dropped_after_previous"] == 4
    assert offer["stopped_cases_last_activity"] == [{"activity": "Lost", "cases": 3}, {"activity": "Call", "cases": 1}]
    assert offer["hours_from_previous"]["median"] == 4 and call["hours_from_previous"]["median"] == 2
    assert won["share_of_first_step"] == 0.5 and r["completed"]["hours_first_to_last"]["median"] == 10
    assert r["largest_drop"] == {"after_step": 2, "to_step": 3, "cases": 4}


def test_conversion_window_restarts_on_a_later_previous_step():
    cases = [[("Apply", 0), ("Apply", 30), ("Approve", 32)],   # second Apply restarts the clock: within 24h
             [("Apply", 0), ("Approve", 30)],                  # 30h later: outside the window
             [("Apply", 0), ("Approve", 5)]]
    log = _import(cases)
    assert [s["cases"] for s in fn.funnel(log, ["Apply", "Approve"])["steps"]] == [3, 3]
    windowed = fn.funnel(log, ["Apply", "Approve"], within_hours=24)
    assert [s["cases"] for s in windowed["steps"]] == [3, 2]
    assert windowed["steps"][1]["hours_from_previous"]["median"] == 3.5


def test_bad_requests_are_refused():
    log = _import([[("A", 0), ("B", 1)]])
    for steps in (["A"], ["A", "B"] * 5):
        with pytest.raises(EventLogWorkspaceError, match="steps"):
            fn.funnel(log, steps)
    with pytest.raises(EventLogWorkspaceError, match="not in this selection: Z"):
        fn.funnel(log, ["A", "Z"])
    with pytest.raises(EventLogWorkspaceError, match="window"):
        fn.funnel(log, ["A", "B"], within_hours=0)
    # Preserve current main's stricter input contract while integrating #1111.
    # The superseded branch coerced non-string step values to strings.
    with pytest.raises(EventLogWorkspaceError, match="steps"):
        fn.funnel(log, ("A", "B"), evidence_step=2)
    with pytest.raises(EventLogWorkspaceError, match="each step"):
        fn.funnel(log, [1, "B"], evidence_step=2)
    for steps in ("AB", {"A": 1, "B": 2}, ["A", None], ["A", 1]):
        with pytest.raises(EventLogWorkspaceError):
            fn.funnel(log, steps)


def test_case_evidence_matches_funnel_and_filters():
    log = _import([[('A', 0), ('B', 1), ('C', 2)],
                   [('A', 0), ('B', 1), ('Lost', 2)],
                   [('A', 0), ('Lost', 1)], [('B', 0)]])
    reached = fn.funnel(log, ['A', 'B', 'C'], evidence_step=2)
    assert reached['evidence'] == {'step': 2, 'status': 'reached', 'total': 2,
        'cases': [{'case_id': 'c0', 'last_activity': 'C', 'steps_reached': 3},
                  {'case_id': 'c1', 'last_activity': 'Lost', 'steps_reached': 2}], 'truncated': False}
    dropped = fn.funnel(log, ['A', 'B', 'C'], evidence_step=3, evidence_status='dropped')
    assert dropped['evidence']['total'] == dropped['steps'][2]['dropped_after_previous'] == 1
    assert dropped['evidence']['cases'][0]['case_id'] == 'c1'
    filtered = fn.funnel(log, ['A', 'B'], cohort={'activity': 'C'}, evidence_step=2)
    assert [c['case_id'] for c in filtered['evidence']['cases']] == ['c0']
    with pytest.raises(EventLogWorkspaceError):
        fn.funnel(log, ['A', 'B'], evidence_step=1, evidence_status='dropped')
    with pytest.raises(EventLogWorkspaceError):
        fn.funnel(log, ['A', 'B'], evidence_step=3)


def test_case_evidence_is_bounded_and_ordered():
    rows = ['case,activity,time']
    for i in range(55):
        rows.extend([f'c{i:02},A,2024-01-01T00:00:00Z', f'c{i:02},B,2024-01-01T01:00:00Z'])
    log = workspace.import_log(BytesIO('\n'.join(rows).encode()), name='many', source_format='csv',
                               mapping=MAPPING)['log_id']
    evidence = fn.funnel(log, ['A', 'B'], evidence_step=2)['evidence']
    assert evidence['total'] == 55 and evidence['truncated'] and len(evidence['cases']) == 50
    assert [c['case_id'] for c in evidence['cases']] == [f'c{i:02}' for i in range(50)]


def test_case_evidence_requires_dashboard_session(tmp_path):
    import os
    import subprocess
    import sys
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session()}
    made = client.post('/v1/event-logs/import?format=csv&name=f&case_column=case&activity_column=activity&timestamp_column=time',
        content=b'case,activity,time\nc1,A,2024-01-01T00:00:00Z\nc1,B,2024-01-01T01:00:00Z\n',
        headers={**owner, 'x-owg-local-storage-confirmed': 'true'})
    assert made.status_code == 200, made.text
    url = '/v1/event-logs/' + made.json()['log_id'] + '/funnel?step=A&step=B&evidence_step=2'
    assert client.get(url).status_code in (401, 403)
    result = client.get(url, headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()['evidence']['cases'][0]['case_id'] == 'c1'
    paged = url + '&evidence_page=true'
    assert client.get(paged).status_code in (401, 403)
    assert len(client.get(paged, headers=owner).json()['evidence']['revision']) == 64
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_paged_evidence_covers_all_cases_and_refuses_changed_list():
    rows = ['case,activity,time']
    for i in range(105):
        rows.extend([f'c{i:03},A,2024-01-01T00:00:00Z', f'c{i:03},B,2024-01-01T01:00:00Z'])
    log = workspace.import_log(BytesIO('\n'.join(rows).encode()), name='pages', source_format='csv', mapping=MAPPING)['log_id']
    args = {'evidence_step': 2, 'evidence_page': True}
    first = fn.funnel(log, ['A', 'B'], **args)['evidence']
    second = fn.funnel(log, ['A', 'B'], **args, evidence_after=first['next_after'],
                       evidence_revision=first['revision'])['evidence']
    third = fn.funnel(log, ['A', 'B'], **args, evidence_after=second['next_after'],
                      evidence_revision=first['revision'])['evidence']
    assert [len(page['cases']) for page in (first, second, third)] == [50, 50, 5]
    assert [case['case_id'] for page in (first, second, third) for case in page['cases']] == [f'c{i:03}' for i in range(105)]
    assert third['next_after'] is None and first['total'] == second['total'] == third['total'] == 105
    with pytest.raises(EventLogWorkspaceError, match='revision'):
        fn.funnel(log, ['A', 'B'], **args, evidence_after=first['next_after'])
    with pytest.raises(EventLogWorkspaceError, match='changed'):
        fn.funnel(log, ['A', 'B'], **args, within_hours=2, evidence_after=first['next_after'], evidence_revision=first['revision'])
    workspace.append_log(log, BytesIO(b'case,activity,time\nnew,A,2024-01-02T00:00:00Z\nnew,B,2024-01-02T01:00:00Z\n'),
                         source_format='csv', mapping=MAPPING)
    with pytest.raises(EventLogWorkspaceError, match='changed'):
        fn.funnel(log, ['A', 'B'], **args, evidence_after=first['next_after'], evidence_revision=first['revision'])
