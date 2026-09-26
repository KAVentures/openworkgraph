from __future__ import annotations

import json


def test_public_readable_runs_omit_legacy_structural_tokens(monkeypatch):
    from server import procedural_feedback as feedback

    monkeypatch.setattr(
        feedback,
        "_human_runs",
        lambda _raw: [
            {
                "execution_id": "execution:one",
                "family_key": "human:email.compose_send",
                "family_basis": "canonical_task_family",
                "started_at": "2026-09-26T10:00:00Z",
                "ended_at": "2026-09-26T10:11:00Z",
                "duration_seconds": 660.0,
                "outcome_status": "observed_completion",
                "outcome_basis": "strong_completion_anchor",
                "positive_example": True,
                "structural_steps": [
                    "surface:gmail",
                    "action:click",
                    "surface:33a7935db79d",
                    "action:click",
                ],
                "semantic_steps": [
                    "Gmail · Open email",
                    "Salesforce · Open account",
                    "Google Sheets · Update status",
                    "Gmail · Send",
                ],
                "evidence_refs": [],
                "derived": True,
                "authoritative": False,
                "needs_review": True,
            }
        ],
    )

    result = feedback.readable_feedback(
        [], family_key="human:email.compose_send", min_support=2
    )
    encoded = json.dumps(result, ensure_ascii=False)
    assert result["runs"][0]["semantic_steps"][1] == "Salesforce · Open account"
    assert "structural_steps" not in result["runs"][0]
    assert "surface:33a7935db79d" not in encoded
    assert "surface:gmail" not in encoded
