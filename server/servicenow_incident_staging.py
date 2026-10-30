"""Offline ServiceNow incident page staging; no network, credentials or storage.

Caller must separately authorize a selected-record read and validate the grant
again before committing any later event-log append. Provider responses are not
proof of completeness, identity authority or customer outcomes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

MAX_BYTES = 262_144
MAX_PAGE = 100
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


def plan_page(scope: SelectedIncidents, *, offset: int = 0, limit: int = 50,
              since: str | None = None) -> dict[str, Any]:
    """Construct a fixed-host GET plan; never follows provider-supplied links."""
    scope.require_authority()
    if type(offset) is not int or not 0 <= offset <= 10_000:
        raise StagingError("Page offset outside bounded range")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise StagingError("Page size outside bounded range")
    if since is not None and (type(since) is not str or not _UPDATED.fullmatch(since)):
        raise StagingError("Invalid provider-local update watermark")
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
            "offset": offset, "limit": limit, "historical_completeness": "unknown",
            "provider_timestamp_timezone_verified": False}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StagingError("Duplicate JSON keys")
        out[key] = value
    return out


def project_page(scope: SelectedIncidents, raw: bytes, *, limit: int = 50) -> dict[str, Any]:
    """Validate one fake/provider page; return minimized staged source snapshots.

    No persisted cursor is advanced here. A future caller must recheck scope,
    consent, lease/revision and source identity atomically with any append.
    """
    scope.require_authority()
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise StagingError("Page size outside bounded range")
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise StagingError("Source response exceeds bounded byte limit")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(StagingError("Nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StagingError("Invalid bounded provider JSON") from exc
    if type(body) is not dict or type(body.get("result")) is not list or len(body["result"]) > limit:
        raise StagingError("Invalid provider result envelope or page size")
    selected = set(scope.sys_ids)
    seen: dict[str, dict[str, Any]] = {}
    for item in body["result"]:
        if type(item) is not dict:
            raise StagingError("Invalid incident record")
        sys_id = item.get("sys_id")
        if type(sys_id) is not str or not _ID.fullmatch(sys_id) or sys_id not in selected:
            raise StagingError("Provider returned an unselected or invalid incident")
        updated = item.get("sys_updated_on")
        if type(updated) is not str or not _UPDATED.fullmatch(updated):
            raise StagingError("Invalid provider-local update timestamp")
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
        prior = seen.get(sys_id)
        if prior is not None and prior != record:
            raise StagingError("Conflicting incident revision within page")
        seen[sys_id] = record
    return {"records": [seen[k] for k in sorted(seen)],
            "returned_rows": len(body["result"]), "distinct_selected_rows": len(seen),
            "may_have_more": len(body["result"]) == limit,
            "historical_completeness": "unknown", "checkpoint_advanced": False,
            "provider_timestamp_timezone_verified": False,
            "not_an_authorization_to_append": True}
