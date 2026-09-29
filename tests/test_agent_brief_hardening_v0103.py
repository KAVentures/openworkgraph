from __future__ import annotations


def test_unknown_test_result_does_not_invent_zero_counts(monkeypatch):
    from server import agent_brief

    monkeypatch.setattr(agent_brief, "_runs", lambda framework, now: [{
        "started_at": "2026-09-28T10:00:00+00:00",
        "work_summary": {"tests": {"runs": 1, "ended": "unknown"}},
    }])
    text = agent_brief.build_brief("claude-code")["text"]
    assert "Most recent test result: unknown; no recognized test summary was observed." in text
    assert "0 passed, 0 failed" not in text
