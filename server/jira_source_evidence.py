from __future__ import annotations

"""Opt-in, local-only Jira Cloud source receipts for an observed issue.

No new credential or raw-activity storage, no default network calls, and no
cross-tenant lookup. A user's per-installation environment provides an explicit
Jira tenant and restricted read access; the existing browser's opaque issue ref
must have been observed in retained evidence before record_graph calls us.

The source issues API is authoritative only for Jira's *current issue state*.
It does not establish refund/payment/customer acknowledgement or a successful
human-to-human handoff.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlsplit
from typing import Any, Callable

import httpx

from .local_auth import ensure_api_token
from .local_reference_lookup import resolve_resource_reference

_ISSUE = re.compile(r"^[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$")
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$")
_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_CLOUD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ACTOR = re.compile(r"^[A-Za-z0-9:._-]{1,128}$")
_EVENT = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
_UTC_CATEGORIES = frozenset({"new", "indeterminate", "done"})
_MAX_AUDIT = 40
_MAX_BYTES = 320_000


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "availability": "unknown", "reason": reason, "provider": "jira",
        "verified_business_outcome": False, "source_state_reported": False,
        "not_a_claim_of_business_success": True,
    }


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        return ""
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(timezone.utc).isoformat() if at.tzinfo else ""
    except (ValueError, OverflowError):
        return ""


def _fingerprint(category: str, value: str, *, key: bytes) -> str:
    digest = hmac.new(key, f"jira:{category}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"{category}:{digest}"


def _jira_get(url: str, params: dict[str, Any], token: str) -> Any:
    """Bound network responses; don't follow a redirect that might receive auth."""
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.netloc != "api.atlassian.com":
        raise ValueError("untrusted_atlassian_api_target")
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        follow_redirects=False, trust_env=False, timeout=8.0,
    ) as client:
        with client.stream("GET", url, params=params, headers={"Accept": "application/json"}) as response:
            if response.status_code != 200:
                raise ValueError("source_response_unavailable")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > _MAX_BYTES:
                    raise ValueError("source_response_too_large")
    parsed = json.loads(data)
    if not isinstance(parsed, (dict, list)):
        raise ValueError("invalid_source_response")
    return parsed


def _safe_audit(
    payload: dict[str, Any], issue_key: str, *, key: bytes,
) -> tuple[list[dict[str, Any]], bool, int]:
    values = payload.get("values") if isinstance(payload.get("values"), list) else []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in values[:_MAX_AUDIT]:
        if not isinstance(entry, dict):
            continue
        event_id = str(entry.get("id") or "")
        timestamp = _timestamp(entry.get("created"))
        if not _EVENT.fullmatch(event_id) or not timestamp:
            continue
        items = entry.get("items") if isinstance(entry.get("items"), list) else []
        for index, item in enumerate(items[:30]):
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "").casefold()
            if field not in {"status", "assignee"}:
                continue
            evidence_id = f"{issue_key}:{event_id}:{index}"
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            row: dict[str, Any] = {
                "source_event_ref": _fingerprint("audit", evidence_id, key=key),
                "observed_at": timestamp,
                "action": "status_transition" if field == "status" else "assignee_transition",
                "provenance": "authenticated_jira_issue_changelog",
                "usernames_and_comments_withheld": True,
            }
            if field == "status":
                before, after = item.get("from"), item.get("to")
                if (isinstance(before, str) and _ACTOR.fullmatch(before)
                        and isinstance(after, str) and _ACTOR.fullmatch(after)
                        and before != after):
                    # Jira's stable status *IDs* expose reversals without
                    # leaking custom status labels or claiming failed work.
                    row.update({
                        "from_status_ref": _fingerprint("status", before, key=key),
                        "to_status_ref": _fingerprint("status", after, key=key),
                    })
            if field == "assignee":
                before, after = item.get("from"), item.get("to")
                if before == after:
                    continue
                if before is not None and (not isinstance(before, str) or not _ACTOR.fullmatch(before)):
                    continue
                if after is not None and (not isinstance(after, str) or not _ACTOR.fullmatch(after)):
                    continue
                row.update({
                    "from_actor_ref": _fingerprint("actor", before, key=key) if before else None,
                    "to_actor_ref": _fingerprint("actor", after, key=key) if after else None,
                    "assignment_transition_verified": True,
                    "human_handoff_or_acceptance_verified": False,
                })
            rows.append(row)

    # A partial/missing changelog is not evidence of no reassignment or rework.
    try:
        total = int(payload["total"])
    except (ValueError, TypeError, KeyError):
        total = -1
    truncated = (
        total < 0 or total > len(values) or len(values) > _MAX_AUDIT
        or payload.get("isLast") is not True
    )
    rows.sort(key=lambda x: (x["observed_at"], x["source_event_ref"]))
    return rows[:_MAX_AUDIT], truncated or len(rows) > _MAX_AUDIT, len(rows)


