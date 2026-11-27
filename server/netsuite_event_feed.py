"""NetSuite SuiteQL curated event records with renewable client credentials.

Deployment-owned typed custom record, fixed account and certificate, no
arbitrary SQL, record mutations, implicit extraction or external result links.
SuiteQL offset pages are not a transactional snapshot: require two matching
complete scans and report the remaining limitation explicitly.
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

CATALOG = "OWG_NETSUITE_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
ACCOUNT = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
RECORD = re.compile(r"customrecord_[a-z][a-z0-9_]{0,79}\Z")
COLUMN = re.compile(r"custrecord_[a-z][a-z0-9_]{0,79}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,119}\Z")
OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
INTEGER = re.compile(r"[0-9]{1,19}\Z")
TOKEN_PATH = "/services/rest/auth/oauth2/v1/token"
QUERY_PATH = "/services/rest/query/v1/suiteql"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ("owg_event", "owg_case", "owg_activity", "owg_micros", "owg_resource")


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    account_domain: str
    client_id: str = Field(min_length=1, max_length=200)
    certificate_id: str = Field(min_length=1, max_length=200)
    credential_env: str = Field(pattern=r"^OWG_NETSUITE_KEY_[A-Z0-9_]{1,48}$")
    event_record: str
    case_column: str
    activity_column: str
    epoch_micros_column: str
    resource_column: str | None = None
    activity_labels: dict[str, str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reviewed(self):
        cols = [self.case_column, self.activity_column, self.epoch_micros_column]
        if self.resource_column is not None:
            cols.append(self.resource_column)
        if (not ACCOUNT.fullmatch(self.account_domain)
                or not RECORD.fullmatch(self.event_record)
                or not OPAQUE.fullmatch(self.client_id)
                or not OPAQUE.fullmatch(self.certificate_id)
                or any(not COLUMN.fullmatch(c) for c in cols)
                or len(set(cols)) != len(cols)):
            raise ValueError("fixed account, certificate and distinct custom-record fields required")
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
        raise rest.FeedError("invalid_netsuite_deployment_catalog", 422) from None


def key_material(src):
    # Existing managed secret files deliberately contain one line. Encode
    # unencrypted PKCS#8 DER as standard base64, not a multiline PEM.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    value = rest.resolve_secret(src["credential_env"])
    require(isinstance(value, str) and 1 <= len(value) <= 8192,
            "netsuite_credential_unavailable", 409)
    try:
        raw = base64.b64decode(value, validate=True)
        key = serialization.load_der_private_key(raw, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or not 2048 <= key.key_size <= 4096:
            raise ValueError()
    except Exception:
        raise rest.FeedError("invalid_netsuite_rsa_private_key", 409) from None
    # Bind the actual public key, not PEM formatting. Only the public digest
    # is passed to the common retained profile; never store the private key.
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    from hashlib import sha256
    return key, sha256(public).hexdigest()


def available(src):
    try:
        key_material(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources": [{"source_alias": alias, "source_sha256": rest._digest(src),
                         "relation": src["event_record"], "account": src["account_domain"],
                         "activities": sorted(src["activity_labels"].values()),
                         "resource_retained_as_pseudonym": src["resource_column"] is not None,
                         "credential_available": available(src)}
                        for alias, src in sorted(catalog().items())],
            "provider_reads_performed": False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value) == cfg["source_sha256"],
            "netsuite_source_definition_changed", 409)
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
        raise rest.FeedError("invalid_netsuite_profile", 422) from None
    require(spec.confirm_configuration and spec.confirm_source_read and spec.confirm_metadata_retention,
            "explicit_netsuite_read_and_retention_required", 403)
    require(spec.name.strip() and all(ord(c) >= 32 and ord(c) != 127
                                     and not 0xD800 <= ord(c) <= 0xDFFF for c in spec.name),
            "invalid_profile_name", 422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now - timedelta(days=366) <= since < now - timedelta(minutes=spec.ingestion_lag_minutes),
            "netsuite_initial_since_outside_reviewed_window", 422)
    cfg = spec.model_dump(exclude={"confirm_configuration", "confirm_source_read", "confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(), initial_since=rest._iso(since))
    _, fingerprint = key_material(source(cfg))
    return rest.create_profile(cfg, fingerprint, now, provider="netsuite")


def cutoff(p, now, start):
    until = now - timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until > start, "no_new_checkpoint_window", 409)
    return until


def origin(src):
    return "https://" + src["account_domain"] + ".suitetalk.api.netsuite.com"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def assertion(src, key, *, now=None):
    """RFC 7518 PS256, exactly one rest_webservices grant, short assertion."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    stamp = int(time.time()) if now is None else now
    header = {"typ": "JWT", "alg": "PS256", "kid": src["certificate_id"]}
    claims = {"iss": src["client_id"], "scope": ["rest_webservices"],
              "aud": origin(src) + TOKEN_PATH, "iat": stamp, "exp": stamp + 120,
              "jti": uuid4().hex}
    parts = [_b64(json.dumps(v, separators=(",", ":"), ensure_ascii=True).encode())
             for v in (header, claims)]
    signing = b".".join(parts)
    signature = key.sign(signing, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                         hashes.SHA256())
    return (signing + b"." + _b64(signature)).decode("ascii")


