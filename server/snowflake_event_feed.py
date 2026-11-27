"""Read approved Snowflake event views with the bounded retained-feed runtime.

Only fixed SQL API SELECTs are sent. Account, user and role are checked before
and after each result. A curated view is not a CDC feed or proof of completeness.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hmac
import os
import re
import time
from uuid import uuid4

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest

CATALOG = "OWG_SNOWFLAKE_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\.snowflakecomputing\.com\Z")
HANDLE = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,119}\Z")
TIMESTAMP = re.compile(r"(-?[0-9]{1,15}(?:\.[0-9]{1,9})?)(?: ([0-9]{3,4}))?\Z")
BASE = "/api/v2/statements"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ("OWG_EVENT", "OWG_CASE", "OWG_ACTIVITY", "OWG_TIMESTAMP", "OWG_RESOURCE")
IDENTITY_FIELDS = ("OWG_ACCOUNT", "OWG_USER", "OWG_ROLE")


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    host: str = Field(max_length=150)
    warehouse: str
    database: str
    schema_name: str
    view: str
    role: str
    expected_account: str
    expected_user: str
    credential_env: str = Field(pattern=r"^OWG_SNOWFLAKE_TOKEN_[A-Z0-9_]{1,48}$")
    event_column: str
    case_column: str
    activity_column: str
    timestamp_column: str
    resource_column: str | None = None
    activity_labels: dict[str, str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reviewed(self):
        names = [self.warehouse, self.database, self.schema_name, self.view, self.role,
                 self.expected_account, self.expected_user]
        cols = [self.event_column, self.case_column, self.activity_column, self.timestamp_column]
        if self.resource_column is not None:
            cols.append(self.resource_column)
        if (not HOST.fullmatch(self.host)
                or any(not IDENT.fullmatch(n) for n in names + cols)
                or len({n.upper() for n in cols}) != len(cols)):
            raise ValueError("fixed public account host and distinct reviewed SQL identifiers required")
        if (any(not LABEL.fullmatch(k) or not LABEL.fullmatch(v)
                for k, v in self.activity_labels.items())
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
        raise rest.FeedError("invalid_snowflake_deployment_catalog", 422) from None


def token(src):
    value = rest.resolve_secret(src["credential_env"])
    require(isinstance(value, str) and 16 <= len(value) <= 4096
            and all(33 <= ord(c) <= 126 for c in value), "snowflake_credential_unavailable", 409)
    return value


def available(src):
    try:
        token(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources": [{"source_alias": alias, "source_sha256": rest._digest(src),
                         "relation": ".".join([src["database"], src["schema_name"], src["view"]]),
                         "role": src["role"], "activities": sorted(src["activity_labels"].values()),
                         "resource_retained_as_pseudonym": src["resource_column"] is not None,
                         "credential_available": available(src)}
                        for alias, src in sorted(catalog().items())],
            "provider_reads_performed": False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value) == cfg["source_sha256"],
            "snowflake_source_definition_changed", 409)
    return value


def check_binding(p):
    secret = token(source(p["config"]))
    require(hmac.compare_digest(rest._key(p, "credential", secret), p["credential_binding"]),
            "credential_changed_reconfigure_required", 409)
    return secret


def create(value):
    try:
        spec = Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_snowflake_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_source_read and spec.confirm_metadata_retention,
            "explicit_snowflake_read_and_retention_required", 403)
    require(spec.name.strip() and all(ord(c) >= 32 and ord(c) != 127
                                     and not 0xD800 <= ord(c) <= 0xDFFF for c in spec.name),
            "invalid_profile_name", 422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now - timedelta(days=366) <= since < now - timedelta(minutes=spec.ingestion_lag_minutes),
            "snowflake_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_source_read", "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since))
    return rest.create_profile(cfg, token(source(cfg)), now, provider="snowflake")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def quote(name):
    require(isinstance(name, str) and IDENT.fullmatch(name) is not None,
            "invalid_snowflake_identifier", 422)
    return '"' + name + '"'


def query(src):
    relation = ".".join(quote(src[x]) for x in ("database", "schema_name", "view"))
    event, case, activity, stamp = (quote(src[x]) for x in
                                   ("event_column", "case_column", "activity_column", "timestamp_column"))
    resource = quote(src["resource_column"]) if src["resource_column"] else "CAST(NULL AS VARCHAR)"
    # Keep native result types. This validates the actual query's schema,
    # including empty results, instead of trusting a separate schema probe.
    return (f"SELECT {event} AS OWG_EVENT, {case} AS OWG_CASE, {activity} AS OWG_ACTIVITY, "
            f"{stamp} AS OWG_TIMESTAMP, {resource} AS OWG_RESOURCE FROM {relation} "
            f"WHERE {stamp} >= TO_TIMESTAMP_LTZ(?,9) AND {stamp} < TO_TIMESTAMP_LTZ(?,9) "
            f"ORDER BY {stamp}, {event} LIMIT {rest.MAX_ROWS + 1}")


def nanos(stamp):
    delta = stamp - EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000


def parse_timestamp(value, native_type):
    # SQL API jsonv2 default LTZ/TZ encoding: epoch seconds, with a
    # Snowflake-encoded offset suffix only for TZ. No local-time guessing.
    match = TIMESTAMP.fullmatch(value) if isinstance(value, str) else None
    require(match is not None, "snowflake_invalid_timestamp")
    epoch, offset = match.groups()
    if native_type == "timestamp_tz":
        require(offset is not None and 720 <= int(offset) <= 2160, "snowflake_invalid_timestamp_offset")
    else:
        require(native_type == "timestamp_ltz" and offset is None, "snowflake_invalid_timestamp_type")
    micros = Decimal(epoch) * 1000000
    require(micros == micros.to_integral_value(), "snowflake_submicrosecond_precision_unsupported")
    try:
        return EPOCH + timedelta(microseconds=int(micros))
    except OverflowError:
        raise rest.FeedError("snowflake_invalid_timestamp", 502) from None


def literal(value, code, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str) and 1 <= len(value) <= 200 and value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value), code)
    return value


def project(p, src, row, since, until, native_type):
    event = literal(row[0], "snowflake_invalid_event_identity")
    case = literal(row[1], "snowflake_invalid_case_identity")
    label = row[2]
    require(isinstance(label, str) and label in src["activity_labels"], "snowflake_unreviewed_activity")
    at = parse_timestamp(row[3], native_type)
    require(since <= at < until, "snowflake_row_outside_window")
    resource = literal(row[4], "snowflake_invalid_resource", optional=True)
    require(src["resource_column"] is not None or not resource, "snowflake_unexpected_resource")
    return [rest._key(p, "snowflake_event", event), rest._key(p, "snowflake_case", case),
            src["activity_labels"][label], rest._iso(at),
            rest._key(p, "snowflake_resource", resource) if resource else ""]


class Reader:
    def __init__(self, p, client, credential_check=rest._check):
        self.p, self.client, self.credential_check = p, client, credential_check
        self.src = source(p["config"])
        self.pages = self.bytes = 0
        self.deadline = time.monotonic() + rest.MAX_SECONDS

    def request(self, method, path, *, body=None, partition=None):
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        secret = self.credential_check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        params = {"requestId": str(uuid4())}
        if method == "POST":
            params["async"] = "false"
        if partition is not None:
            params["partition"] = str(partition)
        try:
            with self.client.stream(method, "https://" + self.src["host"] + path, params=params, json=body,
                                    headers={"Authorization": "Bearer " + secret,
                                             "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
                                             "User-Agent": "OpenWorkGraph/1.0", "Accept": "application/json"},
                                    timeout=min(30, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited_or_pending", 429, delay)
                # Refuse pending/expired/truncated operations; never replay a
                # submitted statement after uncertain transport failure.
                require(response.status_code == 200, "snowflake_http_or_statement_failure")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes + len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        result = rest._strict_json(bytes(raw))
        require(isinstance(result, dict), "snowflake_invalid_response")
        return result

    def execute(self, statement, names, types, bindings=None, limit=rest.MAX_ROWS):
        response = self.request("POST", BASE, body={
            "statement": statement, "timeout": 20,
            "warehouse": self.src["warehouse"], "database": self.src["database"],
            "schema": self.src["schema_name"], "role": self.src["role"],
            "bindings": bindings or {},
            "parameters": {"TIMEZONE": "UTC", "USE_CACHED_RESULT": "false", "AUTOCOMMIT": "true"},
        })
        require(response.get("code") == "090001" and response.get("sqlState") == "00000"
                and not response.get("statementHandles"), "snowflake_incomplete_statement")
        handle = response.get("statementHandle")
        require(isinstance(handle, str) and HANDLE.fullmatch(handle) is not None,
                "snowflake_invalid_statement_handle")
        meta = response.get("resultSetMetaData")
        require(isinstance(meta, dict) and meta.get("format") == "jsonv2",
                "snowflake_invalid_result_metadata")
        rowtype = meta.get("rowType")
        require(isinstance(rowtype, list) and len(rowtype) == len(names), "snowflake_schema_mismatch")
        actual_types = []
        for name, allowed, col in zip(names, types, rowtype):
            require(isinstance(col, dict) and col.get("name") == name
                    and isinstance(col.get("type"), str) and col["type"].lower() in allowed,
                    "snowflake_schema_mismatch")
            actual_types.append(col["type"].lower())
        total, partitions = meta.get("numRows"), meta.get("partitionInfo")
        require(type(total) is int and 0 <= total <= limit and isinstance(partitions, list)
                and 1 <= len(partitions) <= rest.MAX_PAGES, "snowflake_result_population_exceeded")
        counts = []
        for part in partitions:
            require(isinstance(part, dict) and type(part.get("rowCount")) is int
                    and 0 <= part["rowCount"] <= total, "snowflake_partition_metadata_invalid")
            counts.append(part["rowCount"])
        require(sum(counts) == total, "snowflake_partition_population_mismatch")
        rows = []
        for i, count in enumerate(counts):
            # Construct the exact result endpoint, never follow response links.
            chunk = response if i == 0 else self.request("GET", BASE + "/" + handle, partition=i)
            require(chunk.get("statementHandle", handle) == handle
                    and chunk.get("code", "090001") == "090001"
                    and chunk.get("sqlState", "00000") == "00000"
                    and not chunk.get("statementHandles"), "snowflake_partition_identity_mismatch")
            data = chunk.get("data")
            require(isinstance(data, list) and len(data) == count,
                    "snowflake_incomplete_partition")
            for row in data:
                require(isinstance(row, list) and len(row) == len(names)
                        and all(x is None or isinstance(x, str) for x in row), "snowflake_invalid_row")
            rows.extend(data)
        require(len(rows) == total, "snowflake_incomplete_result")
        return rows, actual_types

    def identity(self):
        text = ("text",)
        rows, _ = self.execute("SELECT CURRENT_ACCOUNT() AS OWG_ACCOUNT, CURRENT_USER() AS OWG_USER, "
                               "CURRENT_ROLE() AS OWG_ROLE", IDENTITY_FIELDS, (text,) * 3, limit=1)
        require(rows == [[self.src["expected_account"], self.src["expected_user"], self.src["role"]]],
                "snowflake_account_user_or_role_mismatch")


def extract(p, since, until, client_factory=httpx.Client):
    rows, seen = [], set()
    with client_factory(timeout=30, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        reader.identity()
        bindings = {str(i): {"type": "FIXED", "value": str(nanos(at))}
                    for i, at in enumerate((since, until), 1)}
        text = ("text",)
        data, types = reader.execute(query(reader.src), FIELDS,
                                     (text, text, text, ("timestamp_ltz", "timestamp_tz"), text), bindings)
        for item in data:
            row = project(p, reader.src, item, since, until, types[3])
            require(row[0] not in seen, "snowflake_duplicate_event_identity")
            seen.add(row[0])
            rows.append(row)
        reader.identity()
        return rows, {"pages": reader.pages, "records_received": len(rows),
                      "duplicate_records_in_window": 0, "account_user_role_readback_verified": True,
                      "source_view_schema_checked": True, "provider_completeness_verified": False,
                      "raw_source_identifiers_retained": False}
