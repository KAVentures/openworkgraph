"""Offline ServiceNow incident page staging; no network, credentials or storage.

Caller must separately authorize a selected-record read and validate the grant
again before committing any later event-log append. Provider responses are not
proof of completeness, identity authority or customer outcomes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

MAX_BYTES = 262_144
MAX_PAGE = 100
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4096
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.service-now\.com\Z")
_UPDATED = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\Z")


class StagingError(ValueError):
    """Fail closed; messages never echo provider values."""


@dataclass(frozen=True)
class SelectedIncidents:
    host: str
    tenant_scope: str
    sys_ids: tuple[str, ...]
    consented: bool = False
    grant_current: bool = False

    def __post_init__(self) -> None:
        if type(self.host) is not str or not _HOST.fullmatch(self.host):
            raise StagingError("Unsupported ServiceNow instance host")
        if type(self.tenant_scope) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", self.tenant_scope):
            raise StagingError("Invalid local tenant scope")
        if type(self.sys_ids) is not tuple or not 1 <= len(self.sys_ids) <= 8:
            raise StagingError("Select one to eight exact incident IDs")
        if any(type(x) is not str or not _ID.fullmatch(x) for x in self.sys_ids):
            raise StagingError("Invalid incident ID")
        if len(set(self.sys_ids)) != len(self.sys_ids):
            raise StagingError("Duplicate selected incident ID")
        if type(self.consented) is not bool or type(self.grant_current) is not bool:
            raise StagingError("Invalid consent or grant state")

    @property
    def namespace(self) -> str:
        scope = f"{self.host}\0{self.tenant_scope}\0incident".encode("ascii")
        return "snow." + hashlib.sha256(scope).hexdigest()[:24]

    def require_authority(self) -> None:
        if not self.consented or not self.grant_current:
            raise StagingError("Selected-record read is not currently authorized")


def _require_selected_scope(scope: SelectedIncidents) -> None:
    """Enforce the exact validated authority type before any source operation.

    Duck-typed objects and subclasses may override require_authority and
    fabricate host, namespace or consent. They cannot be trusted as grants.
    """
    if type(scope) is not SelectedIncidents:
        raise StagingError("Invalid selected incident scope")
    scope.require_authority()


def _validate_updated(value: str) -> None:
    """Require a real provider-local calendar timestamp, without claiming UTC."""
    if type(value) is not str or not _UPDATED.fullmatch(value):
        raise StagingError("Invalid provider-local update timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise StagingError("Invalid provider-local update timestamp") from exc


def plan_page(scope: SelectedIncidents, *, offset: int = 0, limit: int = 50,
              since: str | None = None) -> dict[str, Any]:
    """Construct a fixed-host GET plan; never follows provider-supplied links."""
    _require_selected_scope(scope)
    if type(offset) is not int or not 0 <= offset <= 10_000:
        raise StagingError("Page offset outside bounded range")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise StagingError("Page size outside bounded range")
    if since is not None:
        _validate_updated(since)
    query = "sys_idIN" + ",".join(sorted(scope.sys_ids))
    if since is not None:
        query += "^sys_updated_on>=" + since
    query += "^ORDERBYsys_updated_on^ORDERBYsys_id"
    params = {
        "sysparm_query": query,
        "sysparm_fields": "sys_id,sys_updated_on,sys_mod_count,state",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": str(limit),
        "sysparm_offset": str(offset),
    }
    return {"method": "GET", "url": f"https://{scope.host}/api/now/table/incident?{urlencode(params)}",
            "source_namespace": scope.namespace, "selected_count": len(scope.sys_ids),
            "offset": offset, "limit": limit, "since": since,
            "historical_completeness": "unknown",
            "provider_timestamp_timezone_verified": False}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StagingError("Duplicate JSON keys")
        out[key] = value
    return out


def _finite_json_float(value: str) -> float:
    """Refuse numeric JSON overflow, including fields later discarded."""
    number = float(value)
    if not math.isfinite(number):
        raise StagingError("Nonfinite JSON")
    return number


def _validate_json_tree(body: Any) -> None:
    """Bound discarded fields too; JSON escapes must decode to Unicode scalars.

    The byte cap alone does not bound the number of tiny nested values, and
    Python's JSON decoder permits isolated escaped UTF-16 surrogates. Neither
    should be silently accepted just because the field is later discarded.
    """
    pending: list[tuple[Any, int]] = [(body, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise StagingError("Provider JSON exceeds structural bounds")
        if type(value) is str:
            if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise StagingError("Invalid provider JSON Unicode scalar")
        elif type(value) is dict:
            # Count keys as well as values, including discarded private fields.
            if visited + len(pending) + 2 * len(value) > MAX_JSON_NODES:
                raise StagingError("Provider JSON exceeds structural bounds")
            for key, child in value.items():
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        elif type(value) is list:
            if visited + len(pending) + len(value) > MAX_JSON_NODES:
                raise StagingError("Provider JSON exceeds structural bounds")
            pending.extend((child, depth + 1) for child in value)


def project_page(scope: SelectedIncidents, raw: bytes, *, limit: int = 50,
                 since: str | None = None,
                 _require_planned_order: bool = False) -> dict[str, Any]:
    """Validate one fake/provider page; return minimized staged source snapshots.

    When plan_page used a since bound, the caller must supply that same bound
    here: a provider may ignore a filter. Comparison is provider-local only.
    No persisted cursor is advanced here. A future caller must recheck scope,
    consent, lease/revision and source identity atomically with any append.
    """
    _require_selected_scope(scope)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise StagingError("Page size outside bounded range")
    if since is not None:
        _validate_updated(since)
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise StagingError("Source response exceeds bounded byte limit")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_float=_finite_json_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(StagingError("Nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StagingError("Invalid bounded provider JSON") from exc
    _validate_json_tree(body)
    # A ServiceNow Table API read has one result array. Never silently stage
    # a mixed success/error envelope or provider-supplied pagination metadata:
    # an HTTP status/ACL failure must not masquerade as an empty successful page.
    if (type(body) is not dict or set(body) != {"result"}
            or type(body["result"]) is not list or len(body["result"]) > limit):
        raise StagingError("Invalid provider result envelope or page size")
    selected = set(scope.sys_ids)
    seen: dict[str, dict[str, Any]] = {}
    last_sort_key: tuple[str, str] | None = None
    for item in body["result"]:
        if type(item) is not dict:
            raise StagingError("Invalid incident record")
        sys_id = item.get("sys_id")
        if type(sys_id) is not str or not _ID.fullmatch(sys_id) or sys_id not in selected:
            raise StagingError("Provider returned an unselected or invalid incident")
        updated = item.get("sys_updated_on")
        _validate_updated(updated)
        # Fixed-width, calendar-validated provider-local timestamps compare in
        # chronological order within the same unverified provider clock.
        # Refuse a selected row if ServiceNow ignored the requested filter.
        if since is not None and updated < since:
            raise StagingError("Provider returned incident older than requested watermark")
        mod = item.get("sys_mod_count")
        if type(mod) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})", mod):
            raise StagingError("Invalid provider modification counter")
        state = item.get("state")
        if type(state) is not str or not 1 <= len(state) <= 64 or any(ord(c) < 32 or ord(c) > 126 for c in state):
            raise StagingError("Invalid incident state")
        record = {"source_namespace": scope.namespace, "source_case_id": sys_id,
                  "source_event_id": f"{sys_id}:{mod}", "reported_state": state,
                  "reported_updated_on": updated, "timestamp_timezone_verified": False,
                  "source_status_only": True, "business_outcome_verified": False}
        # A planned offset read requires ascending ORDERBY updated_on, sys_id.
        # Do not normalize away evidence that the provider ignored that order.
        # Equal keys (including exact duplicate rows) remain valid.
        if _require_planned_order:
            sort_key = (updated, sys_id)
            if last_sort_key is not None and sort_key < last_sort_key:
                raise StagingError("Provider page out of planned order")
            last_sort_key = sort_key
        prior = seen.get(sys_id)
        if prior is not None and prior != record:
            raise StagingError("Conflicting incident revision within page")
        seen[sys_id] = record
    return {"records": [seen[k] for k in sorted(seen)],
            "unobserved_selected_ids": sorted(selected - seen.keys()),
            "returned_rows": len(body["result"]), "distinct_selected_rows": len(seen),
            "may_have_more": len(body["result"]) == limit,
            "historical_completeness": "unknown", "checkpoint_advanced": False,
            "provider_timestamp_timezone_verified": False,
            "not_an_authorization_to_append": True}


def project_planned_page(scope: SelectedIncidents, raw: bytes, *,
                         plan: dict[str, Any]) -> dict[str, Any]:
    """Stage a response against the *same* selected-record GET plan.

    This prevents an accidental omission/change of the incremental watermark
    between request construction and response projection. The plan is local
    metadata, not a provider receipt, signed grant, or permission to append.
    The caller must separately enforce transport identity and atomic revocation
    fencing before any later persistent operation.
    """
    _require_selected_scope(scope)
    if type(plan) is not dict:
        raise StagingError("Missing or invalid selected-record page plan")
    # Reconstruct from the current exact scope rather than trusting a copied
    # URL, namespace, selected-count, limit, offset or watermark in isolation.
    # Missing/extra fields and tampered query parameters fail closed.
    expected = plan_page(scope, offset=plan.get("offset"),
                         limit=plan.get("limit"), since=plan.get("since"))
    # Python considers True == 1 and 0 == False. A plan's source and
    # provenance metadata is a typed contract, not just a value comparison:
    # a caller must not substitute booleans/floats for selected counts or
    # source-verification flags and still claim an exact plan match.
    if (plan != expected or any(
            type(plan[key]) is not type(expected[key]) for key in expected
    )):
        raise StagingError("Selected-record page plan does not match current scope")
    result = project_page(scope, raw, limit=expected["limit"],
                          since=expected["since"], _require_planned_order=True)
    return {**result, "plan_bound": True, "planned_offset": expected["offset"],
            "planned_since": expected["since"], "page_order_matches_plan": True}

def project_planned_pages(scope: SelectedIncidents, pages: list[tuple[dict[str, Any], bytes]]) -> dict[str, Any]:
    """Check a bounded consecutive run of planned pages without claiming completeness.

    No network or append occurs. A cross-page duplicate is rejected even when
    identical: an offset shift can hide a different incident. A future caller
    must independently fence source revocation and validate transport identity.
    """
    _require_selected_scope(scope)
    if type(pages) is not list or not 1 <= len(pages) <= 8:
        raise StagingError("Select one to eight consecutive page responses")
    first_limit: int | None = None
    first_since: str | None = None
    previous_last: tuple[str, str] | None = None
    seen: dict[str, dict[str, Any]] = {}
    returned_rows = 0
    last_page: dict[str, Any] | None = None
    for index, entry in enumerate(pages):
        if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not dict:
            raise StagingError("Invalid planned page entry")
        plan, raw = entry
        if index == 0:
            first_limit = plan.get("limit")
            if type(first_limit) is not int or not 1 <= first_limit <= MAX_PAGE:
                raise StagingError("Invalid planned page size")
            first_since = plan.get("since")
        if (plan.get("limit") != first_limit or plan.get("since") != first_since
                or plan.get("offset") != index * first_limit):
            raise StagingError("Nonconsecutive or inconsistent planned pages")
        staged = project_planned_page(scope, raw, plan=plan)
        # Even identical duplicates consume offset slots. A later offset can
        # therefore skip another selected incident while appearing consecutive.
        # Standalone single-page projection retains its legacy dedupe contract.
        if staged["distinct_selected_rows"] != staged["returned_rows"]:
            raise StagingError("Duplicate incident row within planned offset page")
        if index < len(pages) - 1 and staged["returned_rows"] != first_limit:
            raise StagingError("Short page before another planned offset")
        keys = []
        for record in staged["records"]:
            incident_id = record["source_case_id"]
            if incident_id in seen:
                raise StagingError("Incident repeated across planned pages")
            seen[incident_id] = record
            keys.append((record["reported_updated_on"], incident_id))
        if keys:
            if previous_last is not None and min(keys) < previous_last:
                raise StagingError("Provider page order regressed across offsets")
            previous_last = max(keys)
        returned_rows += staged["returned_rows"]
        last_page = staged
    return {"records": [seen[k] for k in sorted(seen)],
            "unobserved_selected_ids": sorted(set(scope.sys_ids) - seen.keys()),
            "returned_rows": returned_rows, "distinct_selected_rows": len(seen),
            "pages_checked": len(pages), "plan_sequence_checked": True,
            "may_have_more": last_page["may_have_more"],
            "historical_completeness": "unknown", "checkpoint_advanced": False,
            "provider_timestamp_timezone_verified": False,
            "not_an_authorization_to_append": True}


def reconcile_replayed_pages(scope: SelectedIncidents, earlier_raw: bytes,
                             later_raw: bytes, *, limit: int = 50,
                             since: str | None = None) -> dict[str, Any]:
    """Compare two independently staged selected-incident reads, without writes.

    Only identical source IDs within the exact selected scope are compared.
    Equal reported revision counters must carry identical minimized status and
    timestamp evidence; a lower counter is a conflict, not a deletion/replay.
    Missing records are *unknown* (ACL, pagination, deletion, concurrent edits).
    This cannot establish provider identity, completeness or checkpoint safety.
    """
    _require_selected_scope(scope)
    earlier = project_page(scope, earlier_raw, limit=limit, since=since)
    later = project_page(scope, later_raw, limit=limit, since=since)
    return _reconcile_projected(scope, earlier, later)


def _reconcile_projected(scope: SelectedIncidents, earlier: dict[str, Any],
                         later: dict[str, Any]) -> dict[str, Any]:
    """Compare strictly projected selected-source identities and revisions."""
    before = {r["source_case_id"]: r for r in earlier["records"]}
    after = {r["source_case_id"]: r for r in later["records"]}
    same: list[str] = []
    higher: list[str] = []
    timestamp_regression_unknown: list[str] = []
    for incident_id in sorted(before.keys() & after.keys()):
        old = before[incident_id]
        new = after[incident_id]
        old_revision = int(old["source_event_id"].rsplit(":", 1)[1])
        new_revision = int(new["source_event_id"].rsplit(":", 1)[1])
        if new_revision < old_revision:
            raise StagingError("Provider revision counter regressed across selected replay")
        if new_revision == old_revision:
            if (new["reported_state"] != old["reported_state"]
                    or new["reported_updated_on"] != old["reported_updated_on"]):
                raise StagingError("Conflicting evidence for same provider revision")
            same.append(incident_id)
        elif new["reported_updated_on"] < old["reported_updated_on"]:
            # An increasing source revision with an earlier provider-local time
            # is contradictory source evidence, not a clean observed advance.
            # Clock/timezone truth is unknown, so withhold rather than infer
            # rollback, deletion, or a verified chronological update.
            timestamp_regression_unknown.append(incident_id)
        else:
            # Equal-second updates are possible. A higher counter is only
            # source-reported, not verified chronology or completion.
            higher.append(incident_id)
    return {
        "source_namespace": scope.namespace,
        "same_reported_revision_ids": same,
        "higher_reported_revision_ids": higher,
        "timestamp_regression_unknown_ids": timestamp_regression_unknown,
        "newly_observed_ids": sorted(after.keys() - before.keys()),
        "missing_unknown_ids": sorted(before.keys() - after.keys()),
        # Selected records absent from both pages are still unknown, not
        # evidence of a successful empty or complete provider read.
        "unobserved_in_either_read_ids": sorted(
            set(scope.sys_ids) - (before.keys() | after.keys())),
        "replay_consistency_checked": True,
        "historical_completeness": "unknown",
        "missing_means_deleted": False,
        "provider_timestamp_timezone_verified": False,
        "business_outcome_verified": False,
        "checkpoint_advanced": False,
        "not_an_authorization_to_append": True,
    }



def reconcile_planned_replays(
    scope: SelectedIncidents,
    earlier_pages: list[tuple[dict[str, Any], bytes]],
    later_pages: list[tuple[dict[str, Any], bytes]],
) -> dict[str, Any]:
    """Compare two bounded offset batches under the exact same GET plans.

    Offline consistency only: not authenticated transport, stable snapshots,
    a persistence-time revocation fence, or checkpoint authorization.
    """
    _require_selected_scope(scope)
    if (type(earlier_pages) is not list or type(later_pages) is not list
            or not 1 <= len(earlier_pages) == len(later_pages) <= 8):
        raise StagingError("Replay requires matching bounded page sequences")
    for entries in (earlier_pages, later_pages):
        if any(type(entry) is not tuple or len(entry) != 2
               or type(entry[0]) is not dict for entry in entries):
            raise StagingError("Invalid planned replay entry")
    if [entry[0] for entry in earlier_pages] != [entry[0] for entry in later_pages]:
        raise StagingError("Replay reads used different selected-record plans")
    earlier = project_planned_pages(scope, earlier_pages)
    later = project_planned_pages(scope, later_pages)
    return {**_reconcile_projected(scope, earlier, later),
            "same_plans_checked": True, "plan_sequence_checked": True,
            "pages_per_read": len(earlier_pages)}
