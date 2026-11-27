"""Databricks SQL warehouse ingestion of explicitly approved event views.

Fixed read-only statement shape, no arbitrary SQL or cloud-storage links.
A customer view declares case identity; warehouse results are not CDC.
"""
from datetime import datetime, timedelta, timezone
import hmac
import os
import re
import time

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator
from . import enterprise_rest_feed as rest

CATALOG = "OWG_DATABRICKS_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\.(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)\Z")
STATEMENT_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,119}\Z")
BASE = "/api/2.0/sql/statements"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ("owg_event", "owg_case", "owg_activity", "owg_micros", "owg_resource")


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    host: str = Field(max_length=150)
    warehouse_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    expected_principal: str = Field(min_length=1, max_length=200)
    credential_env: str = Field(pattern=r"^OWG_DATABRICKS_TOKEN_[A-Z0-9_]{1,48}$")
    catalog: str
    schema_name: str
    view: str
    event_column: str
    case_column: str
    activity_column: str
    timestamp_column: str
    resource_column: str | None = None
    activity_labels: dict[str, str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reviewed(self):
        names = [self.catalog, self.schema_name, self.view, self.event_column,
                 self.case_column, self.activity_column, self.timestamp_column]
        columns = names[3:] + ([self.resource_column] if self.resource_column else [])
        if (not HOST.fullmatch(self.host) or any(not IDENT.fullmatch(n) for n in names)
                or any(not IDENT.fullmatch(n) for n in columns)
                or len({n.lower() for n in columns}) != len(columns)):
            raise ValueError("fixed workspace and distinct simple SQL identifiers required")
        if any(ord(c) < 33 or ord(c) > 126 for c in self.expected_principal):
            raise ValueError("invalid principal")
        if (any(not LABEL.fullmatch(k) or not LABEL.fullmatch(v)
                for k, v in self.activity_labels.items())
                or len(set(self.activity_labels.values())) != len(self.activity_labels)):
            raise ValueError("exact distinct activity mapping required")
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
        raise rest.FeedError("invalid_databricks_deployment_catalog", 422) from None


def token(src):
    secret = rest.resolve_secret(src["credential_env"])
    require(isinstance(secret, str) and 16 <= len(secret) <= 4096
            and all(33 <= ord(c) <= 126 for c in secret), "databricks_credential_unavailable", 409)
    return secret


def available(src):
    try:
        token(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources": [{"source_alias": alias, "source_sha256": rest._digest(src),
                         "relation": ".".join([src["catalog"], src["schema_name"], src["view"]]),
                         "activities": sorted(src["activity_labels"].values()),
                         "resource_retained_as_pseudonym": src["resource_column"] is not None,
                         "credential_available": available(src)}
                        for alias, src in sorted(catalog().items())],
            "provider_reads_performed": False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value) == cfg["source_sha256"],
            "databricks_source_definition_changed", 409)
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
        raise rest.FeedError("invalid_databricks_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_source_read and spec.confirm_metadata_retention,
            "explicit_databricks_read_and_retention_required", 403)
    require(spec.name.strip() and all(32 <= ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF
                                     for c in spec.name), "invalid_profile_name", 422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now - timedelta(days=366) <= since < now - timedelta(minutes=spec.ingestion_lag_minutes),
            "databricks_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_source_read", "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since))
    src = source(cfg)
    return rest.create_profile(cfg, token(src), now, provider="databricks")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def quote(name):
    require(isinstance(name, str) and IDENT.fullmatch(name) is not None,
            "invalid_databricks_identifier", 422)
    return "\x60" + name + "\x60"


def query(src):
    relation = ".".join(quote(src[x]) for x in ("catalog", "schema_name", "view"))
    event, case, activity, stamp = (quote(src[x]) for x in
                                   ("event_column", "case_column", "activity_column", "timestamp_column"))
    resource = quote(src["resource_column"]) if src["resource_column"] else "CAST(NULL AS STRING)"
    # The approved view must expose STRING identities/activity and TIMESTAMP
    # (instant, not TIMESTAMP_NTZ). No casts of arbitrary identities to string.
    # Check types within the statement, including the zero-row case via a
    # separate schema probe. Timestamp boundaries are epoch microseconds.
    return (f"SELECT {event} AS owg_event, {case} AS owg_case, "
            f"{activity} AS owg_activity, CAST(unix_micros({stamp}) AS STRING) AS owg_micros, "
            f"{resource} AS owg_resource FROM {relation} "
            f"WHERE {stamp} >= timestamp_micros(:owg_since) AND {stamp} < timestamp_micros(:owg_until) "
            f"ORDER BY {stamp}, {event} LIMIT {rest.MAX_ROWS + 1}")


def schema_query(src):
    relation = ".".join(quote(src[x]) for x in ("catalog", "schema_name", "view"))
    cols = [quote(src[x]) for x in ("event_column", "case_column", "activity_column", "timestamp_column")]
    cols.append(quote(src["resource_column"]) if src["resource_column"] else "CAST(NULL AS STRING)")
    return "SELECT " + ", ".join(f"{col} AS {name}" for col, name in zip(cols, FIELDS)) + f" FROM {relation} LIMIT 0"


def micros(stamp):
    delta = stamp - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def literal(value, code, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str) and 1 <= len(value) <= 200 and value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value), code)
    return value


