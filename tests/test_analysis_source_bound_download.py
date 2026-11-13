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
