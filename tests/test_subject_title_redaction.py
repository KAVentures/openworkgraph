from __future__ import annotations

import importlib
import json


def _presentation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)
    # Make owner behavior deterministic for these tests rather than depending on
    # the CI runner's OS account name.
    monkeypatch.setattr(
        presentation,
        "_owner_identity",
        lambda: ({"koyar afrasyab": "OWNER", "koyar": "OWNER"}, set(), set()),
    )
    return presentation


def test_gmail_observed_action_redacts_names_inside_subject(monkeypatch, tmp_path):
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
    assert "Anna Svensson" not in safe["label"]
    assert "Erik Nilsson" not in safe["label"]
    assert "Contract renewal with" in safe["label"]
    assert safe["label"].count("PERSON_") == 2
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


def test_document_and_meeting_titles_mask_embedded_people(monkeypatch, tmp_path):
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

    assert "Anna Svensson" not in doc["resource_title"]
    assert "contract renewal" in doc["resource_title"]
    assert "Erik Nilsson" not in meeting["label"]
    assert "Meeting with" in meeting["label"]
    assert "Pricing Review" in meeting["label"]


def test_generic_unscoped_free_text_is_not_aggressively_name_redacted(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {"note": "Erik Nilsson discussed Project Phoenix"}
    safe = presentation.redact_for_display(raw)

    # The new heuristic is intentionally contextual. Arbitrary prose remains
    # conservative unless the person was learned through structured evidence.
    assert safe == raw
