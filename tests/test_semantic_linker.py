"""Optional semantic linker: minimized input, strict validation, never auto-merged."""
from __future__ import annotations

import json

import httpx
import pytest

from evals.reconstruction import owg_native
from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.prepare import split_cases
from evals.reconstruction.scoring import aggregate
from server import semantic_linker as linker

A, B = ("owg:r:" + c * 32 for c in "ab")


def _event(n, title, *, ref=None, kind="account", app="Google Chrome"):
    metadata = {"target": {"label": title.split(" · ")[-1]}, "page": {"pathname": "/SECRET/PATH"}}
    if ref:
        metadata["resource_reference"] = {"resource_ref": ref, "provider": "salesforce", "resource_kind": kind,
                                          "host": "acme.salesforce.com", "resolver_locator": "SECRET-LOCATOR"}
    return {"event_id": f"e{n:02d}", "observed_at": f"2026-10-20T10:{n:02d}:00+00:00", "app": app,
            "event_type": "browser_click", "source": "browser_extension", "session_id": "s1",
            "window_title": title, "target_label": title.split(" · ")[-1], "metadata": metadata}


EVENTS = [_event(1, "Gmail · Customer email"), _event(2, "Salesforce · Open account", ref=A),
          _event(3, "Jira · Fix login bug"), _event(4, "Salesforce · Save account", ref=A),
          _event(5, "Sheets · Price list")]


def test_items_are_minimized_and_carry_no_identifiers():
    items, groups = linker.build_items(EVENTS)
    text = json.dumps(items)
    assert "SECRET" not in text and "owg:r:" not in text and "e0" not in text
    assert any(item["labels"] == ["Open account", "Save account"] or "Open account" in item["labels"] for item in items)
    assert sum(len(g) for g in groups) == len(EVENTS)


def test_suggestions_are_validated_and_marked_for_review():
    items, groups = linker.build_items(EVENTS)
    account = next(i for i, g in enumerate(groups, 1) if "e02" in g)
    email = next(i for i, g in enumerate(groups, 1) if "e01" in g)
    seen = {}

    def model(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps({"groups": [[email, account]]})
    result = linker.suggest(EVENTS, model)
    assert "untrusted data" in seen["system"]
    assert result["merged_into_canonical_evidence"] is False
    [suggestion] = result["suggestions"]
    assert suggestion["association_status"] == "semantic_suggestion" and suggestion["requires_review"] is True
    assert set(suggestion["event_ids"]) == {"e01", "e02", "e04"}


@pytest.mark.parametrize("content", ["not json", '{"groups": [[1, 99]]}', '{"groups": [[1, 2], [2, 3]]}',
                                     '{"groups": [["1", 2]]}', '{"groups": "all"}', '[[1, 2]]'])
def test_malformed_model_output_is_refused(content):
    with pytest.raises(linker.SemanticLinkerError):
        linker.suggest(EVENTS, lambda system, user: content)


def test_a_suggestion_joining_two_records_of_one_kind_is_vetoed():
    events = [_event(1, "Salesforce · Account", ref=A), _event(2, "Salesforce · Account", ref=A),
              _event(3, "Salesforce · Other account", ref=B), _event(4, "Salesforce · Other account", ref=B)]
    items, _ = linker.build_items(events)
    result = linker.suggest(events, lambda s, u: json.dumps({"groups": [list(range(1, len(items) + 1))]}))
    assert result["suggestions"] == [] and result["vetoed"] == 2  # two unassigned conflicting items


def test_configuration_requires_acknowledgement_and_safe_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(linker, "DATA_DIR", tmp_path)
    assert linker.settings()["enabled"] is False
    good = {"acknowledge": linker.ACK_VERSION, "base_url": "https://api.example.com/v1", "model": "m-1",
            "api_key_env": "OWG_MODEL_KEY"}
    with pytest.raises(linker.SemanticLinkerError):
        linker.configure({**good, "acknowledge": None})
    for bad in ({"base_url": "http://example.com/v1"}, {"model": "bad model"}, {"api_key_env": "sk-live-123"}):
        with pytest.raises(linker.SemanticLinkerError):
            linker.configure({**good, **bad})
    assert linker.configure({**good, "base_url": "http://127.0.0.1:11434/v1"})["enabled"] is True
    assert linker.configure({"enabled": False})["enabled"] is False
    linker.configure(good)
    with pytest.raises(linker.SemanticLinkerError, match="not set"):
        linker.configured_completion()


def test_http_completion_sends_bearer_and_parses_choices():
    calls = []

    def handler(request):
        calls.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"groups": []}'}}]})
    complete = linker.http_completion("https://api.example.com/v1", "m-1", "KEY", transport=httpx.MockTransport(handler))
    assert complete("sys", "user") == '{"groups": []}'
    url, auth, body = calls[0]
    assert url == "https://api.example.com/v1/chat/completions" and auth == "Bearer KEY" and body["model"] == "m-1"
    failing = linker.http_completion("https://api.example.com/v1", "m-1", None,
                                     transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(linker.SemanticLinkerError, match="401"):
        failing("s", "u")


def test_eval_grouping_reaches_ceiling_with_perfect_decisions_and_replays_from_cache(tmp_path):
    cases = split_cases(load_cases(), "development")
    predictions = []
    for case in cases:
        truth = {e: w["workflow_id"] for w in case["ground_truth"]["workflows"] for e in w["event_ids"]}
        _, groups = linker.build_items(owg_native._visible_linked_source_events(case))

        def oracle(system, user, groups=groups, truth=truth):
            by = {}
            for number, ids in enumerate(groups, 1):
                labels = [truth[e] for e in ids if e in truth]
                if labels:
                    by.setdefault(max(set(labels), key=labels.count), []).append(number)
            return json.dumps({"groups": [v for v in by.values() if len(v) > 1]})
        predictions.append(owg_native.predict_semantic_items(case, oracle))
    gate = aggregate(cases, predictions)["identifiable_membership_family_macro"]
    assert gate["workflow_assignment_f1"] >= 0.95 and gate["cross_workflow_contamination"] == 0.0

    cache = tmp_path / "cache.json"
    live = owg_native.cached_completion(lambda s, u: '{"groups": []}', cache)
    assert live("s", "u") == '{"groups": []}'
    replay = owg_native.cached_completion(None, cache)
    assert replay("s", "u") == '{"groups": []}'
    with pytest.raises(KeyError):
        replay("s", "different")


def test_model_failure_leaves_owg_grouping_unchanged():
    case = split_cases(load_cases(), "development")[0]
    broken = owg_native.predict_semantic_items(case, lambda s, u: "garbage")
    topic = owg_native.predict_label_topic_items(case)
    assert sorted(map(sorted, (w["event_ids"] for w in broken["workflows"]))) == \
        sorted(map(sorted, (w["event_ids"] for w in topic["workflows"])))
