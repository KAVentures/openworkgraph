"""Selected Zendesk ticket audit ingestion using the retained enterprise engine.

Full bounded per-ticket audit scans, repeated minimized evidence agreement,
exact selected case scope and independently checked OAuth principal. No
ticket creation/update, comment retention or account-wide audit enumeration.
"""
from datetime import timedelta
import hmac
import re
import time
from urllib.parse import urlsplit, parse_qs

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import connector_oauth as oauth
from . import enterprise_rest_feed as rest

HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.zendesk\.com\Z")
ID = re.compile(r"[1-9][0-9]{0,17}\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
STATUSES = frozenset({"new", "open", "pending", "hold", "solved", "closed"})
PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
TYPES = frozenset({"problem", "incident", "question", "task"})
FIELDS = frozenset({"status", "priority", "type", "assignee_id", "group_id"})
MAX_TICKETS = 4
MAX_AUDITS_PER_TICKET = 500
MAX_CHILD_EVENTS = 20000


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


def identity(value, code):
    require(type(value) is int and ID.fullmatch(str(value)) is not None, code)
    return str(value)


class Create(rest.Strict):
    name: str = Field(min_length=1, max_length=100)
    connection_id: str = Field(pattern=r"^conn_[a-f0-9]{16}$")
    instance_host: str = Field(max_length=80)
    principal_id: str = Field(pattern=r"^[1-9][0-9]{0,17}$")
    ticket_ids: list[str] = Field(min_length=1, max_length=MAX_TICKETS)
    fields: list[str] = Field(min_length=1, max_length=5)
    initial_since: str = Field(min_length=20, max_length=40)
    window_hours: StrictInt = Field(ge=1, le=168, default=24)
    overlap_minutes: StrictInt = Field(ge=0, le=1440, default=60)
    ingestion_lag_minutes: StrictInt = Field(ge=0, le=1440, default=15)
    confirm_configuration: StrictBool
    confirm_transient_audit_payloads: StrictBool
    confirm_metadata_retention: StrictBool

    @model_validator(mode="after")
    def reviewed(self):
        if (not HOST.fullmatch(self.instance_host)
                or any(not ID.fullmatch(v) for v in self.ticket_ids)
                or len(set(self.ticket_ids)) != len(self.ticket_ids)
                or not set(self.fields) <= FIELDS or len(set(self.fields)) != len(self.fields)
                or self.overlap_minutes >= self.window_hours * 60):
            raise ValueError("fixed tenant, unique selected tickets and approved fields required")
        return self


def grant(connection_id, host):
    require(isinstance(host, str) and HOST.fullmatch(host), "invalid_zendesk_instance", 422)
    try:
        conn = next((c for c in oauth.status()["connections"] if c.get("connection_id") == connection_id), None)
        actual = oauth.api_host(connection_id)
    except Exception:
        raise rest.FeedError("zendesk_grant_unavailable") from None
    require(conn is not None and conn.get("provider") == "zendesk"
            and conn.get("host") == host and actual == host, "zendesk_grant_unavailable", 409)
    require(set(conn.get("scopes", [])) == {"read"}, "zendesk_grant_scope_mismatch", 409)
    require(not conn.get("reauthorization_required") and conn.get("has_refresh_token") is True,
            "zendesk_reconnect_required", 409)
    return "oauth_connection:" + connection_id + ":" + host


def check_binding(p):
    cfg = p["config"]
    binding = grant(cfg["connection_id"], cfg["instance_host"])
    require(hmac.compare_digest(rest._key(p, "credential", binding), p["credential_binding"]),
            "credential_changed_reconfigure_required", 409)
    return binding


def create(value):
    try:
        spec = Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_zendesk_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_transient_audit_payloads
            and spec.confirm_metadata_retention, "explicit_zendesk_scope_and_retention_required", 403)
    require(spec.name.strip() and all(32 <= ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF
                                    for c in spec.name), "invalid_profile_name", 422)
    binding = grant(spec.connection_id, spec.instance_host)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now-timedelta(days=365) <= since < now-timedelta(minutes=spec.ingestion_lag_minutes),
            "zendesk_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_transient_audit_payloads",
                                  "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since),
               ticket_ids=sorted(spec.ticket_ids), fields=sorted(spec.fields))
    return rest.create_profile(cfg, binding, now, provider="zendesk")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def label(field, value):
    if field in ("status", "priority", "type"):
        allowed = {"status": STATUSES, "priority": PRIORITIES, "type": TYPES}[field]
        if value is None and field != "status":
            return "zendesk." + field + ".cleared"
        require(isinstance(value, str) and value in allowed, "zendesk_unreviewed_field_value")
        return "zendesk." + field + "." + value
    require(field in ("assignee_id", "group_id"), "zendesk_unreviewed_field")
    if value is None or value == "":
        return "zendesk." + field + ".cleared"
    # Zendesk audit field values may be integers or decimal strings. Do not
    # retain the new/previous assigned identity, even in pseudonymous form.
    require((type(value) is int or isinstance(value, str)) and ID.fullmatch(str(value)),
            "zendesk_invalid_assignment_value")
    return "zendesk." + field + ".set"