def micros(stamp):
    delta = stamp - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def query(src, since, until):
    # SuiteQL REST accepts a query string, not an arbitrary parameter map.
    # All identifiers already match the deployment grammar; recheck here.
    record = src["event_record"]
    case, activity, epoch = (src[x] for x in ("case_column", "activity_column", "epoch_micros_column"))
    resource = src["resource_column"]
    require(RECORD.fullmatch(record) and all(COLUMN.fullmatch(x) for x in (case, activity, epoch))
            and (resource is None or COLUMN.fullmatch(resource)), "invalid_netsuite_identifier", 422)
    lower, upper = micros(since), micros(until)
    require(0 <= lower < upper < 10**19, "invalid_netsuite_window", 422)
    # Exact integer epoch micros avoid NetSuite session-local date formatting.
    # Curated producer owns conversion to UTC and durable case/event semantics.
    # Omit the resource column entirely when not authorized.
    select = f"id AS owg_event, {case} AS owg_case, {activity} AS owg_activity, {epoch} AS owg_micros"
    if resource:
        select += f", {resource} AS owg_resource"
    return (f"SELECT {select} FROM {record} WHERE {epoch} >= {lower} AND {epoch} < {upper} "
            f"ORDER BY {epoch}, id")


def literal(value, code, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str) and 1 <= len(value) <= 200 and value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value), code)
    return value


def integer(value, code):
    # NetSuite REST SuiteQL returns numerical field values as strings;
    # reject scientific notation, fractional micros, bools and floats.
    require(isinstance(value, str) and INTEGER.fullmatch(value) is not None, code)
    return int(value)


def project(p, src, item, since, until):
    require(isinstance(item, dict), "netsuite_invalid_row")
    keys = set(FIELDS if src["resource_column"] else FIELDS[:-1])
    require(set(item) - {"links"} == keys, "netsuite_unexpected_row_fields")
    event = integer(item["owg_event"], "netsuite_invalid_event_identity")
    require(event > 0, "netsuite_invalid_event_identity")
    case = literal(item["owg_case"], "netsuite_invalid_case_identity")
    label = item["owg_activity"]
    require(isinstance(label, str) and label in src["activity_labels"], "netsuite_unreviewed_activity")
    epoch = integer(item["owg_micros"], "netsuite_invalid_timestamp")
    require(micros(since) <= epoch < micros(until), "netsuite_row_outside_window")
    try:
        stamp = EPOCH + timedelta(microseconds=epoch)
    except OverflowError:
        raise rest.FeedError("netsuite_invalid_timestamp", 502) from None
    resource = literal(item.get("owg_resource"), "netsuite_invalid_resource", optional=True)
    return (epoch, event), [rest._key(p, "netsuite_event", str(event)),
                            rest._key(p, "netsuite_case", case), src["activity_labels"][label],
                            rest._iso(stamp), rest._key(p, "netsuite_resource", resource) if resource else ""]


