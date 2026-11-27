"""BigQuery approved event views, renewable read-only service-account OAuth.

Fixed parameterized Standard SQL, explicit query billing cap, exact job scope
and result pagination. No arbitrary SQL, exports, DDL, or delegated user access.
"""
from datetime import datetime, timedelta, timezone
import base64
import hmac
import json
import os
import re
import time
from uuid import uuid4

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator
from . import enterprise_rest_feed as rest

CATALOG = "OWG_BIGQUERY_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
LOCATION = re.compile(r"(?:US|EU|[a-z][a-z0-9-]{1,39})\Z")
EMAIL = re.compile(r"[a-z0-9][a-z0-9-]{0,100}@[a-z0-9][a-z0-9-]{0,100}\.iam\.gserviceaccount\.com\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,119}\Z")
JOB = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
PAGE = re.compile(r"[A-Za-z0-9_+/=-]{1,4096}\Z")
INT = re.compile(r"-?(?:0|[1-9][0-9]{0,18})\Z")
UINT = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://bigquery.googleapis.com/bigquery/v2/projects/"
SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ("owg_event", "owg_case", "owg_activity", "owg_micros", "owg_resource", "owg_principal")


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    billing_project: str
    data_project: str
    dataset: str
    view: str
    location: str
    service_account: str
    key_id: str = Field(pattern=r"^[a-f0-9]{40}$")
    credential_env: str = Field(pattern=r"^OWG_BIGQUERY_KEY_[A-Z0-9_]{1,48}$")
    event_column: str
    case_column: str
    activity_column: str
    timestamp_column: str
    resource_column: str | None = None
    activity_labels: dict[str, str] = Field(min_length=1, max_length=100)
    maximum_bytes_billed: StrictInt = Field(ge=10485760, le=1099511627776)

    @model_validator(mode="after")
    def reviewed(self):
        cols = [self.event_column, self.case_column, self.activity_column, self.timestamp_column]
        if self.resource_column is not None:
            cols.append(self.resource_column)
        if (not PROJECT.fullmatch(self.billing_project) or not PROJECT.fullmatch(self.data_project)
                or not LOCATION.fullmatch(self.location) or not EMAIL.fullmatch(self.service_account)
                or any(not IDENT.fullmatch(n) for n in [self.dataset, self.view] + cols)
                or len({n.lower() for n in cols}) != len(cols)):
            raise ValueError("fixed project, location, service account and distinct reviewed identifiers required")
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
    page_size: StrictInt = Field(ge=1, le=1000, default=500)
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
        raise rest.FeedError("invalid_bigquery_deployment_catalog", 422) from None


def key_material(src):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from hashlib import sha256
    value = rest.resolve_secret(src["credential_env"])
    require(isinstance(value, str) and 1 <= len(value) <= 8192,
            "bigquery_credential_unavailable", 409)
    try:
        key = serialization.load_der_private_key(base64.b64decode(value, validate=True), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or not 2048 <= key.key_size <= 4096:
            raise ValueError()
    except Exception:
        raise rest.FeedError("invalid_bigquery_rsa_private_key", 409) from None
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, sha256(public).hexdigest()


def available(src):
    try:
        key_material(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources": [{"source_alias": alias, "source_sha256": rest._digest(src),
                         "relation": ".".join([src["data_project"], src["dataset"], src["view"]]),
                         "billing_project": src["billing_project"], "location": src["location"],
                         "maximum_bytes_billed": src["maximum_bytes_billed"],
                         "activities": sorted(src["activity_labels"].values()),
                         "resource_retained_as_pseudonym": src["resource_column"] is not None,
                         "credential_available": available(src)}
                        for alias, src in sorted(catalog().items())],
            "provider_reads_performed": False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value) == cfg["source_sha256"],
            "bigquery_source_definition_changed", 409)
    return value


def check_binding(p):
    key, fingerprint = key_material(source(p["config"]))
    require(hmac.compare_digest(rest._key(p, "credential", fingerprint), p["credential_binding"]),
            "credential_changed_reconfigure_required", 409)
    return key


