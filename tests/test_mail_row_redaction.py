from __future__ import annotations

import importlib
import json


def _presentation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)
    monkeypatch.setattr(
        presentation,
        "_owner_identity",
        lambda: ({"koyar afrasyab": "OWNER", "koyar": "OWNER"}, set(), set()),
    )
    return presentation


def test_swedish_gmail_row_redacts_bare_first_name_sender(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: oläst, Julia, RESCUE Sverige",
    }
    before = json.loads(json.dumps(raw))
    safe = presentation.redact_for_display(raw)

    assert raw == before
    assert "Julia" not in safe["label"]
    assert safe["label"] == "click: oläst, PERSON, RESCUE Sverige"


def test_multiple_localized_mail_row_states_still_find_sender(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: oläst, stjärnmärkt, Julia, RESCUE Sverige",
    })

    assert safe["label"] == "click: oläst, stjärnmärkt, PERSON, RESCUE Sverige"


def test_english_outlook_row_redacts_sender_slot(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Outlook",
        "hostname": "outlook.office.com",
        "label": "click: unread, Julia, Contract renewal",
    })

    assert safe["label"] == "click: unread, PERSON, Contract renewal"


def test_ambiguous_name_word_in_sender_slot_does_not_redact_subject_occurrence(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: unread, May, May pricing report",
    })

    assert safe["label"] == "click: unread, PERSON, May pricing report"


def test_owner_in_mail_row_sender_slot_remains_owner(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: oläst, Koyar, Project update",
    })

    assert safe["label"] == "click: oläst, OWNER, Project update"


def test_org_like_sender_is_not_forced_into_person_token(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: oläst, RESCUE Sverige, Contract renewal",
    })

    assert safe["label"] == "click: oläst, RESCUE Sverige, Contract renewal"


def test_same_comma_text_outside_mail_context_is_untouched(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {"surface": "Some desktop app", "label": "click: oläst, Julia, RESCUE Sverige"}
    assert presentation.redact_for_display(raw) == raw
