"""Workday RaaS curated business-event and control-count reports.

Only deployment-approved tenant/report paths, exact window prompts and
flat reviewed aliases. Two matching population reads and bracketing count
reports detect many inconsistencies, not transactional snapshot completeness.
"""
from datetime import timedelta
import hmac
import os
import re
import time

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest

CATALOG = "OWG_WORKDAY_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
HOST = re.compile(r"wd[0-9]{1,3}(?:-impl)?(?:-services[0-9]{1,3})?\.(?:workday|myworkday)\.com\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,119}\Z")
COUNT = re.compile(r"(?:0|[1-9][0-9]{0,8})\Z")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
BASE = "/ccx/service/customreport2/"


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    host: str = Field(max_length=100)
    tenant: str
    report_owner: str
    event_report: str
    count_report: str
    integration_user: str
    credential_env: str = Field(pattern=r"^OWG_WORKDAY_PASSWORD_[A-Z0-9_]{1,48}$")
    since_prompt: str
    until_prompt: str
    event_field: str
    case_field: str
    activity_field: str
    timestamp_field: str
    resource_field: str | None = None
    count_field: str
    activity_labels: dict[str, str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reviewed(self):
        names = [self.tenant, self.report_owner, self.event_report,
                 self.count_report, self.integration_user]
        fields = [self.event_field, self.case_field, self.activity_field,
                  self.timestamp_field] + ([self.resource_field] if self.resource_field is not None else [])
        prompts = [self.since_prompt, self.until_prompt]
        if (not HOST.fullmatch(self.host) or any(not NAME.fullmatch(v) for v in names)
                or self.event_report == self.count_report
                or any(not FIELD.fullmatch(v) for v in fields + prompts + [self.count_field])
                or len(set(fields)) != len(fields) or len(set(prompts)) != 2
                or any(v.lower() == "format" for v in prompts)):
            raise ValueError("fixed distinct reports, aliases, prompts and tenant required")
        if (any(not LABEL.fullmatch(k) or not LABEL.fullmatch(v) for k, v in self.activity_labels.items())
                or len(set(self.activity_labels.values())) != len(self.activity_labels)):
            raise ValueError("distinct reviewed activity mapping required")
        return self


class Create(rest.Strict):
    name: str = Field(min_length=1, max_length=100)
    source_alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_since: str = Field(min_length=20, max_length=40)
    window_hours: StrictInt = Field(ge=1, le=168, default=24)
    overlap_minutes: StrictInt = Field(ge=0, le=1440, default=60)
    ingestion_lag_minutes: StrictInt = Field(ge=0, le=1440, default=15)
    confirm_configuration: StrictBool
    confirm_source_read: StrictBool
    confirm_metadata_retention: StrictBool

    @model_validator(mode="after")
    def reviewed(self):
        if self.overlap_minutes >= self.window_hours * 60:
            raise ValueError("overlap must be shorter than window")
        return self


def catalog():
    try:
        raw = os.environ.get(CATALOG, "{}")
        if len(raw) > 100000:
            raise ValueError()
        data = rest._strict_json(raw.encode())
        if not isinstance(data, dict) or len(data) > 30 or any(not ALIAS.fullmatch(a) for a in data):
            raise ValueError()
        return {alias: Source.model_validate(value).model_dump() for alias, value in data.items()}
    except (ValueError, TypeError, rest.FeedError):
        raise rest.FeedError("invalid_workday_deployment_catalog", 422) from None


def password(src):
    value = rest.resolve_secret(src["credential_env"])
    require(isinstance(value, str) and 1 <= len(value) <= 1024
            and all(32 <= ord(c) <= 126 for c in value) and value.strip(),
            "workday_credential_unavailable", 409)
    return value


def available(src):
    try:
        password(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources": [{"source_alias": alias, "source_sha256": rest._digest(src),
                         "relation": src["event_report"], "control_report": src["count_report"],
                         "tenant": src["tenant"], "activities": sorted(src["activity_labels"].values()),
                         "resource_retained_as_pseudonym": src["resource_field"] is not None,
                         "credential_available": available(src)}
                        for alias, src in sorted(catalog().items())],
            "provider_reads_performed": False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value) == cfg["source_sha256"],
            "workday_source_definition_changed", 409)
    return value


def check_binding(p):
    secret = password(source(p["config"]))
    require(hmac.compare_digest(rest._key(p, "credential", secret), p["credential_binding"]),
            "credential_changed_reconfigure_required", 409)
    return secret


