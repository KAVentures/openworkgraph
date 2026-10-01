from __future__ import annotations

import hashlib
import hmac
import re

import pytest

from browser_privacy import harden_browser_event, minimize_known_resource_path
from resource_references import normalize_resource_reference
from server import browser_signal_settings
from server.local_auth import ensure_browser_secret


_SENSOR_NAMESPACE = "openworkgraph-resource-reference-sensor-v1:"


@pytest.fixture(autouse=True)
def isolated_resource_reference_secrets(tmp_path, monkeypatch):
    # Resource-reference tests must never write auth material into the checkout,
    # and each test gets a clean installation-equivalent secret boundary.
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))


def _config(**overrides):
    value = {
        "excluded_apps": [],
        "excluded_title_patterns": [],
        "excluded_browser_host_patterns": [],
        "business_object_references": False,
        "resource_reference_locators": False,
    }
    value.update(overrides)
    return value


def _event(reference):
    return {
        "event_id": "evt-1",
        "observed_at": "2026-10-01T08:00:00+00:00",
        "device_id": "d1",
        "session_id": "s1",
        "app": "Google Chrome",
        "window_title": "Opportunity",
        "event_type": "browser_resource_reference_observed",
        "screenshot_path": None,
        "metadata": {
            "source": "browser_extension",
            "action": "resource_reference_observed",
            "page": {
                "origin": "https://acme.lightning.force.com",
                "hostname": "acme.lightning.force.com",
                "pathname": "/lightning/r/Opportunity/006ABCDEF123456789/view?token=SECRET#x",
                "title": "Opportunity",
            },
            "resource_reference": reference,
        },
    }


def _canonical(reference):
    return "|".join((
        str(reference["provider"]).lower(),
        str(reference["resource_kind"]).lower(),
        str(reference["host"]).lower(),
        str(reference["resolver_locator"]),
    ))


