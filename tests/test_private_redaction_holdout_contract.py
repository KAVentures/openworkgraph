"""Validate holdout protocol without bundling any real holdout into GitHub."""
import json

import pytest

from evals.private_redaction_holdout import score


def test_heldout_score_uses_only_aggregate_metrics_and_precommitted_gates(tmp_path):
    # Tiny public contract smoke test; not the real hidden evaluation.
    rows = [
        {"text": "Katarina kommenterade avtalet",
         "sensitive_spans": [{"value": "Katarina", "kind": "PERSON"}],
         "must_preserve": ["kommenterade"]},
        {"text": "Jobbar på Capio idag",
         "sensitive_spans": [{"value": "Capio", "kind": "ORG"}],
         "must_preserve": ["Jobbar på "]},
        {"text": "Slack · @lisa.k reagerade",
         "sensitive_spans": [{"value": "@lisa.k", "kind": "HANDLE"}],
         "must_preserve": ["Slack · "]},
        {"text": "Patient: Karl Gustafsson, 54 år",
         "category": "clinical",
         "sensitive_spans": [{"value": "Karl Gustafsson", "kind": "CLINICAL"}]},
        {"text": "Hämta igen", "sensitive_spans": []},
    ]
    path = tmp_path / "hidden-owned-by-reviewer.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    metrics = score(path, secret="synthetic-long-device-secret", enforce_minimum=False)
    assert metrics["scores"]["PERSON"] == 1
    assert metrics["scores"]["ORG"] == 1
    assert metrics["scores"]["HANDLE"] == 1
    assert metrics["scores"]["CLINICAL"] == 1
    assert metrics["scores"]["UI"] == 1
    assert metrics["minimum_sample_gate"] is False
    assert metrics["thresholds"]["PERSON"] == 0.95
    assert metrics["thresholds"]["UI"] == 0.95
    assert metrics["thresholds"]["CLINICAL"] == 1.0
    result = json.dumps(metrics)
    assert not any(secret in result for secret in (
        "Katarina", "Capio", "@lisa.k", "Karl Gustafsson",
    ))


def test_holdout_cannot_live_in_checkout():
    from pathlib import Path
    forbidden = Path(__file__).resolve()
    with pytest.raises(ValueError, match="outside the source checkout"):
        score(forbidden, secret="synthetic-long-device-secret")