def create(value):
    try:
        spec = Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_workday_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_source_read and spec.confirm_metadata_retention,
            "explicit_workday_read_and_retention_required", 403)
    require(spec.name.strip() and all(ord(c) >= 32 and ord(c) != 127
                                     and not 0xD800 <= ord(c) <= 0xDFFF for c in spec.name),
            "invalid_profile_name", 422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now - timedelta(days=366) <= since < now - timedelta(minutes=spec.ingestion_lag_minutes),
            "workday_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_source_read", "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since))
    return rest.create_profile(cfg, password(source(cfg)), now, provider="workday")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def literal(value, code, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str) and 1 <= len(value) <= 200 and value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value), code)
    return value


def project(p, src, item, since, until):
    fields = {src[x] for x in ("event_field", "case_field", "activity_field", "timestamp_field")}
    if src["resource_field"]:
        fields.add(src["resource_field"])
    require(isinstance(item, dict) and set(item) == fields, "workday_report_schema_mismatch")
    event = literal(item[src["event_field"]], "workday_invalid_event_identity")
    case = literal(item[src["case_field"]], "workday_invalid_case_identity")
    label = item[src["activity_field"]]
    require(isinstance(label, str) and label in src["activity_labels"], "workday_unreviewed_activity")
    raw_time = item[src["timestamp_field"]]
    require(isinstance(raw_time, str) and STAMP.fullmatch(raw_time) is not None,
            "workday_invalid_timestamp")
    stamp = rest._stamp(raw_time)
    require(since <= stamp < until, "workday_row_outside_window")
    resource = literal(item[src["resource_field"]] if src["resource_field"] else None,
                       "workday_invalid_resource", optional=True)
    return [rest._key(p, "workday_event", event), rest._key(p, "workday_case", case),
            src["activity_labels"][label], rest._iso(stamp),
            rest._key(p, "workday_resource", resource) if resource else ""]


class Reader:
    def __init__(self, p, client, credential_check=rest._check):
        self.p, self.client, self.credential_check = p, client, credential_check
        self.src = source(p["config"])
        self.pages = self.bytes = 0
        self.deadline = time.monotonic() + rest.MAX_SECONDS

    def request(self, report, since, until):
        require(report in (self.src["event_report"], self.src["count_report"]),
                "workday_unapproved_report")
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        secret = self.credential_check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        path = BASE + "/".join([self.src["tenant"], self.src["report_owner"], report])
        params = {"format": "json", self.src["since_prompt"]: rest._iso(since),
                  self.src["until_prompt"]: rest._iso(until)}
        auth = httpx.BasicAuth(self.src["integration_user"] + "@" + self.src["tenant"], secret)
        try:
            with self.client.stream("GET", "https://" + self.src["host"] + path,
                                    params=params, auth=auth, headers={"Accept": "application/json"},
                                    timeout=min(20, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited", 429, delay)
                require(response.status_code == 200, "workday_http_failure")
                require(response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        == "application/json", "workday_unexpected_content_type")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes + len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        result = rest._strict_json(bytes(raw))
        require(isinstance(result, dict) and set(result) == {"Report_Entry"}
                and isinstance(result["Report_Entry"], list), "workday_invalid_report_shape")
        require(len(result["Report_Entry"]) <= rest.MAX_ROWS, "workday_window_population_exceeded")
        return result["Report_Entry"]

    def count(self, since, until):
        data = self.request(self.src["count_report"], since, until)
        require(len(data) == 1 and isinstance(data[0], dict)
                and set(data[0]) == {self.src["count_field"]}, "workday_invalid_control_report")
        value = data[0][self.src["count_field"]]
        require(isinstance(value, str) and COUNT.fullmatch(value) is not None,
                "workday_invalid_control_count")
        count = int(value)
        require(count <= rest.MAX_ROWS, "workday_window_population_exceeded")
        return count

    def scan(self, since, until, expected_count):
        data = self.request(self.src["event_report"], since, until)
        require(len(data) == expected_count, "workday_control_population_mismatch")
        rows, seen = [], set()
        for item in data:
            row = project(self.p, self.src, item, since, until)
            require(row[0] not in seen, "workday_duplicate_event_identity")
            seen.add(row[0])
            rows.append(row)
        # Workday report sort order is not assumed to be stable.
        return sorted(rows, key=lambda row: row[0])


def extract(p, since, until, client_factory=httpx.Client):
    with client_factory(timeout=20, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        count = reader.count(since, until)
        first = reader.scan(since, until, count)
        second = reader.scan(since, until, count)
        require(first == second, "workday_repeat_scan_changed")
        require(reader.count(since, until) == count, "workday_control_count_changed")
        reader.credential_check(p)
        return first, {"pages": reader.pages, "records_received": len(first) + len(second),
                       "distinct_records_in_window": len(first), "duplicate_records_in_window": 0,
                       "control_count_agreement": True, "repeat_scan_agreement": True,
                       "independent_principal_readback_verified": False,
                       "transaction_snapshot_verified": False, "provider_completeness_verified": False,
                       "raw_source_identifiers_retained": False}