def _sensor_ref(reference):
    digest = hmac.new(
        ensure_browser_secret().encode("utf-8"),
        (_SENSOR_NAMESPACE + _canonical(reference)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"owg:e:{digest[:24]}"


def _legacy_plain_hash(reference):
    digest = hashlib.sha256(_canonical(reference).encode("utf-8")).hexdigest()
    return f"owg:r:{digest[:24]}"


def _reference():
    value = {
        "provider": "salesforce",
        "resource_kind": "record",
        "host": "acme.lightning.force.com",
        "resolver_locator": "Opportunity:006ABCDEF123456789",
        "resolution": "observed",
    }
    value["resource_ref"] = _sensor_ref(value)
    return value


def _context_reference():
    value = _reference()
    value.pop("resolver_locator")
    return value


def test_privacy_first_drops_business_reference_and_masks_known_route_id():
    safe = harden_browser_event(_event(_reference()), _config())
    metadata = safe["metadata"]
    assert "resource_reference" not in metadata
    assert metadata["page"]["pathname"] == "/lightning/r/Opportunity/:id/view"
    assert "006ABCDEF123456789" not in str(safe)
    assert "SECRET" not in str(safe)
    assert metadata["privacy"]["business_object_references"] is False


def test_context_mode_rekeys_sensor_fingerprint_before_persistence():
    incoming = _context_reference()
    sensor_ref = incoming["resource_ref"]
    safe = harden_browser_event(
        _event(incoming),
        _config(business_object_references=True, resource_reference_locators=False),
    )
    ref = safe["metadata"]["resource_reference"]
    assert ref["provider"] == "salesforce"
    assert ref["resource_kind"] == "record"
    assert re.fullmatch(r"owg:r:[0-9a-f]{32}", ref["resource_ref"])
    assert ref["resource_ref"] != sensor_ref
    assert sensor_ref not in str(safe)
    assert "resolver_locator" not in ref
    assert safe["metadata"]["page"]["pathname"] == "/lightning/r/Opportunity/:id/view"


def test_persisted_token_is_not_old_dictionary_attackable_plain_hash():
    rich_input = _reference()
    old_plain = _legacy_plain_hash(rich_input)
    sensor_ref = rich_input["resource_ref"]
    safe = harden_browser_event(
        _event(rich_input),
        _config(business_object_references=True, resource_reference_locators=False),
    )
    stored = safe["metadata"]["resource_reference"]["resource_ref"]
    assert stored != old_plain
    assert stored != sensor_ref
    assert old_plain not in str(safe)
    assert sensor_ref not in str(safe)


def test_rich_mode_keeps_only_validated_minimal_locator_and_rekeys_token():
    incoming = _reference()
    sensor_ref = incoming["resource_ref"]
    safe = harden_browser_event(
        _event(incoming),
        _config(business_object_references=True, resource_reference_locators=True),
    )
    ref = safe["metadata"]["resource_reference"]
    assert ref["resolver_locator"] == "Opportunity:006ABCDEF123456789"
    assert re.fullmatch(r"owg:r:[0-9a-f]{32}", ref["resource_ref"])
    assert ref["resource_ref"] != sensor_ref
    assert set(ref) == {"provider", "resource_kind", "host", "resource_ref", "resolution", "resolver_locator"}
    assert safe["metadata"]["page"]["pathname"] == "/lightning/r/Opportunity/006ABCDEF123456789/view"


def test_conflicting_sensor_token_is_rejected_when_locator_is_present():
    value = _reference()
    value["resource_ref"] = "owg:e:000000000000000000000000"
    safe = harden_browser_event(
        _event(value),
        _config(business_object_references=True, resource_reference_locators=True),
    )
    assert "resource_reference" not in safe["metadata"]


def test_known_resource_paths_mask_ids_without_collapsing_route_structure():
    assert minimize_known_resource_path(
        "docs.google.com", "/document/d/1A2b3C4d5E6f7G8h9I0jKLMNop/edit"
    ) == "/document/d/:id/edit"
    assert minimize_known_resource_path(
        "github.com", "/KAVentures/openworkgraph/pull/123/files"
    ) == "/KAVentures/openworkgraph/pull/:id/files"
    assert minimize_known_resource_path(
        "acme.atlassian.net", "/browse/OPS-431"
    ) == "/browse/:id"
    assert minimize_known_resource_path(
        "linear.app", "/acme/issue/ENG-42/fix-the-thing"
    ) == "/acme/issue/:id/fix-the-thing"


def test_invalid_or_excluded_reference_never_survives():
    invalid = _reference()
    invalid["resolver_locator"] = "Opportunity:bad?token=SECRET"
    safe = harden_browser_event(
        _event(invalid),
        _config(business_object_references=True, resource_reference_locators=True),
    )
    assert "resource_reference" not in safe["metadata"]

    excluded = harden_browser_event(
        _event(_reference()),
        _config(
            business_object_references=True,
            resource_reference_locators=True,
            excluded_browser_host_patterns=[r"force\.com"],
        ),
    )
    assert excluded["app"] == "Excluded"
    assert "resource_reference" not in excluded["metadata"]


def test_minimized_persisted_reference_is_idempotent_without_locator():
    rich = normalize_resource_reference(_reference(), include_locator=False)
    assert rich is not None
    minimized = normalize_resource_reference(rich, include_locator=False)
    assert minimized == rich


def test_same_sensor_reference_gets_different_stored_token_in_another_install(tmp_path, monkeypatch):
    reference = _reference()
    incoming = dict(reference)
    incoming.pop("resolver_locator")
    first = normalize_resource_reference(incoming, include_locator=False)
    assert first is not None

    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "second-install-auth"))
    second = normalize_resource_reference(incoming, include_locator=False)
    assert second is not None
    assert second["resource_ref"] != first["resource_ref"]


def test_privacy_profiles_are_explicit_and_backwards_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_signal_settings, "SETTINGS_PATH", tmp_path / "browser_signal_settings.json")
    defaults = browser_signal_settings.public_settings()
    assert defaults["profile"] == "privacy_first"
    assert defaults["settings"]["business_object_references"] is False
    assert defaults["settings"]["performance_timing"] is True
    assert defaults["settings"]["file_upload_category"] is False

    context = browser_signal_settings.save_settings(
        browser_signal_settings.apply_profile("context", defaults["settings"])
    )
    assert browser_signal_settings.profile_for_settings(context) == "context"
    assert context["business_object_references"] is True
    assert context["resource_reference_locators"] is False
    assert context["file_upload_category"] is False

    rich = browser_signal_settings.save_settings(
        browser_signal_settings.apply_profile("rich_enterprise", context)
    )
    assert browser_signal_settings.profile_for_settings(rich) == "rich_enterprise"
    assert rich["resource_reference_locators"] is True
    assert rich["file_upload_category"] is False

    privacy = browser_signal_settings.save_settings(
        browser_signal_settings.apply_profile("privacy_first", rich)
    )
    assert browser_signal_settings.profile_for_settings(privacy) == "privacy_first"
    assert privacy["business_object_references"] is False
    assert privacy["resource_reference_locators"] is False
