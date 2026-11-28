"""Agents can discover and check the model of one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_model_tools_within_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    bob = {"resource": "bob"}
    assert sharing.footprints(log_id, case_filter=bob)["cases"] == 6
    assert sharing.model_quality(log_id, case_filter=bob)["cases"] == 6
    assert sharing.model_deviations(log_id, case_filter=bob)["cases"] == 6
    for call in (sharing.footprints, sharing.model_quality, sharing.model_deviations):
        with pytest.raises(workspace.EventLogWorkspaceError):
            call(log_id, case_filter={"case_ids": ["c0"]})
