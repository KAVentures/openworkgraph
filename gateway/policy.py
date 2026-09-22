from __future__ import annotations

from typing import Any

DEFAULT_ORGANIZATION_POLICY: dict[str, Any] = {
    "share_excluded": False,
    "share_window_titles": True,
    "share_metadata": True,
    "allowed_event_types": [],
    "strip_metadata_keys": [],
}

FORBIDDEN_CONTENT_KEYS = {
    "typed_text",
    "typed_value",
    "typed_values",
    "input_value",
    "field_value",
    "password",
    "password_value",
    "clipboard_text",
    "clipboard_content",
    "clipboard_contents",
    "key_identity",
    "key_identities",
}

FORBIDDEN_TRUE_FLAGS = {
    "typed_text_captured",
    "typed_values_captured",
    "clipboard_content_captured",
    "clipboard_contents_captured",
    "key_identity_captured",
    "key_identities_captured",
    "screenshot_bytes_captured",
}


def normalize_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_ORGANIZATION_POLICY)
    for key in ("share_excluded", "share_window_titles", "share_metadata"):
        if key in source:
            result[key] = bool(source[key])
    for key in ("allowed_event_types", "strip_metadata_keys"):
        raw = source.get(key)
        if isinstance(raw, list):
            result[key] = sorted({str(x).strip() for x in raw if str(x).strip()})
    return result


def _privacy_contract_violation(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        privacy = value.get("privacy")
        if isinstance(privacy, dict):
            for key in ("typed_values", "clipboard_contents", "key_identities"):
                if privacy.get(key) is True:
                    return f"{path}.privacy.{key} contradicts the OpenWorkGraph privacy contract"
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_CONTENT_KEYS and child not in (None, "", False, [], {}):
                return f"{path}.{key} contains content OpenWorkGraph must not ingest"
            if lowered in FORBIDDEN_TRUE_FLAGS and child is True:
                return f"{path}.{key}=true contradicts the OpenWorkGraph privacy contract"
            found = _privacy_contract_violation(child, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found = _privacy_contract_violation(child, f"{path}[{i}]")
            if found:
                return found
    return None


def privacy_contract_violation(event: dict[str, Any]) -> str | None:
    if event.get("screenshot_bytes") not in (None, "", False, b""):
        return "$.screenshot_bytes is not accepted by the Gateway"
    return _privacy_contract_violation(event)
