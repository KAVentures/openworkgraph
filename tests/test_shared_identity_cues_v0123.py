"""Shared Swedish explicit identity cues must work before local storage."""
from __future__ import annotations

import re
from gateway.plugin_redaction import PublicRedactor
from server.contextual_redaction import Detector
from shared.core.browser_title_privacy import protect_text
from shared.core.identity_cues import redact_explicit_person_cues


def test_local_and_hosted_identify_explicit_names_with_distinct_scoped_tokens():
    local = Detector(token=lambda kind, value: f"{kind}_MASKED")
    hosted = PublicRedactor(secret="local-test-scoped-secret-here", principal="tester")
    for value in (
        "Mitt namn är Michaela - Gmail",
        "Hälsningar, Michaela",
        "/ Michaela",
        "Välkommen Michaela, din faktura är klar",
        "My name is Michaela",
    ):
        local_output = local.redact(value)
        hosted_output = hosted.text(value)
        assert "Michaela" not in local_output, (value, local_output)
        assert "Michaela" not in hosted_output, (value, hosted_output)
        assert "PERSON_" in local_output, (value, local_output)
        assert "PERSON_" in hosted_output, (value, hosted_output)


def test_local_storage_title_protection_also_removes_unlisted_first_name():
    for value in ("Mitt namn är Michaela", "Hälsningar, Michaela", "/ Michaela"):
        safe = protect_text(value)
        assert "Michaela" not in safe
        assert re.search(r"PERSON_[0-9A-F]{6}", safe), safe
        assert protect_text(safe) == safe


def test_existing_workflow_labels_and_generic_greetings_unchanged():
    local = Detector(token=lambda kind, value: f"{kind}_MASKED")
    ordinary = [
        "PR #197 · KAVentures/openworkgraph - GitHub",
        "Invoice 4471 - Fortnox",
        "Hej alla",
        "Välkommen tillbaka",
        "Medlink Nordic AB - Customer contract",
        "Microsoft Teams - Q3 budget",
    ]
    for value in ordinary:
        assert local.redact(value) == value, (value, local.redact(value))


def test_shared_cues_do_not_double_redact_pseudonyms():
    input_text = "Mitt namn är PERSON_AB12CD och mitt projekt är PR #197"
    result = redact_explicit_person_cues(
        input_text,
        person_token=lambda _name: "PERSON_OTHER",
    )
    assert result == input_text


def test_known_non_person_words_are_not_names_when_blocked():
    result = redact_explicit_person_cues(
        "Välkommen Tillbaka! Hej alla",
        person_token=lambda name: f"PERSON_{name}",
        blocked_names={"tillbaka", "alla"},
    )
    assert result == "Välkommen Tillbaka! Hej alla"
