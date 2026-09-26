from __future__ import annotations

from typing import Any

from sensitive_identifiers import sanitize_event_identifiers

DEFAULT_LOCAL_POLICY: dict[str, Any] = {
    "share_excluded": False,
    "share_window_titles": True,
    "share_metadata": True,
    "allow_agent_events": False,
    "allowed_event_types": [],
    "strip_metadata_keys": [],
}


def normalize_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_LOCAL_POLICY)
    for key in ("share_excluded", "share_window_titles", "share_metadata", "allow_agent_events"):
        if key in source:
            result[key] = bool(source[key])
    for key in ("allowed_event_types", "strip_metadata_keys"):
        raw = source.get(key)
        if isinstance(raw, list):
            result[key] = sorted({str(x).strip() for x in raw if str(x).strip()})
    return result


def _restrict_allowlist(local: list[str], remote: list[str]) -> tuple[list[str], bool]:
    """Return the narrowest allowlist and whether it explicitly denies all.

    Existing configuration semantics are preserved: an empty allowlist on either
    side means that side is unrestricted. The only ambiguous case was two
    non-empty, disjoint allowlists: their empty intersection must mean *share
    nothing*, not unrestricted. ``deny_all`` carries that internal result without
    changing the public config format.
    """
    a = {str(x) for x in local if str(x)}
    b = {str(x) for x in remote if str(x)}
    if a and b:
        intersection = sorted(a & b)
        return intersection, not bool(intersection)
    return sorted(a or b), False


def _effective_agent_sharing(
    local_policy: dict[str, Any] | None,
    organization_policy: dict[str, Any] | None,
) -> bool:
    """Return the restrictive effective agent-sharing choice.

    Agent evidence requires an explicit endpoint-local opt-in. A missing local key
    therefore fails closed. The organization side is a narrowing layer: a missing
    organization key means that the organization imposes no additional restriction,
    while an explicit organization False still disables sharing. This preserves the
    rule that a remote organization can never broaden the endpoint's local choice.
    """
    local_source = local_policy if isinstance(local_policy, dict) else {}
    remote_source = organization_policy if isinstance(organization_policy, dict) else {}
    local_allows = bool(local_source.get("allow_agent_events", False))
    remote_allows = bool(remote_source.get("allow_agent_events", True))
    return local_allows and remote_allows


def merge_policies(local_policy: dict[str, Any] | None, organization_policy: dict[str, Any] | None) -> dict[str, Any]:
    """Combine policies so a remote organization can never broaden the local floor."""
    local = normalize_policy(local_policy)
    remote = normalize_policy(organization_policy)
    allowed_event_types, deny_all_event_types = _restrict_allowlist(
        local["allowed_event_types"], remote["allowed_event_types"]
    )
    return {
        "share_excluded": bool(local["share_excluded"] and remote["share_excluded"]),
        "share_window_titles": bool(local["share_window_titles"] and remote["share_window_titles"]),
        "share_metadata": bool(local["share_metadata"] and remote["share_metadata"]),
        "allow_agent_events": _effective_agent_sharing(local_policy, organization_policy),
        "allowed_event_types": allowed_event_types,
        "_deny_all_event_types": deny_all_event_types,
        "strip_metadata_keys": sorted(set(local["strip_metadata_keys"]) | set(remote["strip_metadata_keys"])),
    }


def _strip_keys(value: Any, blocked: set[str]) -> Any:
    if isinstance(value, dict):
        return {str(k): _strip_keys(v, blocked) for k, v in value.items() if str(k) not in blocked}
    if isinstance(value, list):
        return [_strip_keys(x, blocked) for x in value]
    return value


def prepare_event_for_gateway(event: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if str(event.get("source") or "") == "agent" and policy.get("allow_agent_events") is not True:
        return None
    if bool(metadata.get("excluded")) and not policy.get("share_excluded", False):
        return None
    if policy.get("_deny_all_event_types", False):
        return None
    allowed = {str(x) for x in policy.get("allowed_event_types") or []}
    if allowed and str(event.get("event_type") or "") not in allowed:
        return None

    result = dict(event)
    result.pop("screenshot_path", None)
    if not policy.get("share_window_titles", True):
        result["window_title"] = ""
    if not policy.get("share_metadata", True):
        result["metadata"] = {"gateway_sync": {"source": "local_privacy_hardened_store", "raw_metadata_preserved": False}}
    else:
        blocked = {str(x) for x in policy.get("strip_metadata_keys") or []}
        safe_metadata = _strip_keys(metadata, blocked)
        if not isinstance(safe_metadata, dict):
            safe_metadata = {}
        safe_metadata["gateway_sync"] = {
            "source": "local_privacy_hardened_store",
            "raw_metadata_preserved": not bool(blocked),
        }
        result["metadata"] = safe_metadata

    # Defense-in-depth: the canonical local store has already been hardened, but
    # synchronize only another sanitized copy.
    return sanitize_event_identifiers(result)