def create(value):
    try:
        spec = Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_bigquery_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_source_read and spec.confirm_metadata_retention,
            "explicit_bigquery_read_and_retention_required", 403)
    require(spec.name.strip() and all(ord(c) >= 32 and ord(c) != 127
                                     and not 0xD800 <= ord(c) <= 0xDFFF for c in spec.name),
            "invalid_profile_name", 422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now - timedelta(days=366) <= since < now - timedelta(minutes=spec.ingestion_lag_minutes),
            "bigquery_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_source_read", "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since))
    _, fingerprint = key_material(source(cfg))
    return rest.create_profile(cfg, fingerprint, now, provider="bigquery")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def assertion(src, key, *, now=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    stamp = int(time.time()) if now is None else now
    header = {"alg": "RS256", "typ": "JWT", "kid": src["key_id"]}
    claims = {"iss": src["service_account"], "scope": SCOPE, "aud": TOKEN_URL,
              "iat": stamp, "exp": stamp + 120}
    signing = b".".join(_b64(json.dumps(v, separators=(",", ":"), ensure_ascii=True).encode())
                        for v in (header, claims))
    return (signing + b"." + _b64(key.sign(signing, padding.PKCS1v15(), hashes.SHA256()))).decode("ascii")


def quote(name):
    require(isinstance(name, str) and IDENT.fullmatch(name) is not None,
            "invalid_bigquery_identifier", 422)
    return "\x60" + name + "\x60"


def query(src):
    # All identifiers are deployment-pinned; timestamps are named parameters.
    # No DML, temporary UDF, remote function or SQL text accepted from callers.
    require(PROJECT.fullmatch(src["data_project"]) and IDENT.fullmatch(src["dataset"])
            and IDENT.fullmatch(src["view"]), "invalid_bigquery_identifier", 422)
    relation = "\x60" + ".".join([src["data_project"], src["dataset"], src["view"]]) + "\x60"
    event, case, activity, stamp = (quote(src[x]) for x in
                                   ("event_column", "case_column", "activity_column", "timestamp_column"))
    resource = quote(src["resource_column"]) if src["resource_column"] else "CAST(NULL AS STRING)"
    return (f"SELECT {event} AS owg_event, {case} AS owg_case, {activity} AS owg_activity, "
            f"UNIX_MICROS({stamp}) AS owg_micros, {resource} AS owg_resource, "
            f"SESSION_USER() AS owg_principal FROM {relation} "
            f"WHERE {stamp} >= @owg_since AND {stamp} < @owg_until "
            f"ORDER BY {stamp}, {event} LIMIT {rest.MAX_ROWS + 1}")


def literal(value, code, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str) and 1 <= len(value) <= 200 and value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value), code)
    return value


def project(p, src, row, since, until):
    event = literal(row[0], "bigquery_invalid_event_identity")
    case = literal(row[1], "bigquery_invalid_case_identity")
    label = row[2]
    require(isinstance(label, str) and label in src["activity_labels"], "bigquery_unreviewed_activity")
    require(isinstance(row[3], str) and INT.fullmatch(row[3]) is not None, "bigquery_invalid_timestamp")
    try:
        stamp = EPOCH + timedelta(microseconds=int(row[3]))
    except OverflowError:
        raise rest.FeedError("bigquery_invalid_timestamp", 502) from None
    require(since <= stamp < until, "bigquery_row_outside_window")
    resource = literal(row[4], "bigquery_invalid_resource", optional=True)
    require(src["resource_column"] is not None or not resource, "bigquery_unexpected_resource")
    require(row[5] == src["service_account"], "bigquery_principal_mismatch")
    return [rest._key(p, "bigquery_event", event), rest._key(p, "bigquery_case", case),
            src["activity_labels"][label], rest._iso(stamp),
            rest._key(p, "bigquery_resource", resource) if resource else ""]


def unsigned(value, code):
    require(isinstance(value, str) and UINT.fullmatch(value) is not None, code)
    return int(value)


