"""A noise level whose discovered draft is too large is reported and skipped, not fatal."""
from __future__ import annotations

import pytest

from server import event_log_model_quality as mq
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _dirs, _import  # noqa: F401  (fixture reuse)


def test_oversized_levels_are_skipped_and_reported(monkeypatch):
    log = _import([["Register", "Check", "Pay", "Close"]] * 20)
    real = pnml.discovered_pnml

    def fake(log_id, case_type, *, noise, **kwargs):
        if noise == 0:
            raise EventLogWorkspaceError("The discovered draft needs 59 nodes")
        return real(log_id, case_type, noise=noise, **kwargs)
    monkeypatch.setattr(pnml, "discovered_pnml", fake)
    result = mq.model_quality(log, levels="0,0.2")
    assert [level["noise"] for level in result["levels"]] == [0.2]
    assert result["levels_skipped"] == [{"noise": 0.0, "reason": "The discovered draft needs 59 nodes"}]
    assert result["best_f_score_noise"] == 0.2
    monkeypatch.setattr(pnml, "discovered_pnml", lambda *a, **k: (_ for _ in ()).throw(EventLogWorkspaceError("too big")))
    with pytest.raises(EventLogWorkspaceError, match="too big"):
        mq.model_quality(log, levels="0,0.2")
