from __future__ import annotations

import json
import os
from typing import Any

from .db import DATA_DIR

SETTINGS_PATH = DATA_DIR / "browser_signal_settings.json"
DEFAULTS: dict[str, bool] = {
    "performance_timing": True,
    "file_upload_category": False,
    "business_object_references": False,
    "resource_reference_locators": False,
}
SETTING_KEYS = tuple(DEFAULTS)

PROFILES: dict[str, dict[str, bool]] = {
    # Keeps optional business-reference capture off. v0.114 also masks known
    # SaaS object-ID positions in stored URL paths, which is deliberately stricter
    # than the pre-v0.114 privacy-first path behavior.
    "privacy_first": {
        "performance_timing": True,
        "file_upload_category": False,
        "business_object_references": False,
        "resource_reference_locators": False,
    },
    # Correlate the same business object across tools using an installation-keyed
    # local token; do not retain the provider's actual object/thread/record locator.
    "context": {
        "performance_timing": True,
        "file_upload_category": False,
        "business_object_references": True,
        "resource_reference_locators": False,
    },
    # Explicit opt-in for customer-controlled deployments that want an AI to be
    # able to resolve the observed object through its own authorized connector.
    # Do not silently enable unrelated optional sensors when this profile changes.
    "rich_enterprise": {
        "performance_timing": True,
        "file_upload_category": False,
        "business_object_references": True,
        "resource_reference_locators": True,
    },
}


def normalize_settings(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    settings = {key: bool(source.get(key, default)) for key, default in DEFAULTS.items()}
    # A resolver locator is never meaningful or permitted unless the reference
    # feature itself is enabled.
    if not settings["business_object_references"]:
        settings["resource_reference_locators"] = False
    return settings


def apply_profile(name: str, current: Any = None) -> dict[str, bool]:
    base = normalize_settings(current)
    profile = PROFILES.get(str(name or "").strip())
    if profile is None:
        return base
    base.update(profile)
    return normalize_settings(base)


def profile_for_settings(value: Any) -> str:
    settings = normalize_settings(value)
    for name, profile in PROFILES.items():
        if settings == normalize_settings(profile):
            return name
    return "custom"


def load_settings() -> dict[str, bool]:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return normalize_settings(raw)


def save_settings(value: Any) -> dict[str, bool]:
    settings = normalize_settings(value)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)
    return settings


def public_settings() -> dict[str, Any]:
    settings = load_settings()
    return {
        "settings": settings,
        "profile": profile_for_settings(settings),
        "profiles": {name: dict(values) for name, values in PROFILES.items()},
        "defaults": dict(DEFAULTS),
        "captured_signals": {
            "performance_timing": {
                "enabled": settings["performance_timing"],
                "content": False,
                "description": "Rounded top-frame navigation timing only; no resource URLs or page contents.",
            },
            "file_upload_category": {
                "enabled": settings["file_upload_category"],
                "content": False,
                "description": "Optional coarse MIME category only; no filename, path, exact size, hash, or file contents.",
            },
            "business_object_references": {
                "enabled": settings["business_object_references"],
                "content": False,
                "description": "Recognize allowlisted work objects such as a GitHub PR, Google document, Jira issue or Salesforce record. Context mode stores only an installation-keyed correlation token; full URLs are never stored.",
            },
            "resource_reference_locators": {
                "enabled": settings["resource_reference_locators"],
                "content": False,
                "description": "Optional validated provider-specific object locator for connector resolution. Off in Privacy-first and Context modes.",
            },
        },
        "derived_without_new_sensor": [
            "fragmentation",
            "manual_transfer_patterns",
            "rapid_click_candidates",
            "auth_flow_candidates",
            "daily_rhythm",
            "ai_tool_usage",
            "navigation_hunting_candidates",
        ],
        "never_captured_by_these_signals": [
            "typed_text",
            "clipboard_contents",
            "file_names",
            "file_paths",
            "exact_file_sizes",
            "file_contents",
            "microphone_state",
            "ordinary_key_identities",
            "url_query_values",
            "url_fragments_as_urls",
            "page_contents",
        ],
    }


__all__ = [
    "DEFAULTS",
    "PROFILES",
    "SETTING_KEYS",
    "apply_profile",
    "load_settings",
    "normalize_settings",
    "profile_for_settings",
    "public_settings",
    "save_settings",
]
