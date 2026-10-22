from __future__ import annotations

"""First vertical slice of a record graph: canonical UI evidence + live GitHub audit.

No new capture, storage, background polling, third-party credentials or broad
system-log ingestion. Reads a source only after an explicit request, and only
with existing opt-in GitHub outcome tracking enabled.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import hashlib
import hmac
import secrets
from typing import Any, Callable

from .local_reference_lookup import resolve_resource_reference
from .db import rows as _db_rows
from .workflow_evidence import WorkflowEvidenceError, _ts
from . import outcome_tracker
from .gh_cli import find_gh_cli
from .jira_source_evidence import jira_issue_receipt
from .stripe_source_evidence import stripe_record_receipt

_RESOURCE = re.compile(r"^owg:r:[0-9a-f]{32}$")
_GITHUB_PR = re.compile(r"^([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,9})$")
_ALLOWED_REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"})
_ALLOWED_PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})
_MAX_SOURCE_ROWS = 25_000
_MAX_TIMELINE = 150
_MAX_REVIEWS = 40
_AUDIT_SECRET = secrets.token_bytes(32)


def _audit_ref(resource: str, identity: str) -> str:
    return 'source_event:' + hmac.new(_AUDIT_SECRET, (resource+'|'+identity).encode(), hashlib.sha256).hexdigest()[:32]


def _source_timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value)>64:
        return ''
    try:
        at=datetime.fromisoformat(value.replace('Z','+00:00'))
        return at.astimezone(timezone.utc).isoformat() if at.tzinfo else ''
    except (ValueError,OverflowError):
        return ''


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_unknown(reason: str) -> dict[str, Any]:
    return {
        "availability": "unknown",
        "reason": reason,
        "verified_business_outcome": False,
        "source_state_reported": False,
        "not_a_claim_of_business_success": True,
    }


def _gh_pr_snapshot(
    ref: str, *, runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = find_gh_cli,
) -> dict[str, Any]:
    """Read-only GitHub source status and review audit for a locally observed PR.

    The local resolver is *not* an allowlist by itself: callers first prove
    the same reference was observed in canonical retained OWG evidence.
    Never echo locator, repo, account, review author or comments in output.
    """
    if not outcome_tracker.enabled():
        return _source_unknown("existing_github_outcome_tracking_opt_in_off")
    info = resolve_resource_reference(ref)
    if not isinstance(info, dict):
        return _source_unknown("approved_local_record_locator_unavailable")
    if info.get("provider") != "github" or info.get("resource_kind") != "pull_request" or info.get("host") != "github.com":
        return _source_unknown("unsupported_source_type")
    locator = str(info.get("resolver_locator") or "")
    matched = _GITHUB_PR.fullmatch(locator)
    if matched is None:
        return _source_unknown("invalid_stored_github_pull_request_locator")
    # Reject dot-paths and poorly formed GitHub owner/repo names.
    if any(part in {".", ".."} or part.startswith("-") or part.endswith(".")
           for part in matched.groups()[:2]):
        return _source_unknown("invalid_stored_github_pull_request_locator")
    gh = which("gh")
    if not gh:
        return _source_unknown("github_cli_not_installed")

    # No shell and no caller-controlled URL/host. No writes; bounded source read.
    url = "https://github.com/" + locator
    try:
        result = runner(
            [gh, "pr", "view", url, "--json", "state,mergedAt,closedAt,statusCheckRollup,reviews"],
            capture_output=True, text=True, timeout=12,
        )
        if result.returncode != 0 or len(result.stdout or "") > 150_000:
            return _source_unknown("github_cli_read_failed")
        report = json.loads(result.stdout or "{}")
        if not isinstance(report, dict):
            return _source_unknown("invalid_github_response")
    except (OSError, ValueError, subprocess.SubprocessError):
        return _source_unknown("github_cli_read_failed")

    state = str(report.get("state") or "").upper()
    if state not in _ALLOWED_PR_STATES:
        return _source_unknown("unknown_github_pr_state")
    if report.get('mergedAt') not in (None,'') and not _source_timestamp(report.get('mergedAt')):
        return _source_unknown('invalid_github_merge_timestamp')
    merged = bool(report.get("mergedAt")) or state == "MERGED"
    if merged and state not in {"CLOSED", "MERGED"}:
        return _source_unknown("conflicting_github_pr_state")
    # Closed is not necessarily merged, and merged isn't evidence of customer
    # acceptance, refunds, payment, deployment, or work quality.
    status = "merged" if merged else "closed_unmerged" if state == "CLOSED" else "open"
    reviews = report.get("reviews") if isinstance(report.get("reviews"), list) else []
    safe_reviews = []
    for item in reviews[:_MAX_REVIEWS]:
        if not isinstance(item, dict):
            continue
        review_state = str(item.get("state") or "").upper()
        date = str(item.get("submittedAt") or "")
        if review_state not in _ALLOWED_REVIEW_STATES:
            continue
        try:
            parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                continue
        except (ValueError, OverflowError):
            continue
        safe_reviews.append({
            "action": "github_pull_request_review",
            "state": review_state.lower(),
            "observed_at": parsed.astimezone(timezone.utc).isoformat(),
            "actor_identity_withheld": True,
            "review_text_withheld": True,
        })
    safe_reviews.sort(key=lambda x: x["observed_at"])
    review_projection, conflicting_reviews = {}, set()
    for item in reviews[:_MAX_REVIEWS]:
        if not isinstance(item,dict):
            continue
        identifier=item.get('id'); stamp=_source_timestamp(item.get('submittedAt'))
        decision=item.get('state')
        # Dismissal changes a review's current state without providing the
        # dismissal timestamp here; pending reviews have no submission event.
        # Never invent either action at the original submission timestamp.
        if (not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_+=/-]{1,160}',identifier)
                or identifier in conflicting_reviews):
            continue
        if not stamp or not isinstance(decision,str) or decision not in {'APPROVED','CHANGES_REQUESTED','COMMENTED'}:
            review_projection.pop(identifier,None);conflicting_reviews.add(identifier)
            continue
        projected={'action':{'APPROVED':'review_approved','CHANGES_REQUESTED':'review_changes_requested',
            'COMMENTED':'review_commented'}[decision], 'observed_at':stamp,
            'source_event_ref':_audit_ref(ref,'review:'+identifier)}
        if identifier in review_projection and review_projection[identifier]!=projected:
            review_projection.pop(identifier);conflicting_reviews.add(identifier)
        else:
            review_projection[identifier]=projected
    process_events=list(review_projection.values())
    terminal_stamp=_source_timestamp(report.get('mergedAt') if merged else report.get('closedAt') if state=='CLOSED' else None)
    if terminal_stamp:
        action='pull_request_merged' if merged else 'pull_request_closed_unmerged'
        process_events.append({'action':action,'observed_at':terminal_stamp,
                               'source_event_ref':_audit_ref(ref,action+':'+terminal_stamp)})
    process_events.sort(key=lambda row:row['observed_at'])
    return {
        "availability": "source_reported",
        "provider": "github",
        "object_type": "pull_request",
        "source_state": status,
        "verification_method": "explicit_opt_in_authenticated_local_github_cli_read",
        "verified_at": _checked_at(),
        "review_audit_events": safe_reviews,
        "review_audit_events_truncated": len(reviews) > _MAX_REVIEWS,
        'process_audit_events':process_events,
        'process_audit_complete':False,
        'process_audit_scope':'bounded_review_submissions_and_current_lifecycle_timestamps',
        'process_audit_event_refs_ephemeral':True,
        'process_audit_is_complete_case_history':False,
        "review_authors_included": False,
        "comments_and_values_included": False,
        "verified_business_outcome": False,
        "source_state_reported": True,
        "not_a_claim_of_business_success": True,
    }


def _record_rows(
    resource_ref: str, *, since: str | None, until: str | None, limit: int,
) -> list[dict[str, Any]]:
    """Read only this record's retained events, not the most recent N *other* events.

    Both the substring prefilter and exact JSON-path equality are necessary:
    the first avoids decoding unrelated large metadata blobs, while the latter
    rules out matches in free-text fields or a different JSON key. No new index,
    migration, capture, storage, or additional source-system permission.
    """
    for name, value in (("since", since), ("until", until)):
        if value and _ts(value) is None:
            raise WorkflowEvidenceError(f"{name} must be an ISO-8601 timestamp")
    if since and until and _ts(since) >= _ts(until):
        raise WorkflowEvidenceError("until must be after since")

    conditions = [
        "metadata_json LIKE ?",
        "lower(CASE WHEN json_valid(metadata_json) "
        "THEN json_extract(metadata_json, '$.resource_reference.resource_ref') "
        "ELSE NULL END) = ?",
    ]
    values: list[Any] = [f"%{resource_ref}%", resource_ref]
    if since:
        conditions.append("julianday(observed_at) >= julianday(?)")
        values.append(since)
    if until:
        conditions.append("julianday(observed_at) < julianday(?)")
        values.append(until)
    # One extra matching event distinguishes exactly-at-limit from truncation.
    # This does not manufacture an authoritative record identity from a subset.
    query = (
        "SELECT event_id, observed_at, session_id, actor_id, event_type, "
        "source, metadata_json FROM events WHERE "
        + " AND ".join(conditions)
        + " ORDER BY julianday(observed_at) DESC, event_id DESC LIMIT ?"
    )
    return _db_rows(query, tuple(values + [limit + 1]))


def record_graph(
    resource_ref: str, *, since: str | None = None, until: str | None = None,
    include_source_status: bool = False, source_event_limit: int = _MAX_SOURCE_ROWS,
    include_source_lifecycle: bool = False,
    _raw_events: list[dict[str, Any]] | None = None,
    source_reader: Callable[[str], dict[str, Any]] | None = None,
    required_source_type: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    """Read the per-record OWG evidence timeline; optionally join live source data.

    Interleaved events cannot contaminate this record because only an exact
    normalized resource ref matches. Shared actor presence is not a handoff.
    A missing ref is not a licence to inspect someone else's source system.
    """
    if not _RESOURCE.fullmatch(str(resource_ref or "")):
        raise ValueError("resource_ref must be a normalized opaque owg:r: reference")
    max_rows = max(1, min(int(source_event_limit), _MAX_SOURCE_ROWS))
    raw = (_record_rows(resource_ref, since=since, until=until, limit=max_rows)
           if _raw_events is None else _raw_events[:max_rows + 1])
    truncated = len(raw) > max_rows
    raw = raw[:max_rows]
    matched: list[dict[str, Any]] = []
    identity: tuple[str, str, str] | None = None
    conflicts = 0
    for event in raw:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        reference = metadata.get("resource_reference") if isinstance(metadata.get("resource_reference"), dict) else {}
        if str(reference.get("resource_ref") or "").lower() != resource_ref:
            continue
        candidate = (str(reference.get("provider") or ""), str(reference.get("resource_kind") or ""),
                     str(reference.get("host") or ""))
        if not all(candidate):
            conflicts += 1
            continue
        if identity is None:
            identity = candidate
        if candidate != identity:
            conflicts += 1
            continue
        matched.append(event)

    if conflicts:
        # Do not query another service when conflicting source types claim the
        # same pseudonym; validation failures must not widen access.
        matched = []
        identity = None

    matched.sort(key=lambda row: (str(row.get("observed_at") or ""), str(row.get("event_id") or "")))
    def structural_label(value: Any, *, limit: int) -> str:
        # A schema field normally contains a bounded identifier, but a legacy
        # or corrupted row must not leak free-text data via an action/source.
        label = str(value or "").strip().lower()
        return label if len(label) <= limit and re.fullmatch(r"[a-z0-9_:-]+", label) else "unknown"

    timeline = [{
        "event_id": str(row.get("event_id") or "")[:120]
            if re.fullmatch(r"[A-Za-z0-9:._-]{1,120}", str(row.get("event_id") or "")) else "",
        "observed_at": str(row.get("observed_at") or "")[:48],
        "observed_action_type": structural_label(row.get("event_type"), limit=60),
        "source": structural_label(row.get("source"), limit=40),
    } for row in matched[:_MAX_TIMELINE]]
    source = _source_unknown("source_refresh_not_requested")
    if include_source_status:
        if required_source_type is not None and identity != required_source_type:
            source = _source_unknown('selected_source_type_not_observed')
        elif truncated:
            # A hidden older observation could contradict the apparent record
            # identity. Refuse to authorize a provider read from partial proof.
            source = _source_unknown("record_evidence_window_truncated")
        elif not matched:
            source = _source_unknown("record_not_observed_in_permitted_evidence_window")
        elif identity == ("github", "pull_request", "github.com"):
            source = (source_reader or _gh_pr_snapshot)(resource_ref)
        elif identity and identity[0] == "jira" and identity[1] == "issue" and identity[2].endswith(".atlassian.net"):
            # A Jira status read requires a second, provider-specific opt-in,
            # and a strict match against the configured tenant. The source
            # client never broadens this permission via API parameters.
            source = (source_reader or jira_issue_receipt)(resource_ref)
        elif identity and identity[0] == "stripe" and identity[1] in {"invoice", "refund"} and identity[2] == "dashboard.stripe.com":
            adapter=source_reader or stripe_record_receipt
            source = adapter(resource_ref,include_lifecycle=True) if include_source_lifecycle is True and identity[1]=='invoice' else adapter(resource_ref)
        else:
            source = _source_unknown("no_approved_source_adapter_for_record_type")
    return {
        "format": "openworkgraph.record-graph.v1",
        "resource_ref": resource_ref,
        "record_observed": bool(matched),
        "record_type": {"provider": identity[0], "kind": identity[1]} if identity else None,
        "observed_event_count": len(matched),
        "observed_session_count": len({str(row.get("session_id")) for row in matched if row.get("session_id")}),
        "observed_actor_count": len({str(row.get("actor_id")) for row in matched if row.get("actor_id")}),
        "timeline": timeline,
        "timeline_truncated": len(matched) > _MAX_TIMELINE,
        "source_evidence": source,
        "source_event_limit_reached": truncated,
        "source_rows_scoped_to_exact_record": _raw_events is None,
        "record_identity_complete_within_retained_window": not truncated and not conflicts,
        "source_window": {"since": since, "until": until},
        "observed_same_record_not_verified_handoff": True,
        "source_log_join_supported": bool(identity and not truncated and not conflicts and (identity == ("github", "pull_request", "github.com") or
            (identity[0] == "jira" and identity[1] == "issue" and identity[2].endswith(".atlassian.net")) or
            (identity[0] == "stripe" and identity[1] in {"invoice", "refund"} and identity[2] == "dashboard.stripe.com"))),
        "verified_customer_outcome": False,
        "no_event_means_not_observed_not_no_work": True,
        "structural_evidence_only": True,
        "read_only": True,
        "canonical_events_not_modified": True,
    }


__all__ = ["record_graph"]
