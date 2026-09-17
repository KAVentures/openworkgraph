from __future__ import annotations

import importlib


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


def test_unstated_gmail_row_redacts_full_sender_but_preserves_subject(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Scott Walchek , Autonomous AI in Healthcare Tracker just went live , 20:24 , … (AXCheckBox)",
    }
    safe = presentation.redact_for_display(raw)

    assert "Scott Walchek" not in safe["label"]
    assert "PERSON_" in safe["label"]
    assert "Autonomous AI in Healthcare Tracker just went live" in safe["label"]
    assert "20:24" in safe["label"]
    assert "AXCheckBox" in safe["label"]


def test_org_sender_does_not_block_subject_person_redaction(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": (
            "click: vald, oläst, LinkedIn, Koyar Afrasyab, "
            "add Sabrina Bouzenada - Consultant General Surg… (AXCheckBox)"
        ),
    }
    safe = presentation.redact_for_display(raw)

    assert "LinkedIn" in safe["label"]
    assert "Koyar Afrasyab" not in safe["label"]
    assert "OWNER" in safe["label"]
    assert "Sabrina Bouzenada" not in safe["label"]
    assert "PERSON_" in safe["label"]
    assert "Consultant General Surg" in safe["label"]


def test_unstated_mail_row_still_redacts_bare_first_name_sender(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Julia, Weekly review, 20:24, … (AXCheckBox)",
    })

    assert safe["label"].startswith("click: PERSON, Weekly review")


def test_person_name_at_start_of_confirmed_subject_is_redacted(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Outlook",
        "hostname": "outlook.office.com",
        "label": "click: unread, LinkedIn, Julia sent you a message, 20:24, … (AXCheckBox)",
    })

    assert "Julia" not in safe["label"]
    assert "PERSON sent you a message" in safe["label"]


def test_person_after_subject_cue_is_redacted(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: unread, LinkedIn, Meeting with Anna, 20:24, … (AXCheckBox)",
    })

    assert "Meeting with Anna" not in safe["label"]
    assert "Meeting with PERSON" in safe["label"]


def test_org_and_product_phrases_survive_inside_confirmed_mail_subject(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    for subject in (
        "Region Skåne - update",
        "Volvo Cars - update",
        "Klarna Checkout - update",
        "Sankt Görans Sjukhus - update",
    ):
        safe = presentation.redact_for_display({
            "surface": "Gmail",
            "hostname": "mail.google.com",
            "label": f"click: unread, LinkedIn, {subject}, 20:24, … (AXCheckBox)",
        })
        assert subject in safe["label"]


def test_non_mail_title_remains_conservative(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Notion",
        "hostname": "notion.so",
        "window_title": "Scott Walchek - Autonomous AI in Healthcare Tracker",
    }
    assert presentation.redact_for_display(raw) == raw


def test_unconfirmed_comma_text_is_not_reinterpreted_as_mail_row(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Scott Walchek, Project update, draft",
    }
    assert presentation.redact_for_display(raw) == raw
