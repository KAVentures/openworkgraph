"""Opt-in label-topic linking (roadmap #326 A1)."""
from __future__ import annotations

import json

from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.owg_native import score_label_topic_items, score_linked_items
from evals.reconstruction.prepare import split_cases
from server import workflow_evidence
from server.label_topic_links import label_topic_members, label_topic_work_items, topic_words

A, B = ("owg:r:" + c * 32 for c in "ab")


def _event(n, title, *, ref=None, app="Google Chrome", session="session:one", kind="opportunity"):
    metadata = {"target": {"label": title.split(" · ")[-1]}}
    if ref:
        metadata["resource_reference"] = {"resource_ref": ref, "provider": "salesforce",
                                          "resource_kind": kind, "host": "acme.salesforce.com"}
    return {"event_id": f"e{n:02d}", "observed_at": f"2026-10-20T10:{n:02d}:00+00:00", "app": app,
            "event_type": "browser_click", "source": "browser_extension", "session_id": session,
            "window_title": title, "target_label": title.split(" · ")[-1], "metadata": metadata}


def _groups(events, **kwargs):
    return {tuple(ids) for ids in label_topic_members(events, **kwargs)["members"] if len(ids) >= 2}


def test_interleaved_topics_become_separate_items():
    events = [
        _event(1, "Gmail · Renewal request"), _event(2, "Slack · Escalation request", app="Slack"),
        _event(3, "Salesforce · Renewal account"), _event(4, "Jira · Escalation issue"),
        _event(5, "Google Sheets · Renewal pricing"), _event(6, "Jira · Resolve escalation"),
        _event(7, "Gmail · Renewal reply"),
    ]
    assert _groups(events) == {("e01", "e03", "e05", "e07"), ("e02", "e04", "e06")}


def test_generic_ui_words_and_surface_names_link_nothing():
    assert topic_words(_event(1, "Gmail · Open request")) == set()
    events = [_event(1, "Gmail · Open message"), _event(2, "Gmail · Send reply"),
              _event(3, "Jira · Open issue"), _event(4, "Jira · Save")]
    assert _groups(events) == set()


def test_shared_topic_never_merges_two_records_of_one_kind():
    events = [_event(1, "Salesforce · Edit opportunity", ref=A), _event(2, "Salesforce · Edit opportunity", ref=B),
              _event(3, "Salesforce · Save opportunity", ref=A), _event(4, "Salesforce · Save opportunity", ref=B),
              _event(5, "Notes · Quarterly", app="Notes"), _event(6, "Notes · Quarterly", app="Notes")]
    result = label_topic_members(events, max_share=1.0)
    assert {tuple(ids) for ids in result["members"] if len(ids) >= 2} == {
        ("e01", "e03"), ("e02", "e04"), ("e05", "e06")}
    assert result["record_conflict_vetoes"] >= 1


def test_topics_never_cross_capture_scope():
    events = [_event(1, "Gmail · Renewal"), _event(2, "Gmail · Renewal", session="session:two")]
    assert _groups(events) == set()


def test_word_seen_in_most_observations_is_not_distinctive():
    events = [_event(n, "Portal · Acme step") for n in range(1, 6)]
    assert _groups(events, max_share=0.6) == set()


def test_projection_never_returns_topic_words():
    events = [_event(1, "Gmail · Renewal confidential"), _event(2, "Sheets · Renewal confidential")]
    result = label_topic_work_items(events)
    assert result["items"][0]["event_ids"] == ["e01", "e02"]
    assert result["link_contract"]["topic_words_returned"] is False
    text = json.dumps(result).lower()
    assert "renewal" not in text and "confidential" not in text


def test_discovery_includes_topic_items_only_when_opted_in(monkeypatch):
    events = [_event(1, "Gmail · Renewal"), _event(2, "Sheets · Renewal")]
    monkeypatch.setattr(workflow_evidence, "_load", lambda **_: events)
    default = workflow_evidence.list_workflow_evidence_candidates()
    assert "label_topic_work_items" not in default
    opted_in = workflow_evidence.list_workflow_evidence_candidates(label_topics=True)
    assert opted_in["label_topic_work_items"]["total_items"] == 1


def _gate(report):
    gate = report["identifiable_membership_family_macro"]
    return gate["workflow_assignment_f1"], gate["cross_workflow_contamination"]


def test_label_topics_improve_assignment_without_contamination():
    # Regression floors (identifiable-membership family macro). Holdout was
    # scored once after development tuning; see evals/reconstruction/README.md.
    for split, floor in (("development", 0.88), ("holdout", 0.70), ("all", 0.80)):
        cases = split_cases(load_cases(), split)
        topic_f1, topic_contamination = _gate(score_label_topic_items(cases))
        base_f1, _ = _gate(score_linked_items(cases))
        assert topic_f1 >= floor and topic_f1 > base_f1
        assert topic_contamination == 0.0
