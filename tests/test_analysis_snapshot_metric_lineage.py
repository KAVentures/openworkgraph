"""R9 regression: aggregate snapshots pin bounded metric definitions without exposing them."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.analysis_snapshot import Snapshot
from server.event_log_query import _metric_lineage


def _snapshot(**extra):
    return dict(
        generated_at=datetime.now(timezone.utc), level="cases",
        columns=[{"heading": "Measure 1", "kind": "number", "aggregation": "cases"}],
        rows=[[2]], matching_cases=2, matching_rows=2, rows_truncated=False,
        source_content_sha256="a" * 64, analysis_spec_sha256="b" * 64,
        **extra,
    )


def test_metric_definition_digest_stable_and_embedded_in_snapshot():
    definition = {"kind": "activity_count", "name": "touches", "activity": "Review"}
    digest = _metric_lineage(definition)["definition_sha256"]
    assert digest == _metric_lineage(dict(reversed(list(definition.items()))))["definition_sha256"]
    snapshot = Snapshot.model_validate(_snapshot(metric_definition_sha256=[digest]))
    exported = snapshot.model_dump(mode="json")
    assert exported["metric_definition_sha256"] == [digest]
    assert "Review" not in str(exported)
    assert _metric_lineage({**definition, "activity": "Approve"})["definition_sha256"] != digest


@pytest.mark.parametrize("digests", [["x" * 64], ["a" * 64] * 5, [None]])
def test_metric_lineage_rejects_invalid_or_unbounded_digests(digests):
    with pytest.raises(ValidationError):
        Snapshot.model_validate(_snapshot(metric_definition_sha256=digests))


def test_legacy_snapshots_default_to_no_metric_lineage():
    assert Snapshot.model_validate(_snapshot()).metric_definition_sha256 == []