class Reader:
    def __init__(self, p, client, credential_check=rest._check):
        self.p, self.client, self.credential_check = p, client, credential_check
        self.src = source(p["config"])
        self.pages = self.bytes = 0
        self.deadline = time.monotonic() + rest.MAX_SECONDS
        self.access_token = None
        self.expiry = 0

    def request(self, path, *, headers, data=None, body=None, params=None):
        require(self.pages < rest.MAX_PAGES, "provider_page_budget_exceeded", 409)
        self.pages += 1
        self.credential_check(self.p)
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "read_time_budget_exceeded", 409)
        try:
            with self.client.stream("POST", origin(self.src) + path, headers=headers,
                                    data=data, json=body, params=params,
                                    timeout=min(20, remaining)) as response:
                if response.status_code == 429:
                    try:
                        delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        delay = 60
                    raise rest.FeedError("provider_rate_limited", 429, delay)
                require(response.status_code == 200, "netsuite_http_failure")
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw) <= rest.MAX_PAGE_BYTES and self.bytes + len(raw) <= rest.MAX_TOTAL_BYTES
                            and time.monotonic() <= self.deadline, "provider_response_budget_exceeded")
                self.bytes += len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure", 502) from None
        value = rest._strict_json(bytes(raw))
        require(isinstance(value, dict), "netsuite_invalid_response")
        return value

    def authenticate(self):
        key = self.credential_check(self.p)
        result = self.request(TOKEN_PATH, headers={"Accept": "application/json"},
                              data={"grant_type": "client_credentials",
                                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                                    "client_assertion": assertion(self.src, key)})
        token = result.get("access_token")
        expires = result.get("expires_in")
        require(isinstance(token, str) and 16 <= len(token) <= 8192
                and all(33 <= ord(c) <= 126 for c in token)
                and isinstance(result.get("token_type"), str)
                and result["token_type"].lower() == "bearer"
                and type(expires) is int and 120 <= expires <= 3600,
                "netsuite_invalid_token_response")
        scope = result.get("scope")
        require(scope in (None, "rest_webservices", ["rest_webservices"]),
                "netsuite_unexpected_token_scope")
        self.access_token = token
        self.expiry = time.monotonic() + expires - 30

    def scan(self, since, until):
        require(self.access_token is not None and self.expiry > time.monotonic(),
                "netsuite_token_expired", 409)
        size = self.p["config"]["page_size"]
        offset = 0
        total = None
        last = None
        rows, seen = [], set()
        sql = query(self.src, since, until)
        while True:
            result = self.request(QUERY_PATH,
                                  headers={"Authorization": "Bearer " + self.access_token,
                                           "Prefer": "transient", "Accept": "application/json"},
                                  body={"q": sql}, params={"limit": size, "offset": offset})
            count, found = result.get("count"), result.get("totalResults")
            more, items = result.get("hasMore"), result.get("items")
            require(type(count) is int and 0 <= count <= size
                    and type(found) is int and 0 <= found <= rest.MAX_ROWS
                    and type(more) is bool and type(result.get("offset")) is int
                    and result["offset"] == offset and isinstance(items, list)
                    and len(items) == count, "netsuite_invalid_page_population")
            if total is None:
                total = found
                # Both complete scans share one page/time/byte budget. Reject
                # before partial retention if the reviewed window cannot fit.
                pages_per_scan = max(1, (total + size - 1) // size)
                require(1 + 2 * pages_per_scan <= rest.MAX_PAGES,
                        "netsuite_window_exceeds_double_scan_budget", 409)
            require(found == total and count == min(size, total - offset)
                    and more == (offset + count < total), "netsuite_page_population_changed")
            for item in items:
                order, row = project(self.p, self.src, item, since, until)
                require(last is None or order > last, "netsuite_unstable_page_order")
                require(row[0] not in seen, "netsuite_duplicate_event_identity")
                last = order
                seen.add(row[0])
                rows.append(row)
            offset += count
            if not more:
                break
        require(offset == total and len(rows) == total, "netsuite_incomplete_scan")
        return rows


def extract(p, since, until, client_factory=httpx.Client):
    with client_factory(timeout=20, follow_redirects=False, trust_env=False) as client:
        reader = Reader(p, client)
        reader.authenticate()
        first = reader.scan(since, until)
        second = reader.scan(since, until)
        require(first == second, "netsuite_repeat_scan_changed")
        reader.credential_check(p)
        return first, {"pages": reader.pages, "records_received": len(first) + len(second),
                       "distinct_records_in_window": len(first), "duplicate_records_in_window": 0,
                       "repeat_scan_agreement": True, "certificate_client_grant_succeeded": True,
                       "independent_role_readback_verified": False, "transaction_snapshot_verified": False,
                       "provider_completeness_verified": False, "raw_source_identifiers_retained": False}