class Reader:
    def __init__(self, p, client, credential_check=rest._check):
        self.p, self.client, self.credential_check = p, client, credential_check
        self.src = source(p["config"])
        self.pages = self.bytes = 0
        self.deadline = time.monotonic() + rest.MAX_SECONDS
        self.token = None
        self.expiry = 0

    def request(self, method, url, *, params=None, body=None, form=None):
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        self.credential_check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        headers = {"Accept": "application/json"}
        if url != TOKEN_URL:
            require(self.token is not None and self.expiry > time.monotonic(), "bigquery_token_expired", 409)
            require(url.startswith(API + self.src["billing_project"] + "/queries"),
                    "bigquery_unapproved_destination")
            headers["Authorization"] = "Bearer " + self.token
        try:
            with self.client.stream(method, url, headers=headers, params=params,
                                    json=body, data=form, timeout=min(20, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited", 429, delay)
                require(response.status_code == 200, "bigquery_http_failure")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes + len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        result = rest._strict_json(bytes(raw))
        require(isinstance(result, dict), "bigquery_invalid_response")
        return result

    def authenticate(self):
        key = self.credential_check(self.p)
        response = self.request("POST", TOKEN_URL, form={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion(self.src, key)})
        token, expires = response.get("access_token"), response.get("expires_in")
        require(isinstance(token, str) and 16 <= len(token) <= 8192
                and all(33 <= ord(c) <= 126 for c in token)
                and isinstance(response.get("token_type"), str)
                and response["token_type"].lower() == "bearer"
                and type(expires) is int and 120 <= expires <= 3600
                and response.get("scope") in (None, SCOPE), "bigquery_invalid_token_response")
        self.token = token
        self.expiry = time.monotonic() + expires - 30

    def job_ref(self, response, expected=None):
        ref = response.get("jobReference")
        require(isinstance(ref, dict) and ref.get("projectId") == self.src["billing_project"]
                and ref.get("location") == self.src["location"]
                and isinstance(ref.get("jobId"), str) and JOB.fullmatch(ref["jobId"]) is not None
                and (expected is None or ref["jobId"] == expected), "bigquery_job_identity_mismatch")
        require(not response.get("errors") and not response.get("error")
                and type(response.get("jobComplete")) is bool, "bigquery_job_failure_or_warning")
        return ref["jobId"]

    def schema(self, response, names, types):
        schema = response.get("schema")
        require(isinstance(schema, dict) and isinstance(schema.get("fields"), list)
                and len(schema["fields"]) == len(names), "bigquery_schema_mismatch")
        for field, name, allowed in zip(schema["fields"], names, types):
            require(isinstance(field, dict) and field.get("name") == name
                    and field.get("type") in allowed and field.get("mode", "NULLABLE") in ("NULLABLE", "REQUIRED")
                    and not field.get("fields"), "bigquery_schema_mismatch")

    def execute(self, sql, names, types, *, parameters=(), limit=rest.MAX_ROWS):
        endpoint = API + self.src["billing_project"] + "/queries"
        size = min(self.p["config"]["page_size"], limit + 1)
        body = {"query": sql, "useLegacySql": False, "useQueryCache": False, "maxResults": size,
                "location": self.src["location"], "timeoutMs": 10000, "jobTimeoutMs": "60000",
                "maximumBytesBilled": str(self.src["maximum_bytes_billed"]),
                "requestId": str(uuid4()), "parameterMode": "NAMED", "queryParameters": list(parameters)}
        response = self.request("POST", endpoint, body=body)
        job = self.job_ref(response)
        url = endpoint + "/" + job
        params = {"location": self.src["location"], "maxResults": size, "timeoutMs": 10000}
        while not response["jobComplete"]:
            require(not response.get("rows") and not response.get("pageToken"),
                    "bigquery_incomplete_job_has_data")
            response = self.request("GET", url, params=params)
            self.job_ref(response, job)
        self.schema(response, names, types)
        total = unsigned(response.get("totalRows"), "bigquery_invalid_population")
        require(total <= limit, "bigquery_result_population_exceeded")
        rows, seen_pages = [], set()
        billed = None
        while True:
            self.job_ref(response, job)
            require(response["jobComplete"], "bigquery_incomplete_job")
            if "schema" in response:
                self.schema(response, names, types)
            require(unsigned(response.get("totalRows"), "bigquery_invalid_population") == total,
                    "bigquery_result_population_changed")
            if response.get("totalBytesBilled") is not None:
                current = unsigned(response["totalBytesBilled"], "bigquery_invalid_billing_receipt")
                require(current <= self.src["maximum_bytes_billed"]
                        and (billed is None or billed == current), "bigquery_billing_receipt_mismatch")
                billed = current
            data = response.get("rows", [])
            require(isinstance(data, list) and len(data) <= size and len(rows) + len(data) <= total,
                    "bigquery_invalid_page_population")
            for item in data:
                require(isinstance(item, dict) and set(item) == {"f"} and isinstance(item["f"], list)
                        and len(item["f"]) == len(names), "bigquery_invalid_row")
                row = []
                for cell in item["f"]:
                    require(isinstance(cell, dict) and set(cell) == {"v"}
                            and (cell["v"] is None or isinstance(cell["v"], str)), "bigquery_invalid_cell")
                    row.append(cell["v"])
                rows.append(row)
            token = response.get("pageToken")
            if token in (None, ""):
                break
            require(isinstance(token, str) and PAGE.fullmatch(token) is not None
                    and token not in seen_pages and len(rows) < total and bool(data),
                    "bigquery_invalid_page_token")
            seen_pages.add(token)
            response = self.request("GET", url, params={**params, "pageToken": token})
        require(len(rows) == total, "bigquery_incomplete_result")
        return rows, billed

    def identity(self):
        rows, _ = self.execute("SELECT SESSION_USER() AS owg_principal",
                               ("owg_principal",), (("STRING",),), limit=1)
        require(rows == [[self.src["service_account"]]], "bigquery_principal_mismatch")


def extract(p, since, until, client_factory=httpx.Client):
    rows, seen = [], set()
    with client_factory(timeout=20, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        reader.authenticate()
        reader.identity()
        params = [{"name": name, "parameterType": {"type": "TIMESTAMP"},
                   "parameterValue": {"value": rest._iso(at)}}
                  for name, at in (("owg_since", since), ("owg_until", until))]
        text = ("STRING",)
        data, billed = reader.execute(query(reader.src), FIELDS,
                                      (text, text, text, ("INTEGER", "INT64"), text, text), parameters=params)
        for item in data:
            row = project(p, reader.src, item, since, until)
            require(row[0] not in seen, "bigquery_duplicate_event_identity")
            seen.add(row[0])
            rows.append(row)
        reader.credential_check(p)
        return rows, {"pages": reader.pages, "records_received": len(rows),
                      "duplicate_records_in_window": 0, "principal_readback_verified": True,
                      "source_view_schema_checked": True, "query_bytes_billed": billed,
                      "query_maximum_bytes_billed": reader.src["maximum_bytes_billed"],
                      "provider_completeness_verified": False, "raw_source_identifiers_retained": False}
