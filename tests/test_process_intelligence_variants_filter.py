"""Agents can list the variants of one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_variants_of_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    group = sharing.variants(log_id, case_filter={"resource": "bob"})
    assert group["cases"] == 6 and sum(v["cases"] for v in group["variants"]) <= 6
    assert group["coverage_targets"][0]["variants_needed"] >= 1
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.variants(log_id, case_filter={"case_ids": ["c0"]})
