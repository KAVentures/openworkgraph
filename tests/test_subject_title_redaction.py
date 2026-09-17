from __future__ import annotations

import importlib
import json


def _presentation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)
    from server.first_name_policy import install as install_first_name
    from server.mail_row_policy import install as install_mail_row
    from server.typed_privacy_policy import install as install_typed_privacy
    install_first_name(presentation)
    install_mail_row(presentation)
    install_typed_privacy(presentation)
    monkeypatch.setattr(
        presentation,
        "_owner_identity",
        lambda: ({"koyar afrasyab": "OWNER", "koyar": "OWNER"}, set(), set()),
    )
    return presentation


def test_gmail_sender_is_masked_but_unknown_subject_name_is_not_guessed(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Select Anna Svensson, Contract renewal with Erik Nilsson",
        "event_id": "evt-1",
        "duration_seconds": 0.0,
    }
    before = json.loads(json.dumps(raw))
    safe = presentation.redact_for_display(raw)

    assert raw == before
    # The structured Select/sender slot remains strong enough evidence.
    assert "Anna Svensson" not in safe["label"]
    # v0.40 deliberately stops guessing that every title-case subject phrase is
    # a person. Erik is masked once learned from stronger identity evidence.
    assert "Erik Nilsson" in safe["label"]
    assert "Contract renewal with" in safe["label"]
    assert safe["event_id"] == "evt-1"
    assert safe["duration_seconds"] == 0.0


def test_owner_inside_email_subject_becomes_owner(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Select Anna Svensson, Re: Contract renewal with Koyar Afrasyab",
    })

    assert "Anna Svensson" not in safe["label"]
    assert "Koyar Afrasyab" not in safe["label"]
    assert "OWNER" in safe["label"]
    assert "Re: Contract renewal with" in safe["label"]


def test_workflow_title_words_are_not_destroyed(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Select Anna Svensson, Quarterly Pricing Review",
    })

    assert "Anna Svensson" not in safe["label"]
    assert "Quarterly Pricing Review" in safe["label"]


def test_unknown_document_and_meeting_title_entities_are_not_guessed(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)

    doc = presentation.redact_for_display({
        "surface": "Google Docs",
        "hostname": "docs.google.com",
        "resource_title": "Anna Svensson contract renewal",
    })
    meeting = presentation.redact_for_display({
        "surface": "Microsoft Teams",
        "label": "Meeting with Erik Nilsson about Pricing Review",
    })

    assert doc["resource_title"] == "Anna Svensson contract renewal"
    assert meeting["label"] == "Meeting with Erik Nilsson about Pricing Review"


def test_known_identity_is_redacted_in_later_title_without_app_allowlist(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)

    safe = presentation.redact_for_display({
        "identity_evidence": "Erik Nilsson <erik.nilsson@example.com>",
        "surface": "Unknown Internal System",
        "resource_title": "Notes for Erik Nilsson",
    })

    assert "Erik Nilsson" not in safe["identity_evidence"]
    assert "Erik Nilsson" not in safe["resource_title"]
    assert "Notes for PERSON_" in safe["resource_title"]


def test_generic_unscoped_free_text_is_not_aggressively_name_redacted(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {"note": "Erik Nilsson discussed Project Phoenix"}
    safe = presentation.redact_for_display(raw)

    assert safe == raw
