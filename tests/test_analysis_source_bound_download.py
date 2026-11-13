"""Source-bound analysis export refuses changed lineage before releasing rows."""
import pytest

from server import analysis_snapshot_export as exporter
from server.event_log_workspace import EventLogWorkspaceError

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def test_source_bound_export_requires_exact_reviewed_lineage(monkeypatch):
    current = {
        "source_content_sha256": HASH_A,
        "analysis_spec_sha256": HASH_B,
        "metric_definition_sha256": [HASH_C],
        "rows": [[12]],
    }
    monkeypatch.setattr(exporter, "export_snapshot", lambda log_id, spec: dict(current))
    request = {
        "spec": {"level": "case"},
        "source_content_sha256": HASH_A,
        "analysis_spec_sha256": HASH_B,
        "metric_definition_sha256": [HASH_C],
    }
    assert exporter.export_if_source_current("log-1", request)["rows"] == [[12]]
    for field, changed in [
        ("source_content_sha256", HASH_B),
        ("analysis_spec_sha256", HASH_A),
        ("metric_definition_sha256", [HASH_B]),
    ]:
        with pytest.raises(EventLogWorkspaceError, match="changed"):
            exporter.export_if_source_current("log-1", {**request, field: changed})


@pytest.mark.parametrize("change", [
    {"source_content_sha256": "not-a-digest"},
    {"metric_definition_sha256": [1]},
    {"metric_definition_sha256": [HASH_C] * 33},
    {"extra": "unreviewed"},
])
def test_source_bound_export_rejects_malformed_lineage_before_query(monkeypatch, change):
    monkeypatch.setattr(exporter, "export_snapshot", lambda *_: pytest.fail("unexpected query"))
    request = {
        "spec": {},
        "source_content_sha256": HASH_A,
        "analysis_spec_sha256": HASH_B,
        "metric_definition_sha256": [HASH_C],
    }
    with pytest.raises(EventLogWorkspaceError):
        exporter.export_if_source_current("log-1", {**request, **change})


def test_authenticated_download_http_revalidates_source_and_denies_bearer(tmp_path):
    """Exercise the real local HTTP route, including authentication and source drift."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    dashboard = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session()}
    preview_headers = {**dashboard, 'X-OWG-Analysis-Sharing-Preview': 'true'}
    bearer = {'Authorization': 'Bearer ' + local_auth.ensure_api_token(),
              'X-OWG-Analysis-Sharing-Preview': 'true'}
    mapping = '?format=csv&name=private&case_column=case&activity_column=activity&timestamp_column=time'
    imported = client.post('/v1/event-logs/import' + mapping,
        content=b'case,activity,time\nprivate-case,A,2024-01-01T00:00:00Z\n',
        headers={**dashboard, 'X-OWG-Local-Storage-Confirmed': 'true'})
    assert imported.status_code == 200, imported.text
    log_id = imported.json()['log_id']
    base = '/v1/event-logs/' + log_id
    spec = {'level': 'cases', 'dimensions': ['last_activity'],
            'measures': [{'fn': 'count'}]}
    preview = client.post(base + '/analysis-sharing-preview', json=spec, headers=preview_headers)
    assert preview.status_code == 200, preview.text
    snapshot = preview.json()
    request = {k: snapshot[k] for k in
               ('source_content_sha256', 'analysis_spec_sha256', 'metric_definition_sha256')}
    request['spec'] = spec
    endpoint = base + '/analysis-sharing-download'
    assert client.post(endpoint, json=request).status_code == 403
    assert client.post(endpoint, json=request, headers=dashboard).status_code == 403
    assert client.post(endpoint, json=request, headers=bearer).status_code == 403
    ok = client.post(endpoint, json=request, headers=preview_headers)
    assert ok.status_code == 200, ok.text
    assert ok.headers['cache-control'] == 'no-store'
    assert ok.json()['rows'] == snapshot['rows']
    assert 'private-case' not in ok.text
    malformed = client.post(endpoint, json={**request, 'unexpected': True}, headers=preview_headers)
    assert malformed.status_code in (409, 422), malformed.text
    append = client.post(base + '/append?format=csv&case_column=case&activity_column=activity&timestamp_column=time',
        content=b'case,activity,time\nnew-case,B,2024-01-02T00:00:00Z\n',
        headers={**dashboard, 'X-OWG-Local-Storage-Confirmed': 'true'})
    assert append.status_code == 200, append.text
    stale = client.post(endpoint, json=request, headers=preview_headers)
    assert stale.status_code == 409, stale.text
    assert 'rows' not in stale.text and 'private-case' not in stale.text
"""
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth'),
           'WORKFLOW_OBSERVER_CONFIG': str(tmp_path / 'config.json')}
    run = subprocess.run([sys.executable, '-c', script],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
