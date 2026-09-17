from __future__ import annotations

import importlib
import json


def _learn(value):
    from server.privacy_pipeline import learn_persistent_identities
    learn_persistent_identities(value)


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


def test_shared_first_name_never_asserts_one_stable_identity(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)

    first_payload = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "From: Anna Svensson <anna.s@acme.com>",
    }
    second_payload = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "From: Anna Berg <anna.b@acme.com>",
    }
    _learn(first_payload)
    _learn(second_payload)
    first = presentation.redact_for_display(first_payload)
    second = presentation.redact_for_display(second_payload)
    bare = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Meeting with Anna",
    })

    assert "Anna Svensson" not in first["label"]
    assert "Anna Berg" not in second["label"]
    assert "PERSON_" in first["label"]
    assert "PERSON_" in second["label"]
    assert bare["label"] == "Meeting with PERSON"
    assert "PERSON_" not in bare["label"]


def test_first_name_only_is_generic_even_when_only_one_identity_is_known(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    identity = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "From: Erik Nilsson <erik.nilsson@acme.com>",
    }
    _learn(identity)
    presentation.redact_for_display(identity)

    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Erik follow-up",
    })

    assert safe["label"] == "PERSON follow-up"


def test_ambiguous_word_names_need_strong_person_context(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    identity = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "From: May Jensen <may.jensen@acme.com>",
    }
    _learn(identity)
    presentation.redact_for_display(identity)

    report = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "May pricing report",
    })
    meeting = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Meeting with May",
    })
    checkbox = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: Select May, Contract renewal",
    })

    assert report["label"] == "May pricing report"
    assert meeting["label"] == "Meeting with PERSON"
    assert "Select PERSON," in checkbox["label"]
    assert "Contract renewal" in checkbox["label"]


def test_common_word_like_names_are_not_blindly_destroyed(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    for text in ("Bill approval", "Mark complete", "Rose project", "Hope update"):
        safe = presentation.redact_for_display({
            "surface": "Gmail",
            "hostname": "mail.google.com",
            "label": text,
        })
        assert safe["label"] == text


def test_person_specific_field_redacts_single_name_generically(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({"surface": "Salesforce", "sender": "May"})
    assert safe["sender"] == "PERSON"


def test_owner_first_name_is_always_owner(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Re: Question for Koyar",
    })
    assert safe["label"] == "Re: Question for OWNER"


def test_registry_migrates_old_single_token_format(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    token = presentation._token("PERSON", "anna.s@acme.com")
    registry = {
        presentation._alias_hash("Anna Svensson"): token,
        presentation._alias_hash("Anna"): token,
    }
    (tmp_path / ".presentation_people.json").write_text(json.dumps(registry), encoding="utf-8")

    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Anna follow-up",
    })
    assert safe["label"] == "PERSON follow-up"


def test_policy_remains_presentation_only(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    raw = {
        "event_id": "evt-1",
        "session_id": "session-1",
        "observed_at": "2026-09-17T20:00:00+02:00",
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "Meeting with Anna",
        "keypress_count": 17,
        "duration_seconds": 12.5,
    }
    before = json.loads(json.dumps(raw))
    safe = presentation.redact_for_display(raw)

    assert raw == before
    assert safe["event_id"] == raw["event_id"]
    assert safe["session_id"] == raw["session_id"]
    assert safe["observed_at"] == raw["observed_at"]
    assert safe["keypress_count"] == 17
    assert safe["duration_seconds"] == 12.5
