"""Seeded, synthetic privacy/adversarial UI-copy regression corpus.

The cases resemble Swedish/English Gmail, Slack, and calendar snippets but
contain no captured real user data. Preserve utility as well as name recall.
"""
import json
import random
from pathlib import Path

import pytest

from gateway.plugin_redaction import PublicRedactor, project

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "hosted_redaction_adversarial.json")
    .read_text(encoding="utf-8")
)


def _redactor():
    return PublicRedactor(secret="synthetic-fixture-secret", principal="synthetic-actor")


@pytest.mark.parametrize("case", CASES["sensitive"], ids=lambda row: row["id"])
def test_hosted_redaction_sensitive_spans_in_all_text_modes(case):
    raw = case["text"]
    for method in ("text", "target_label"):
        projected = getattr(_redactor(), method)(raw)
        assert case["span"].casefold() not in projected.casefold(), (
            case["id"], method, projected
        )
        assert ("PERSON_" if case["kind"] == "person" else "ORG_") in projected, (
            case["id"], method, projected
        )
        for fragment in case["retain"]:
            assert fragment in projected, (case["id"], method, fragment, projected)


@pytest.mark.parametrize("label", CASES["preserve"])
def test_hosted_redaction_keeps_non_private_interface_copy(label):
    redactor = _redactor()
    assert redactor.target_label(label) == label
    assert redactor.text(label) == label


def test_seeded_inbox_calendar_slack_wrappers_keep_names_redacted():
    """Fuzz 120 deterministic, realistic UI wrappers, not random untrusted PII."""
    rng = random.Random(20261013)
    prefixes = ("Gmail · ", "Calendar · ", "Slack · ", "Re: ", "Inbox: ", "")
    for index in range(120):
        case = rng.choice(CASES["sensitive"])
        line = rng.choice(prefixes) + case["text"]
        redacted = _redactor().target_label(line)
        assert case["span"].casefold() not in redacted.casefold(), (
            index, case["id"], redacted
        )
        assert ("PERSON_" if case["kind"] == "person" else "ORG_") in redacted


def test_structural_event_projection_redacts_names_in_nested_fields():
    """Event, target metadata and readable workflow step paths all agree."""
    for case in CASES["sensitive"]:
        row = {
            "event_id": f"synthetic-{case['id']}",
            "observed_at": "2026-10-13T08:00:00Z",
            "event_type": "browser_click", "app": "Chrome",
            "window_title": case["text"], "target_label": case["text"],
            "metadata": {"target": {"role": "button", "label": case["text"]}},
        }
        for detail in ("compact", "rich"):
            redacted = project(
                {"rows": [row], "candidate_clusters": [
                    {"readable_steps": [case["text"]]}]},
                detail=detail, secret="synthetic-fixture-secret",
                principal="synthetic-actor",
            )
            assert case["span"].casefold() not in str(redacted).casefold(), (
                case["id"], detail, redacted
            )
            assert redacted["rows"][0]["event_id"] == row["event_id"]
