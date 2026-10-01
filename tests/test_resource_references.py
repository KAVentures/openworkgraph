from __future__ import annotations

from browser_privacy import harden_browser_event, minimize_known_resource_path
from resource_references import normalize_resource_reference
from server import browser_signal_settings


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


def _reference():
    return {
        "provider": "salesforce",
        "resource_kind": "record",
        "host": "acme.lightning.force.com",
        "resolver_locator": "Opportunity:006ABCDEF123456789",
        "resource_ref": "owg:r:000000000000000000000000",
        "resolution": "observed",
    }


def test_privacy_first_drops_business_reference_and_masks_known_route_id():
    safe = harden_browser_event(_event(_reference()), _config())
    metadata = safe["metadata"]
    assert "resource_reference" not in metadata
    assert metadata["page"]["pathname"] == "/lightning/r/Opportunity/:id/view"
    assert "006ABCDEF123456789" not in str(safe)
    assert "SECRET" not in str(safe)
    assert metadata["privacy"]["business_object_references"] is False


def test_context_mode_keeps_only_stable_local_reference_token():
    safe = harden_browser_event(
        _event(_reference()),
        _config(business_object_references=True, resource_reference_locators=False),
    )
    ref = safe["metadata"]["resource_reference"]
    assert ref["provider"] == "salesforce"
    assert ref["resource_kind"] == "record"
    assert ref["resource_ref"].startswith("owg:r:")
    assert ref["resource_ref"] != "owg:r:000000000000000000000000"
    assert "resolver_locator" not in ref
    assert safe["metadata"]["page"]["pathname"] == "/lightning/r/Opportunity/:id/view"


def test_rich_mode_keeps_only_validated_minimal_locator():
    safe = harden_browser_event(
        _event(_reference()),
        _config(business_object_references=True, resource_reference_locators=True),
    )
    ref = safe["metadata"]["resource_reference"]
    assert ref["resolver_locator"] == "Opportunity:006ABCDEF123456789"
    assert set(ref) == {"provider", "resource_kind", "host", "resource_ref", "resolution", "resolver_locator"}
    assert safe["metadata"]["page"]["pathname"] == "/lightning/r/Opportunity/006ABCDEF123456789/view"


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


def test_minimized_reference_can_be_revalidated_without_locator():
    rich = normalize_resource_reference(_reference(), include_locator=False)
    assert rich is not None
    minimized = normalize_resource_reference(rich, include_locator=False)
    assert minimized == rich


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