def project(p, ticket, audit, since, until):
    require(isinstance(audit, dict), "zendesk_invalid_audit")
    aid = identity(audit.get("id"), "zendesk_invalid_audit_identity")
    require(identity(audit.get("ticket_id"), "zendesk_invalid_ticket_identity") == ticket,
            "zendesk_ticket_identity_mismatch")
    at = audit.get("created_at")
    require(isinstance(at, str) and TIMESTAMP.fullmatch(at), "zendesk_invalid_audit_timestamp")
    stamp = rest._stamp(at)
    children = audit.get("events")
    require(isinstance(children, list) and len(children) <= 1000, "zendesk_invalid_audit_events")
    rows, seen = [], set()
    for child in children:
        require(isinstance(child, dict), "zendesk_invalid_audit_event")
        cid = identity(child.get("id"), "zendesk_invalid_child_identity")
        require(cid not in seen, "zendesk_duplicate_child_identity")
        seen.add(cid)
        field = child.get("field_name")
        if (child.get("type") not in ("Create", "Change") or not isinstance(field, str)
                or field not in p["config"]["fields"] or not since <= stamp < until):
            continue
        require("value" in child, "zendesk_missing_field_value")
        actor = audit.get("author_id")
        resource = "" if actor is None else rest._key(p, "zendesk_actor",
                            identity(actor, "zendesk_invalid_author_identity"))
        rows.append([rest._key(p, "zendesk_event", ticket+":"+aid+":"+cid),
                     rest._key(p, "zendesk_case", ticket), label(field, child["value"]),
                     rest._iso(stamp), resource])
    return aid, rows, len(children)


class Reader:
    def __init__(self, p, client):
        self.p, self.client = p, client
        self.host = p["config"]["instance_host"]
        self.deadline = time.monotonic() + rest.MAX_SECONDS
        self.pages = self.bytes = self.children = 0

    def request(self, path, token, params=None):
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        rest._check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        try:
            with self.client.stream("GET", "https://"+self.host+path, params=params,
                                    headers={"Authorization": "Bearer "+token, "Accept": "application/json"},
                                    timeout=min(20, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited", 429, delay)
                require(response.status_code == 200, "zendesk_selected_source_unavailable")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes+len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        obj = rest._strict_json(bytes(raw))
        require(isinstance(obj, dict), "zendesk_invalid_response")
        return obj

    def principal(self, token):
        obj = self.request("/api/v2/users/me.json", token)
        user = obj.get("user")
        require(isinstance(user, dict) and identity(user.get("id"), "zendesk_invalid_principal")
                == self.p["config"]["principal_id"], "zendesk_principal_mismatch")
        require(user.get("active") is True and user.get("suspended") is not True
                and user.get("role") in ("agent", "admin"), "zendesk_principal_not_active_agent")

    def scan(self, ticket, token, since, until):
        path = "/api/v2/tickets/"+ticket+"/audits.json"
        rows, audits, events = [], set(), set()
        page, total = 1, None
        while True:
            obj = self.request(path, token, params={"page": page, "per_page": 100, "sort_order": "asc"})
            count, data = obj.get("count"), obj.get("audits")
            require(type(count) is int and 0 <= count <= MAX_AUDITS_PER_TICKET
                    and (total is None or count == total), "zendesk_invalid_audit_population")
            total = count
            require(isinstance(data, list) and len(data) <= 100 and "next_page" in obj
                    and len(audits)+len(data) <= total, "zendesk_invalid_audit_page")
            for item in data:
                aid, projected, children = project(self.p, ticket, item, since, until)
                require(aid not in audits, "zendesk_duplicate_audit_identity")
                audits.add(aid)
                self.children += children
                require(self.children <= MAX_CHILD_EVENTS, "zendesk_child_event_budget_exceeded")
                for row in projected:
                    require(row[0] not in events, "zendesk_duplicate_projected_event")
                    events.add(row[0]); rows.append(row)
                    require(len(rows) <= rest.MAX_ROWS, "provider_row_budget_exceeded")
            nxt = obj["next_page"]
            if nxt is None:
                break
            require(isinstance(nxt, str) and len(nxt) <= 2048 and data and len(audits) < total,
                    "zendesk_invalid_pagination")
            try:
                parts = urlsplit(nxt)
                require(parts.scheme == "https" and parts.hostname == self.host and parts.port in (None,443)
                        and not parts.username and not parts.password and not parts.fragment
                        and parts.path in (path, path.removesuffix(".json"))
                        and parse_qs(parts.query).get("page") == [str(page+1)], "zendesk_invalid_pagination")
            except ValueError:
                raise rest.FeedError("zendesk_invalid_pagination", 502) from None
            # Reconstruct the next request, never follow a provider URL.
            page += 1
        require(len(audits) == total, "zendesk_incomplete_audit_population")
        return sorted(rows, key=lambda r:r[0]), audits


def extract(p, since, until, client_factory=httpx.Client):
    rest._check(p)
    try:
        token = oauth.access_token(p["config"]["connection_id"])
    except Exception:
        raise rest.FeedError("zendesk_reconnect_required") from None
    require(isinstance(token, str) and 1 <= len(token) <= 8192
            and all(33 <= ord(c) <= 126 for c in token), "zendesk_invalid_token")
    rows, total_audits = [], 0
    with client_factory(timeout=20, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        reader.principal(token)
        for ticket in p["config"]["ticket_ids"]:
            first, a = reader.scan(ticket, token, since, until)
            second, b = reader.scan(ticket, token, since, until)
            require(first == second and a == b, "zendesk_repeat_scan_changed")
            rows.extend(first); total_audits += len(a)
            require(len(rows) <= rest.MAX_ROWS, "provider_row_budget_exceeded")
        reader.principal(token)
        rest._check(p)
        return rows, {"pages": reader.pages, "records_received": total_audits*2,
                      "distinct_events_in_window": len(rows), "duplicate_records_in_window": 0,
                      "principal_readback_verified": True, "repeat_scan_agreement": True,
                      "provider_completeness_verified": False, "selected_tickets": len(p["config"]["ticket_ids"]),
                      "raw_audit_payloads_retained": False}
