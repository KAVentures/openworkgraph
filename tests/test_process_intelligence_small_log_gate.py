"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agents_get_no_aggregates_from_a_log_with_fewer_than_five_cases():
    rows = ["case,activity,time,who"] + [f"c{i},A,2024-03-0{1 + i}T08:00:00Z,x" for i in range(3)]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="tiny", source_format="csv", mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    with pytest.raises(sharing.NotAllowed):
        sharing.overview(log_id)
    sharing.set_share(log_id, "cases")
    assert sharing.overview(log_id)["cases"] == 3


def test_aggregate_answers_about_fewer_than_five_cases_are_refused():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    with pytest.raises(sharing.NotAllowed):
        sharing.check_answer_size(log_id, {"cases": 3})
    sharing.check_answer_size(log_id, {"cases": 12})
    sharing.check_answer_size(log_id, {"cases": [{"case_id": "c0"}]})
    sharing.set_share(log_id, "cases")
    sharing.check_answer_size(log_id, {"cases": 3})


def test_shared_log_listing_flags_case_notions_too_small_for_aggregates():
    big = _log("big")
    rows = ["case,activity,time,who"] + [f"c{i},A,2024-03-0{1 + i}T08:00:00Z,x" for i in range(3)]
    tiny = workspace.import_log(BytesIO("\n".join(rows).encode()), name="tiny", source_format="csv", mapping=MAPPING)["log_id"]
    sharing.set_share(big, "aggregates")
    sharing.set_share(tiny, "aggregates")
    listed = {entry["log_id"]: entry for entry in sharing.shared_logs()["logs"]}
    assert listed[big]["case_notions_too_small_for_aggregates"] == []
    assert listed[tiny]["case_notions_too_small_for_aggregates"] == listed[tiny]["case_notions"] != []
    sharing.set_share(tiny, "cases")
    assert {e["log_id"]: e for e in sharing.shared_logs()["logs"]}[tiny]["case_notions_too_small_for_aggregates"] == []