def jira_issue_receipt(
    resource_ref: str, *,
    environment: dict[str, str] | None = None,
    resolver: Callable[[str], dict[str, Any] | None] = resolve_resource_reference,
    reader: Callable[[str, dict[str, Any], str], Any] = _jira_get,
    key_factory: Callable[[], str] = ensure_api_token,
) -> dict[str, Any]:
    """Return only bounded source-derived state and metadata-free audit receipts.

    This function is only reachable from the guarded record graph when an exact
    retained local evidence ref identifies a Jira issue. Its defense-in-depth
    validation refuses guessed or cross-tenant resolver records.
    """
    env = os.environ if environment is None else environment
    if str(env.get("OWG_JIRA_SOURCE_READ_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return _unknown("jira_source_read_opt_in_off")
    if not _REF.fullmatch(str(resource_ref or "")):
        return _unknown("invalid_opaque_record_reference")

    host = str(env.get("OWG_JIRA_HOST") or "").strip().lower()
    email = str(env.get("OWG_JIRA_ACCOUNT_EMAIL") or "").strip()
    token = str(env.get("OWG_JIRA_API_TOKEN") or "").strip()
    if not _TENANT.fullmatch(host) or not email or not token:
        return _unknown("jira_source_configuration_missing")
    if any(c in email for c in "\r\n") or len(email) > 254 or len(token) > 1024:
        return _unknown("jira_source_configuration_invalid")

    info = resolver(resource_ref)
    if not isinstance(info, dict) or (
        info.get("provider"), info.get("resource_kind"), info.get("host")
    ) != ("jira", "issue", host):
        return _unknown("jira_source_tenant_or_record_mismatch")
    issue_key = str(info.get("resolver_locator") or "")
    if not _ISSUE.fullmatch(issue_key):
        return _unknown("invalid_jira_issue_key")

    auth = httpx.BasicAuth(email, token)
    base = f"https://{host}/rest/api/3/issue/{issue_key}"
    try:
        issue = reader(base, {"fields": "status,resolution,resolutiondate,updated"}, auth)
        if str(issue.get("key") or "").upper() != issue_key:
            return _unknown("jira_issue_identity_mismatch")
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        category = status.get("statusCategory") if isinstance(status.get("statusCategory"), dict) else {}
        state = str(category.get("key") or "").lower()
        if state not in _UTC_CATEGORIES:
            return _unknown("jira_current_issue_status_unknown")

        resolution = fields.get("resolution")
        resolution_date = _timestamp(fields.get("resolutiondate"))
        conflicting = (state != "done" and resolution is not None)
        if conflicting:
            return _unknown("jira_resolution_status_conflict")
        verified_resolution = state == "done" and isinstance(resolution, dict) and bool(resolution_date)
        observed_at = datetime.now(timezone.utc).isoformat()
        # Read a separate *bounded* page of source audit history. A failure of
        # optional history does not erase the separately fetched current state.
        audit: list[dict[str, Any]] = []
        audit_truncated = True
        audit_available = False
        audit_count = 0
        try:
            changes = reader(base + "/changelog", {"startAt": 0, "maxResults": _MAX_AUDIT}, auth)
            audit, audit_truncated, audit_count = _safe_audit(
                changes, issue_key, key=key_factory().encode("utf-8"),
            )
            audit_available = True
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError):
            pass

        # A → B followed by B → A is a structural status reversal. It is not
        # evidence of a business failure, nor proof of *all* rework when the
        # changelog is incomplete.
        transitions = [(row.get("from_status_ref"), row.get("to_status_ref"))
                       for row in audit if row["action"] == "status_transition"
                       and row.get("from_status_ref") and row.get("to_status_ref")]
        seen_transitions: set[tuple[str, str]] = set()
        reversed_transitions = 0
        for frm, to in transitions:
            if (to, frm) in seen_transitions:
                reversed_transitions += 1
            seen_transitions.add((frm, to))

        return {
            "availability": "source_reported",
            "provider": "jira",
            "object_type": "issue",
            "source_state": state,
            "source_state_reported": True,
            "verified_at": observed_at,
            "verification_method": "explicit_opt_in_authenticated_jira_issue_status_read",
            # Jira can verify its own issue resolution, never a wider
            # customer/business result. The generic boolean must stay false
            # so agent clients do not treat ticket closure as customer success.
            "verified_business_outcome": False,
            "verified_operational_outcome": {
                "kind": "jira_issue_resolved",
                "status": "resolved" if verified_resolution else "unverified",
                "verified": verified_resolution,
                "source_timestamp": resolution_date if verified_resolution else None,
                "scope": "jira_issue_only_not_customer_acknowledgement_or_refund",
            },
            "review_audit_events": audit,
            "audit_available": audit_available,
            "audit_complete": audit_available and not audit_truncated,
            "audit_observation_count": audit_count,
            "assignment_transitions_verified": sum(
                1 for row in audit if row["action"] == "assignee_transition"
            ),
            "verified_human_handoffs": 0,
            "observed_structural_status_reversals": reversed_transitions,
            "status_reversal_means_business_failure": False,
            "missing_status_reversals_means_no_rework": False,
            "comments_and_values_included": False,
            "native_issue_ids_included": False,
            "not_a_claim_of_business_success": True,
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError, json.JSONDecodeError):
        return _unknown("jira_source_read_failed")


__all__ = ["jira_issue_receipt"]
