from __future__ import annotations

import json

import httpx
import pytest

from evals.reconstruction.fixtures import generate_cases
from evals.reconstruction.run_external import _extract_json, _request, _validate_prediction


def _prediction(case: dict) -> dict:
    truth = case["ground_truth"]
    return {
        "case_id": case["case_id"],
        "workflows": [
            {
                "prediction_id": f"workflow_{idx}",
                "event_ids": list(row["event_ids"]),
                "ordered_event_ids": list(row["event_ids"]),
                "automation_relevant_event_ids": list(row["automation_relevant_event_ids"]),
                "ui_mechanic_event_ids": list(row["ui_mechanic_event_ids"]),
            }
            for idx, row in enumerate(truth["workflows"], start=1)
        ],
        "unassigned_event_ids": list(truth["noise_event_ids"]),
        "insufficient_evidence": bool(truth["requires_uncertainty"]),
    }


def test_extract_json_accepts_plain_and_fenced_objects():
    assert _extract_json('{"workflows":[]}')["workflows"] == []
    assert _extract_json('```json\n{"workflows":[]}\n```')["workflows"] == []


def test_validate_prediction_rejects_unknown_or_duplicate_event_ids():
    case = generate_cases()[0]
    ids = {e["event_id"] for e in case["presented_evidence"]}
    prediction = _prediction(case)
    prediction["workflows"][0]["event_ids"].append("not-real")
    with pytest.raises(ValueError, match="unknown event ids"):
        _validate_prediction(prediction, case["case_id"], ids)

    prediction = _prediction(case)
    duplicate = prediction["workflows"][0]["event_ids"][0]
    prediction["workflows"][1]["event_ids"].append(duplicate)
    with pytest.raises(ValueError, match="multiple workflows"):
        _validate_prediction(prediction, case["case_id"], ids)


def test_request_sends_only_blind_case_payload_and_parses_prediction():
    case = generate_cases()[0]
    expected = _prediction(case)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(200, json={
            "id": "resp-test",
            "model": "test-model",
            "choices": [{"message": {"content": json.dumps(expected)}}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        prediction, meta = _request(
            client,
            base_url="https://example.test/v1",
            api_key="secret-test-value",
            model="test-model",
            case=case,
            temperature=None,
            timeout=5,
        )

    assert prediction["case_id"] == case["case_id"]
    assert meta["response_id"] == "resp-test"
    assert captured["auth"] == "Bearer secret-test-value"
    assert "temperature" not in captured["body"]
    user = json.loads(captured["body"]["messages"][1]["content"])
    assert set(user) == {"case_id", "presented_evidence"}
    assert "ground_truth" not in captured["body"]["messages"][1]["content"]
    assert "source_events" not in captured["body"]["messages"][1]["content"]
