"""Device-before-upload privacy boundary and Gateway opt-in rollout tests."""
import copy
import json

from fastapi.testclient import TestClient

from connector.cloud_privacy import DEVICE_REDACTION_VERSION, project_event_for_upload
from connector.policy import prepare_event_for_gateway
from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings


def _gateway(tmp_path):
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    app = create_app(settings=GatewaySettings(
        database_url=db.database_url, admin_token="admin-secret",
        enrollment_token="enroll-secret",
    ), db=db)
    return app, db


def _enroll(client, organization, device, actor):
    response = client.post(
        "/v1/devices/enroll", headers={"Authorization": "Bearer enroll-secret"},
        json={"organization_id": organization, "device_id": device, "actor_id": actor},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _service(client, organization):
    response = client.post(
        "/v1/admin/integration-tokens",
        headers={"Authorization": "Bearer admin-secret"},
        json={"organization_id": organization, "scopes": ["evidence:read"], "label": "read"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _event(event_id="evt-1"):
    return {
        "event_id": event_id, "observed_at": "2026-10-13T08:00:00+00:00",
        "schema_version": "1.0", "source": "browser_extension",
        "device_id": "device-1", "sensor_id": "browser:sensor",
        "session_id": "session-1", "app": "Google Chrome",
        "window_title": "Samarbetsavtal 2026", "event_type": "browser_click",
        "duration_seconds": 0, "metadata": {
            "action": "click", "clipboard_transfer_id": "transfer-123",
            "page": {"hostname": "mail.example.com", "pathname": "/inbox"},
            "target": {"role": "button", "label": "Hämta igen"},
        },
    }


_SECRET = "synthetic-device-scoped-secret-for-tests"


def test_device_projection_drops_preview_body_and_sanitizes_structural_labels():
    event = _event("evt-upload-1")
    event["window_title"] = "Gmail · Katarina kommenterade avtalet"
    event["metadata"]["page"].update({
        "preview": "Patient: Karl Gustafsson, 54 år",
        "title": "Samarbetsavtal 2026", "pathname": "/users/katarina/private",
        "snippet": "Katarina skrev confidential details",
    })
    event["metadata"]["target"] = {
        "role": "button", "label": "Svar till Michaela",
        "description": "patient name and diagnosis",
        "preview": "Patient: Karl Gustafsson", "help": "Secret",
    }
    event["metadata"].update({
        "preview": "Michaela has the letter", "body": "Michaela",
        "message": "Katarina", "typed_value": "Karl Gustafsson",
        "nested_private": {"patient": "Karl Gustafsson"},
        "custom_context": "Katarina",
    })
    before = copy.deepcopy(event)
    safe = project_event_for_upload(
        prepare_event_for_gateway(event, {
            "share_window_titles": True, "share_metadata": True,
            "allow_agent_events": True,
        }), secret=_SECRET,
    )
    assert event == before, "No local canonical history rewriting"
    serialized = json.dumps(safe, ensure_ascii=False)
    for forbidden in ("Katarina", "Michaela", "Karl Gustafsson", "confidential details",
                      "/users/katarina", "typed_value", "snippet", "preview", "custom_context"):
        if forbidden == "preview":
            # Only an explicit boolean indicates that preview text was dropped.
            assert '"preview":' not in serialized
        else:
            assert forbidden not in serialized
    assert safe["window_title"].startswith("Gmail · PERSON_")
    assert safe["metadata"]["target"]["label"].startswith("Svar till ")
    assert safe["metadata"]["page"]["hostname"] == "mail.example.com"
    assert "pathname" not in safe["metadata"]["page"]
    assert safe["metadata"]["clipboard_transfer_id"] == "transfer-123"
    assert safe["metadata"]["gateway_sync"]["device_redaction_version"] == DEVICE_REDACTION_VERSION
    assert safe["metadata"]["gateway_sync"]["raw_metadata_preserved"] is False


def test_device_projection_preserves_useful_ui_and_holds_clinical_context():
    for title in ("Samarbetsavtal 2026", "Hämta igen",
                  "Välkommen till oss på mottagningen",
                  "Pull requests · KAVentures/openworkgraph"):
        event = _event()
        event["window_title"] = title
        projected = project_event_for_upload(event, secret=_SECRET)
        assert projected["window_title"] == title
    clinical = _event()
    clinical["window_title"] = "Patient: Karl Gustafsson, 54 år – diabetes"
    clinical["metadata"]["target"]["label"] = "Patient: Karl Gustafsson"
    projected = project_event_for_upload(clinical, secret=_SECRET)
    assert projected["window_title"] == "[SENSITIVE_HEALTH_CONTEXT_WITHHELD]"
    assert projected["metadata"]["target"]["label"] == "[SENSITIVE_HEALTH_CONTEXT_WITHHELD]"
    assert "Karl Gustafsson" not in str(projected)


def test_device_projection_redacts_handles_and_social_messages():
    for value, secret in (
        ("Slack · @lisa.k reacted", "@lisa.k"),
        ("Yusuf skrev i #general", "Yusuf"),
        ("Katarina kommenterade en PR", "Katarina"),
        ("Thanks, Olivia!", "Olivia"),
        ("Hey Ahmed", "Ahmed"),
    ):
        row = _event()
        row["window_title"] = value
        projected = project_event_for_upload(row, secret=_SECRET)
        assert secret.casefold() not in projected["window_title"].casefold(), projected


def test_gateway_rejects_legacy_uploads_when_device_gate_is_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OWG_REQUIRE_DEVICE_REDACTION", "true")
    app, db = _gateway(tmp_path)
    with TestClient(app) as client:
        device = _enroll(client, "org", "d1", "actor")
        headers = {"Authorization": "Bearer " + device["token"]}
        raw = _event("raw-1")
        raw["window_title"] = "Patient: Karl Gustafsson"
        blocked = client.post("/v1/evidence/batch", headers=headers, json={"events": [raw]})
        assert blocked.status_code == 400
        assert "projection" in blocked.text
        safe = project_event_for_upload(raw, secret=_SECRET)
        result = client.post("/v1/evidence/batch", headers=headers, json={"events": [safe]})
        assert result.status_code == 200, result.text
        service = _service(client, "org")
        evidence = client.get("/v1/workflow-trace", headers={
            "Authorization": "Bearer " + service["token"],
        }).json()
        assert evidence["returned"] == 1
        assert "Karl Gustafsson" not in str(evidence)
        assert evidence["rows"][0]["window_title"] == "[SENSITIVE_HEALTH_CONTEXT_WITHHELD]"


def test_device_drops_multiline_preview_and_user_home_paths():
    event = _event()
    event["window_title"] = "Inbox preview: first line\nsecond line from patient"
    event["metadata"]["target"]["label"] = "Line one\nLine two"
    output = project_event_for_upload(event, secret=_SECRET)
    assert output["window_title"] == "[SENSITIVE_HEALTH_CONTEXT_WITHHELD]"
    assert output["metadata"]["target"]["label"] == "[LONG_FREE_TEXT_WITHHELD]"
    for local_path in ("C:\\Users\\Michaela\\Documents\\cv.pdf",
                       "/Users/Michaela/Documents/file.pdf", "/home/koyar/data"):
        event["window_title"] = local_path
        assert project_event_for_upload(event, secret=_SECRET)["window_title"] == (
            "[LOCAL_FILE_PATH_WITHHELD]"
        )


def test_gateway_redaction_projection_fails_closed_on_bad_secret():
    try:
        project_event_for_upload(_event(), secret="")
    except ValueError:
        pass
    else:
        raise AssertionError("A missing local secret must never upload unredacted data")


def test_optional_local_ner_redacts_new_unlisted_identity(monkeypatch):
    monkeypatch.setenv("OWG_LOCAL_NER_MODEL", "sv_core_news_sm")

    def fake_model(_model_name):
        from types import SimpleNamespace
        class Model:
            def __call__(self, content):
                if "Zymboria" not in content:
                    return SimpleNamespace(ents=[])
                start = content.index("Zymboria")
                entity = SimpleNamespace(
                    start_char=start, end_char=start + len("Zymboria"),
                    label_="PER", text="Zymboria",
                )
                return SimpleNamespace(ents=[entity])
        return Model()

    monkeypatch.setattr("connector.cloud_privacy._load_local_ner", fake_model)
    e = _event()
    e["window_title"] = "Zymboria skrev ett dokument"
    output = project_event_for_upload(e, secret=_SECRET)
    assert "Zymboria" not in str(output)
    assert output["window_title"].startswith("PERSON_")
    assert "skrev ett dokument" in output["window_title"]


def test_configured_ner_error_stops_upload_no_unredacted_fallback(monkeypatch):
    monkeypatch.setenv("OWG_LOCAL_NER_MODEL", "sv_core_news_sm")

    def fail(_model_name):
        raise RuntimeError("NER not installed")
    monkeypatch.setattr("connector.cloud_privacy._load_local_ner", fail)
    import pytest
    with pytest.raises(RuntimeError, match="NER"):
        project_event_for_upload(_event(), secret=_SECRET)