def project(p, src, row, since, until):
    require(isinstance(row, list) and len(row) == len(FIELDS), "databricks_invalid_row")
    event = literal(row[0], "databricks_invalid_event_identity")
    case = literal(row[1], "databricks_invalid_case_identity")
    label = row[2]
    require(isinstance(label, str) and label in src["activity_labels"], "databricks_unreviewed_activity")
    raw_time = row[3]
    require(isinstance(raw_time, str) and re.fullmatch(r"-?[0-9]{1,19}", raw_time) is not None,
            "databricks_invalid_timestamp")
    try:
        stamp = EPOCH + timedelta(microseconds=int(raw_time))
    except OverflowError:
        raise rest.FeedError("databricks_invalid_timestamp", 502) from None
    require(since <= stamp < until, "databricks_row_outside_window")
    resource = literal(row[4], "databricks_invalid_resource", optional=True)
    require(src["resource_column"] is not None or not resource, "databricks_unexpected_resource")
    return [rest._key(p, "databricks_event", event), rest._key(p, "databricks_case", case),
            src["activity_labels"][label], rest._iso(stamp),
            rest._key(p, "databricks_resource", resource) if resource else ""]


class Reader:
    def __init__(self, p, client, credential_check=rest._check):
        self.p, self.client, self.credential_check = p, client, credential_check
        self.src = source(p["config"])
        self.pages = self.bytes = 0
        self.deadline = time.monotonic() + rest.MAX_SECONDS

    def request(self, method, path, body=None):
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        secret = self.credential_check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        try:
            with self.client.stream(method, "https://" + self.src["host"] + path, json=body,
                                    headers={"Authorization": "Bearer " + secret, "Accept": "application/json"},
                                    timeout=min(20, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited", 429, delay)
                require(response.status_code == 200, "databricks_http_failure")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes + len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        result = rest._strict_json(bytes(raw))
        require(isinstance(result, dict), "databricks_invalid_response")
        return result

    def execute(self, statement, names, types, params=(), limit=rest.MAX_ROWS):
        response = self.request("POST", BASE, {
            "warehouse_id": self.src["warehouse_id"], "statement": statement,
            "parameters": list(params), "format": "JSON_ARRAY", "disposition": "INLINE",
            "wait_timeout": "10s", "on_wait_timeout": "CANCEL", "row_limit": limit + 1,
        })
        # No asynchronous polling or replay of an uncertain submission.
        require(isinstance(response.get("status"), dict) and response["status"].get("state") == "SUCCEEDED",
                "databricks_statement_not_completed")
        sid = response.get("statement_id")
        require(isinstance(sid, str) and STATEMENT_ID.fullmatch(sid) is not None, "databricks_invalid_statement_id")
        manifest = response.get("manifest")
        require(isinstance(manifest, dict) and manifest.get("format") == "JSON_ARRAY"
                and manifest.get("truncated") is False, "databricks_incomplete_manifest")
        schema = manifest.get("schema")
        require(isinstance(schema, dict) and schema.get("column_count") == len(names)
                and isinstance(schema.get("columns"), list) and len(schema["columns"]) == len(names),
                "databricks_schema_mismatch")
        for i, col in enumerate(schema["columns"]):
            require(isinstance(col, dict) and col.get("position") == i and col.get("name") == names[i]
                    and col.get("type_name") == types[i], "databricks_schema_mismatch")
        total, count = manifest.get("total_row_count"), manifest.get("total_chunk_count")
        chunks = manifest.get("chunks")
        require(type(total) is int and 0 <= total <= limit and type(count) is int
                and 0 <= count <= rest.MAX_PAGES and isinstance(chunks, list) and len(chunks) == count,
                "databricks_manifest_population_exceeded")
        if count == 0:
            require(total == 0 and response.get("result") in (None, {}), "databricks_incomplete_result")
            return []
        rows = []
        for i, metadata in enumerate(chunks):
            require(isinstance(metadata, dict) and type(metadata.get("row_count")) is int
                    and metadata["row_count"] >= 0 and metadata.get("chunk_index") == i
                    and metadata.get("row_offset") == len(rows), "databricks_chunk_manifest_mismatch")
            # Construct the path ourselves, never follow response links.
            chunk = response.get("result") if i == 0 else self.request("GET", BASE + f"/{sid}/result/chunks/{i}")
            require(isinstance(chunk, dict) and not chunk.get("external_links")
                    and chunk.get("chunk_index") == i and chunk.get("row_offset") == len(rows)
                    and chunk.get("row_count") == metadata["row_count"]
                    and isinstance(chunk.get("data_array"), list)
                    and len(chunk["data_array"]) == metadata["row_count"], "databricks_incomplete_chunk")
            require(len(rows) + len(chunk["data_array"]) <= total, "databricks_chunk_population_mismatch")
            for row in chunk["data_array"]:
                require(isinstance(row, list) and len(row) == len(names)
                        and all(x is None or isinstance(x, str) for x in row), "databricks_invalid_row")
            rows.extend(chunk["data_array"])
        require(len(rows) == total, "databricks_incomplete_result")
        return rows

    def identity(self):
        rows = self.execute("SELECT current_user() AS owg_principal", ("owg_principal",), ("STRING",), limit=1)
        require(rows == [[self.src["expected_principal"]]], "databricks_principal_mismatch")


def extract(p, since, until, client_factory=httpx.Client):
    rows, seen = [], set()
    with client_factory(timeout=20, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        reader.identity()
        reader.execute(schema_query(reader.src), FIELDS, ("STRING", "STRING", "STRING", "TIMESTAMP", "STRING"), limit=0)
        params = [{"name": name, "value": str(micros(at)), "type": "BIGINT"}
                  for name, at in (("owg_since", since), ("owg_until", until))]
        for item in reader.execute(query(reader.src), FIELDS, ("STRING",) * 5, params):
            row = project(p, reader.src, item, since, until)
            require(row[0] not in seen, "databricks_duplicate_event_identity")
            seen.add(row[0])
            rows.append(row)
        reader.identity()
        return rows, {"pages": reader.pages, "records_received": len(rows),
                      "duplicate_records_in_window": 0, "principal_readback_verified": True,
                      "source_view_schema_checked": True, "provider_completeness_verified": False,
                      "raw_source_identifiers_retained": False}
