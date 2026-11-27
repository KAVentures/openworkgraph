"""Customer REST event-feed protocol: bounded read-only pull into retained logs.

Provider endpoint and credentials are deployment-allowlisted. Profiles, leases
and checkpoints live in the existing DuckDB workspace, committed atomically
with the event append. No second event store, raw response cache or provider
write. Immutable mapping and exact source IDs make overlapping retries safe.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from importlib import import_module
from io import BytesIO, StringIO
import json
import os
import re
import secrets
import time
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from . import event_log_workspace as workspace
from shared.enterprise_connector_contract import PROVIDERS, NATIVE_ADAPTERS, capabilities

FORMAT = "openworkgraph.enterprise-rest-feed.v1"
MAX_PROFILES = 20
MAX_PAGES = 20
MAX_ROWS = 10000
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_SECONDS = 90
LEASE_SECONDS = 120
PROFILE_ID = re.compile(r"rest_[a-f0-9]{24}\Z")
ENV = re.compile(r"OWG_REST_TOKEN_[A-Z0-9_]{1,48}\Z")
FIELD = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")]
SCHEMA = """CREATE TABLE IF NOT EXISTS enterprise_rest_profiles (
    profile_id VARCHAR PRIMARY KEY, definition VARCHAR NOT NULL)"""


class FeedError(ValueError):
    def __init__(self, code, status=409, retry_after=0):
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Mapping(Strict):
    event_id: FIELD
    case_id: FIELD
    activity: FIELD
    event_time: FIELD
    change_time: FIELD
    resource: FIELD | None = None

    @model_validator(mode="after")
    def distinct(self):
        names = [self.event_id,self.case_id,self.activity,self.event_time,self.change_time,self.resource]
        # An immutable source may explicitly use event time as change time.
        if self.event_time == self.change_time:
            names.remove(self.change_time)
        names = [n for n in names if n is not None]
        if len(set(names)) != len(names):
            raise ValueError("mapped fields must be distinct except event/change time")
        return self


class Create(Strict):
    name: str = Field(min_length=1,max_length=100)
    endpoint: str = Field(min_length=10,max_length=500)
    credential_env: str = Field(pattern=r"^OWG_REST_TOKEN_[A-Z0-9_]{1,48}$")
    mapping: Mapping
    initial_since: str = Field(min_length=20,max_length=40)
    window_hours: StrictInt = Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt = Field(ge=0,le=1440,default=60)
    page_size: StrictInt = Field(ge=1,le=500,default=200)
    confirm_configuration: StrictBool
    confirm_activity_labels: StrictBool

    @model_validator(mode="after")
    def window(self):
        if self.overlap_minutes >= self.window_hours*60:
            raise ValueError("overlap must be shorter than each advancing window")
        return self


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    if not isinstance(value,str) or not 1<=len(value)<=64:
        raise FeedError("invalid_source_timestamp",422)
    try:
        result = datetime.fromisoformat(value.replace("Z","+00:00"))
        if result.utcoffset() is None or not 1900<=result.year<=2100:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise FeedError("invalid_source_timestamp",422) from None


def _iso(value):
    return value.isoformat().replace("+00:00","Z")


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def _endpoint(value):
    try:
        p = urlsplit(value)
        if (p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment
                or p.port not in (None,443) or not p.path.startswith("/") or p.path=="/"
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,250}",p.hostname)
                or ".." in p.hostname or not re.fullmatch(r"/[A-Za-z0-9_./~-]{1,400}",p.path)
                or any(x in (".","..") for x in p.path.split("/"))):
            raise ValueError()
        normalized = "https://"+p.hostname+p.path
        if normalized != value:
            raise ValueError()
    except (ValueError,TypeError):
        raise FeedError("invalid_fixed_https_endpoint",422) from None
    try:
        allowed = json.loads(os.environ.get("OWG_REST_ALLOWED_ENDPOINTS","[]"))
        if not isinstance(allowed,list) or len(allowed)>100 or any(type(x) is not str for x in allowed):
            raise ValueError()
    except (ValueError,TypeError):
        raise FeedError("invalid_deployment_endpoint_allowlist") from None
    if value not in allowed:
        raise FeedError("endpoint_not_deployment_allowlisted",403)
    return value


def _endpoint_credential(endpoint,reference,*,required=False):
    """Optional exact deployment association; never silently fall back."""
    raw=os.environ.get("OWG_REST_ENDPOINT_CREDENTIALS")
    if required and raw is None:
        raise FeedError("rest_endpoint_credential_catalog_required",403)
    if raw is not None:
        try:
            if len(raw)>65536:
                raise ValueError()
            value=_strict_json(raw.encode())
            if (not isinstance(value,dict) or not 1<=len(value)<=100
                    or any(not isinstance(k,str) or not isinstance(v,str) or not ENV.fullmatch(v)
                           for k,v in value.items())):
                raise ValueError()
            for k in value:
                _endpoint(k)
        except (FeedError,ValueError,TypeError):
            raise FeedError("invalid_rest_endpoint_credential_catalog",403) from None
        if value.get(endpoint)!=reference:
            raise FeedError("rest_endpoint_credential_pair_not_approved",403)
    return _credential(reference)


def resolve_secret(reference):
    from .enterprise_connector_secrets import resolve, SecretError
    try:
        return resolve(reference)
    except SecretError as error:
        raise FeedError(str(error)) from None


def _credential(env):
    if not isinstance(env,str) or not ENV.fullmatch(env):
        raise FeedError("invalid_credential_reference")
    value = resolve_secret(env)
    if not 1<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise FeedError("credential_unavailable")
    return value


def _key(p, domain, value):
    return hmac.new(bytes.fromhex(p["key"]), (domain+"\0"+value).encode(),sha256).hexdigest()


def _adapter(provider):
    if provider=="rest":
        return None
    module=NATIVE_ADAPTERS.get(provider) if isinstance(provider,str) else None
    if module is None:
        raise FeedError("unknown_enterprise_connector_provider",422)
    return import_module("."+module,__package__)


def _check_binding(p):
    adapter=_adapter(p.get("provider","rest"))
    if adapter is not None:
        return adapter.check_binding(p)
    endpoint=_endpoint(p["config"]["endpoint"])
    token = _endpoint_credential(endpoint,p["config"]["credential_env"],required=p["config"].get("credential_pair_policy") is True)
    if not hmac.compare_digest(_key(p,"credential",token),p["credential_binding"]):
        raise FeedError("credential_changed_reconfigure_required")
    return token


def _connect():
    conn=workspace._connect()
    conn.execute(SCHEMA)
    from . import enterprise_ingestion_run_history as history
    history.init(conn)
    from . import enterprise_ingestion_coverage as coverage
    coverage.init(conn)
    return conn


def _get(conn,profile_id):
    if not isinstance(profile_id,str) or not PROFILE_ID.fullmatch(profile_id):
        raise FeedError("unknown_rest_profile",404)
    row=conn.execute("SELECT definition FROM enterprise_rest_profiles WHERE profile_id=?",[profile_id]).fetchone()
    if row is None:
        raise FeedError("unknown_rest_profile",404)
    return json.loads(row[0])


def _put(conn,p):
    conn.execute("INSERT OR REPLACE INTO enterprise_rest_profiles VALUES (?,?)",
                 [p["profile_id"],json.dumps(p,separators=(",",":"),allow_nan=False)])


def _public(p,now):
    out={k:p[k] for k in ("profile_id","revision","enabled","created_at","status","log_id",
                           "checkpoint","last_success_at","last_attempt_at","last_result","retry_not_before")}
    out["integrity_hold"] = p.get("integrity_hold")
    out["excluded_case_count"] = len(p.get("excluded_case_keys", []))
    out["config"] = p["config"]
    out["provider"] = p.get("provider", "rest")
    out["adapter_capabilities"]=capabilities(out["provider"]) if out["provider"] in PROVIDERS else None
    if p.get("provider")=="sql":
        out["source_schema_sha256"]=p.get("source_schema_sha256")
    if p["status"]=="running" and p["lease_until"]<=now:
        out["status"]="interrupted"
    try:
        _check_binding(p)
        out["credential_binding_available"]=True
    except FeedError:
        out["credential_binding_available"]=False
    from .enterprise_connector_rotation import metadata
    out["credential_management"]=metadata(p)
    from .enterprise_connector_history import public_status
    out.update(public_status(p,now))
    out.update(provider_live_health="unknown", source_completeness_verified=False,
               credentials_exposed=False, execution_authorized=False)
    return out


def inventory():
    with workspace._LOCK:
        c=_connect()
        try:
            rows=c.execute("SELECT definition FROM enterprise_rest_profiles ORDER BY profile_id").fetchall()
            return {"format":FORMAT,"profiles":[_public(json.loads(r[0]),time.time()) for r in rows],
                    "max_profiles":MAX_PROFILES,"provider_reads_performed":False}
        finally:
            c.close()


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise FeedError("invalid_rest_profile",422) from None
    if spec.confirm_configuration is not True or spec.confirm_activity_labels is not True:
        raise FeedError("explicit_configuration_and_label_retention_required",403)
    endpoint=_endpoint(spec.endpoint)
    token=_endpoint_credential(endpoint,spec.credential_env)
    since=_stamp(spec.initial_since)
    now=_now()
    if not now-timedelta(days=366)<=since<=now:
        raise FeedError("initial_since_must_be_within_last_366_days",422)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 for c in spec.name):
        raise FeedError("invalid_profile_name",422)
    config=spec.model_dump(exclude={"confirm_configuration","confirm_activity_labels"})
    config.update(name=spec.name.strip(),endpoint=endpoint,initial_since=_iso(since))
    if "OWG_REST_ENDPOINT_CREDENTIALS" in os.environ:
        config["credential_pair_policy"]=True
    return create_profile(config,token,now)


def create_profile(config,identity,now,*,provider="rest"):
    """Internal factory: provider adapters validate configuration first."""
    if provider not in PROVIDERS:
        raise FeedError("unknown_enterprise_connector_provider",422)
    p={"profile_id":"rest_"+secrets.token_hex(12),"revision":1,"enabled":True,"provider":provider,
       "key":secrets.token_hex(32),"config":config,"created_at":_iso(now),"status":"ready",
       "log_id":None,"checkpoint":None,"last_success_at":None,"last_attempt_at":None,
       "last_result":None,"run_id":None,"lease_until":0,"retry_not_before":0}
    p["credential_binding"]=_key(p,"credential",identity)
    with workspace._LOCK:
        c=_connect()
        try:
            if c.execute("SELECT count(*) FROM enterprise_rest_profiles").fetchone()[0]>=MAX_PROFILES:
                raise FeedError("rest_profile_limit")
            _put(c,p)
        finally:
            c.close()
    return _public(p,time.time())


def change(profile_id,revision,enabled):
    if type(revision) is not int or type(enabled) is not bool:
        raise FeedError("exact_revision_and_enabled_required",422)
    with workspace._LOCK:
        c=_connect()
        try:
            p=_get(c,profile_id)
            if revision!=p["revision"]:
                raise FeedError("profile_changed")
            if enabled:
                if p.get("integrity_hold"):
                    raise FeedError("retained_integrity_hold_requires_reviewed_release")
                if p["status"]=="dataset_deleted":
                    raise FeedError("retained_dataset_deleted_create_new_profile")
                _check_binding(p)
            from . import enterprise_ingestion_run_history as history
            c.execute("BEGIN")
            try:
                history.invalidate(c,p,"profile_revision_changed")
                if p.get("historical_active"):
                    from .enterprise_connector_history import finalize
                    finalize(p,outcome="uncertain",code="historical_run_invalidated_by_profile_change")
                p.update(revision=revision+1,enabled=enabled,run_id=None,lease_until=0,
                         status="ready" if enabled else ("dataset_deleted" if p["status"]=="dataset_deleted" else "disabled"))
                _put(c,p)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return _public(p,time.time())
        finally:
            c.close()


def delete(profile_id,revision):
    if type(revision) is not int:
        raise FeedError("exact_revision_required",422)
    with workspace._LOCK:
        c=_connect()
        try:
            p=_get(c,profile_id)
            if p["revision"]!=revision:
                raise FeedError("profile_changed")
            from . import enterprise_ingestion_run_history as history
            c.execute("BEGIN")
            try:
                history.erase(c,profile_id)
                from . import enterprise_ingestion_coverage as coverage
                coverage.erase(c,profile_id)
                c.execute("DELETE FROM enterprise_rest_profiles WHERE profile_id=?",[profile_id])
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return {"deleted":True,"retained_log_id":p["log_id"],"retained_log_deleted":False}
        finally:
            c.close()


def forget_dataset(conn,log_id):
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name='enterprise_rest_profiles'").fetchone():
        return
    for (definition,) in conn.execute("SELECT definition FROM enterprise_rest_profiles").fetchall():
        p=json.loads(definition)
        if p["log_id"]==log_id:
            from . import enterprise_ingestion_run_history as history
            history.erase(conn,p["profile_id"])
            from . import enterprise_ingestion_coverage as coverage
            coverage.erase(conn,p["profile_id"])
            p.pop("integrity_hold",None)
            p.pop("excluded_case_keys",None)
            p.pop("historical_active",None)
            p.pop("historical_receipts",None)
            p.update(enabled=False,status="dataset_deleted",revision=p["revision"]+1,run_id=None,lease_until=0,
                     log_id=None,checkpoint=None,last_result=None,last_success_at=None,
                     source_schema_sha256=None)
            _put(conn,p)


def _guard(conn,p):
    live=_get(conn,p["profile_id"])
    if (live.get("integrity_hold") or not live["enabled"] or live["revision"]!=p["revision"] or live["run_id"]!=p["run_id"]
            or live["lease_until"]<=time.time() or live["log_id"]!=p["log_id"]):
        raise FeedError("rest_run_invalidated")
    if live["log_id"]:
        workspace._check_log(conn,live["log_id"])
    return _check_binding(live)


def _check(p):
    with workspace._LOCK:
        c=_connect()
        try:
            return _guard(c,p)
        finally:
            c.close()


def _begin(profile_id,revision,now):
    with workspace._LOCK:
        c=_connect()
        try:
            p=_get(c,profile_id)
            if p.get("integrity_hold"):
                raise FeedError("retained_integrity_hold_requires_reviewed_release")
            if p["revision"]!=revision or not p["enabled"]:
                raise FeedError("profile_changed_or_disabled")
            if p.get("historical_active"):
                raise FeedError("historical_run_requires_completion_or_reconciliation")
            if p["run_id"] and p["lease_until"]>time.time():
                raise FeedError("rest_run_in_progress")
            if p["retry_not_before"]>time.time():
                raise FeedError("provider_retry_cooldown")
            _check_binding(p)
            if p["log_id"]:
                workspace._check_log(c,p["log_id"])
            start=_stamp(p["checkpoint"] or p["config"]["initial_since"])
            attempted_at=now
            provider=p.get("provider","rest")
            adapter=_adapter(provider)
            if provider=="google":
                now=adapter.review_cutoff(p,now,start)
            elif adapter is not None and provider!="sql":
                now=adapter.cutoff(p,now,start)
            if now<start:
                raise FeedError("clock_before_checkpoint")
            until=min(now,start+timedelta(hours=p["config"]["window_hours"]))
            since=start-timedelta(minutes=p["config"]["overlap_minutes"]) if p["checkpoint"] else start
            if until<=start:
                raise FeedError("no_new_checkpoint_window")
            from . import enterprise_ingestion_run_history as history
            previous_run_id=p["run_id"]
            p.update(run_id=secrets.token_hex(16),lease_until=time.time()+LEASE_SECONDS,
                     status="running",last_attempt_at=_iso(attempted_at))
            c.execute("BEGIN")
            try:
                _put(c,p)
                history.start(c,p,_iso(since),_iso(until),previous_run_id=previous_run_id)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return p,since,until
        finally:
            c.close()


def _text(value,max_length):
    if (not isinstance(value,str) or not value.strip() or len(value)>max_length
            or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF or ord(c) in (0xFFFE,0xFFFF) for c in value)):
        raise FeedError("invalid_mapped_source_field",422)
    return value


def _project(record,p,since,until):
    if not isinstance(record,dict):
        raise FeedError("invalid_source_record",422)
    m=p["config"]["mapping"]
    try:
        event=_text(record[m["event_id"]],200)
        case=_text(record[m["case_id"]],200)
        activity=_text(record[m["activity"]],160)
        event_at=_stamp(record[m["event_time"]])
        changed=_stamp(record[m["change_time"]])
        if not since<=changed<until:
            raise FeedError("source_record_outside_requested_change_window",422)
        resource=_key(p,"resource",_text(record[m["resource"]],200)) if m["resource"] else ""
    except KeyError:
        raise FeedError("missing_mapped_source_field",422) from None
    return [_key(p,"event",event),_key(p,"case",case),activity,_iso(event_at),resource]


def _strict_json(raw):
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:
                raise ValueError()
            obj[k]=v
        return obj
    def invalid(_):
        raise ValueError()
    try:
        return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)
    except (ValueError,UnicodeError,RecursionError):
        raise FeedError("invalid_provider_json",502) from None


def _extract(p,since,until,client_factory):
    cursor=None
    seen=set()
    rows={}
    total_bytes=0
    received=0
    duplicates=0
    deadline=time.monotonic()+MAX_SECONDS
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        for page in range(1,MAX_PAGES+1):
            token=_check(p)
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise FeedError("read_time_budget_exceeded")
            params={"updated_since":_iso(since),"updated_before":_iso(until),"limit":p["config"]["page_size"]}
            if cursor is not None:
                params["cursor"]=cursor
            try:
                with client.stream("GET",p["config"]["endpoint"],params=params,
                    headers={"Authorization":"Bearer "+token,"Accept":"application/json"},
                    timeout=min(10,remaining)) as response:
                    if response.status_code in (401,403):
                        raise FeedError("source_access_denied",502)
                    if response.status_code==429:
                        try:
                            delay=max(60,min(86400,int(response.headers.get("Retry-After","60"))))
                        except ValueError:
                            delay=60
                        raise FeedError("provider_rate_limited",429,delay)
                    if response.status_code!=200:
                        raise FeedError("provider_http_failure",502)
                    raw=bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if time.monotonic()>deadline:
                            raise FeedError("read_time_budget_exceeded")
                        raw.extend(chunk)
                        if len(raw)>MAX_PAGE_BYTES or total_bytes+len(raw)>MAX_TOTAL_BYTES:
                            raise FeedError("provider_response_budget_exceeded")
                    total_bytes+=len(raw)
            except httpx.HTTPError:
                raise FeedError("provider_transport_failure",502) from None
            body=_strict_json(bytes(raw))
            if not isinstance(body,dict) or set(body)!={"records","next_cursor"} or not isinstance(body["records"],list):
                raise FeedError("invalid_provider_page_contract",502)
            if len(body["records"])>p["config"]["page_size"]:
                raise FeedError("provider_page_size_exceeded")
            received+=len(body["records"])
            if received>MAX_ROWS:
                raise FeedError("provider_row_budget_exceeded")
            for record in body["records"]:
                row=_project(record,p,since,until)
                if row[0] in rows:
                    if rows[row[0]]!=row:
                        raise FeedError("conflicting_source_event_identity",422)
                    duplicates+=1
                rows[row[0]]=row
            cursor=body["next_cursor"]
            if cursor is None:
                return list(rows.values()),{"pages":page,"records_received":received,"duplicate_records_in_window":duplicates}
            if (not isinstance(cursor,str) or not 1<=len(cursor)<=1024
                    or any(ord(c)<33 or ord(c)>126 for c in cursor) or cursor in seen):
                raise FeedError("invalid_or_repeated_provider_cursor",502)
            seen.add(cursor)
    raise FeedError("provider_page_budget_exceeded")


def _retain(p,rows,counts,until):
    from . import enterprise_case_exclusions as exclusions
    rows,excluded=exclusions.filter_rows(p,rows)
    counts=dict(counts)
    counts["excluded_case_events_in_window"]=excluded
    principal_pin=counts.pop("_slack_principal_pin",None)
    stream=StringIO(newline="")
    writer=csv.writer(stream,lineterminator="\n")
    writer.writerow(["event","case","activity","time","resource"])
    writer.writerows(rows)
    mapping={"event_id_column":"event","case_column":"case","activity_column":"activity",
             "timestamp_column":"time","resource_column":"resource","source_namespace":p["profile_id"]}
    def checkpoint(conn,log_id):
        _guard(conn,p)
        if rows and p["log_id"]:
            added,duplicates=conn.execute("""SELECT added_events,duplicate_events FROM log_appends
                WHERE log_id=? ORDER BY batch DESC LIMIT 1""",[log_id]).fetchone()
        elif rows:
            added=conn.execute("SELECT events FROM logs WHERE log_id=?",[log_id]).fetchone()[0]
            duplicates=0
        else:
            added=duplicates=0
        saved=dict(p)
        if p.get("historical_active"):
            from .enterprise_connector_history import finalize
            saved["log_id"]=log_id
            finalize(saved,outcome="succeeded",added=added,duplicates=duplicates,
                     observed=len(rows)+excluded,excluded=excluded)
        else:
            saved.update(log_id=log_id,checkpoint=_iso(until),last_success_at=_iso(_now()),status="read_retained",
                         run_id=None,lease_until=0,retry_not_before=0,
                         last_result={**counts,"outcome":"succeeded","distinct_events_in_window":len(rows),
                                      "events_added":added,"duplicates_skipped":duplicates})
        if p.get("provider")=="sql":
            saved["source_schema_sha256"]=counts["source_schema_sha256"]
        if p.get("provider")=="slack":
            if not principal_pin:
                raise FeedError("slack_principal_pin_missing")
            saved["slack_principal_pin"]=principal_pin
        _put(conn,saved)
        from . import enterprise_ingestion_run_history as history
        from . import enterprise_ingestion_coverage as coverage
        if p.get("historical_active"):
            coverage.commit_historical(conn,p,until)
        else:
            coverage.commit(conn,p,until)
            history.finish(conn,p["profile_id"],p["run_id"],"succeeded",
                           checkpoint=saved["checkpoint"],counts=saved["last_result"])
    with workspace._LOCK:
        _check(p)
        if rows:
            source=BytesIO(stream.getvalue().encode())
            if p["log_id"]:
                result=workspace.append_log(p["log_id"],source,source_format="csv",mapping=mapping,
                                            kind="enterprise_rest_feed",_transaction_hook=checkpoint)
                added=result["added_events"]
                duplicates=result["duplicate_events_skipped"]
            else:
                result=workspace.import_log(source,name={"google":"Google Workspace audit ","sql":"Enterprise SQL ","slack":"Slack activity ","salesforce":"Salesforce field history ","servicenow":"ServiceNow operations ","sap":"SAP procurement day evidence ","dataverse":"Dataverse audit ","hubspot":"HubSpot property history ","databricks":"Databricks event view ","snowflake":"Snowflake event view ","netsuite":"NetSuite event record ","workday":"Workday RaaS event report ","oracle_fusion":"Oracle Fusion procurement ","bigquery":"BigQuery event view ","zendesk":"Zendesk ticket audit "}.get(p.get("provider"),"Enterprise REST ")+p["profile_id"],source_format="csv",
                                            mapping=mapping,_transaction_hook=checkpoint)
                added=result["events"]
                duplicates=0
        else:
            c=_connect()
            try:
                c.execute("BEGIN")
                try:
                    checkpoint(c,p["log_id"])
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise
            finally:
                c.close()
            added=duplicates=0
        c=_connect()
        try:
            live=_get(c,p["profile_id"])
            if live["revision"]!=p["revision"] or not live["enabled"]:
                raise FeedError("rest_run_invalidated")
            return {**_public(live,time.time()),"events_added":added,"duplicates_skipped":duplicates}
        finally:
            c.close()


def _fail(p,error):
    with workspace._LOCK:
        c=_connect()
        try:
            try:
                live=_get(c,p["profile_id"])
            except FeedError:
                return
            # The checkpoint hook may have already committed before a later
            # quality-report error. Never rewrite that committed success.
            if live["run_id"]!=p["run_id"] or live["revision"]!=p["revision"]:
                return
            historical=bool(live.get("historical_active"))
            if historical:
                from .enterprise_connector_history import finalize
                finalize(live,outcome="failed",code=error.code)
                if error.retry_after:
                    live["retry_not_before"]=max(live["retry_not_before"],time.time()+error.retry_after)
            else:
                live.update(status="failed",run_id=None,lease_until=0,
                            retry_not_before=max(live["retry_not_before"],time.time()+error.retry_after) if error.retry_after else live["retry_not_before"],
                            last_result={"outcome":"failed","error":error.code})
            from . import enterprise_ingestion_run_history as history
            c.execute("BEGIN")
            try:
                _put(c,live)
                if not historical:
                    history.finish(c,p["profile_id"],p["run_id"],"failed",error=error.code,
                                   retry_not_before=live["retry_not_before"])
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        finally:
            c.close()


def _extract_window(p,since,until,client_factory):
    provider=p.get("provider","rest")
    adapter=_adapter(provider)
    if adapter is None:
        return _extract(p,since,until,client_factory)
    if provider=="sql":
        return adapter.extract(p,since,until)
    return adapter.extract(p,since,until,client_factory)


def run(profile_id,revision,*,confirm_reads,confirm_retention,client_factory=httpx.Client,now=None):
    if confirm_reads is not True or confirm_retention is not True:
        raise FeedError("explicit_source_read_and_retention_required",403)
    if type(revision) is not int:
        raise FeedError("exact_revision_required",422)
    p,since,until=_begin(profile_id,revision,now or _now())
    try:
        rows,counts=_extract_window(p,since,until,client_factory)
        return _retain(p,rows,counts,until)
    except FeedError as error:
        _fail(p,error)
        raise
    except Exception:
        error=FeedError("retention_or_source_failed_inspect_checkpoint")
        _fail(p,error)
        raise error from None


def schedule_pin(profile_id):
    """No provider access; existing scheduler pins this exact configuration."""
    with workspace._LOCK:
        c=_connect()
        try:
            p=_get(c,profile_id)
            if p.get("integrity_hold"):
                raise FeedError("retained_integrity_hold_requires_reviewed_release")
            if not p["enabled"]:
                raise FeedError("rest_profile_disabled")
            _check_binding(p)
            return p["revision"]
        finally:
            c.close()


def run_scheduled(profile_id,revision):
    try:
        if type(revision) is not int or schedule_pin(profile_id)!=revision:
            return "paused:rest_profile_changed",0.0
        result=run(profile_id,revision,confirm_reads=True,confirm_retention=True)
        return "succeeded:"+str(result["events_added"])+"_events_added",0.0
    except FeedError as error:
        if error.code in {"unknown_rest_profile","rest_profile_disabled","retained_integrity_hold_requires_reviewed_release","profile_changed_or_disabled",
                           "credential_changed_reconfigure_required","rest_run_invalidated",
                           "endpoint_not_deployment_allowlisted","invalid_rest_endpoint_credential_catalog","rest_endpoint_credential_pair_not_approved","rest_endpoint_credential_catalog_required",
                           "unknown_enterprise_connector_provider","invalid_connector_secret_catalog","invalid_connector_secret_root",
                           "unsafe_connector_secret_ancestor","connector_secret_root_must_be_owner_only","unsafe_connector_secret_file",
                           "connector_secret_file_unavailable","connector_secret_changed_during_read","invalid_connector_secret_file_content",
                           "connector_secret_files_require_posix","google_grant_unavailable","google_grant_scope_mismatch","google_reconnect_required",
                           "hubspot_source_definition_changed","hubspot_required_scope_missing","hubspot_private_token_unavailable","hubspot_account_app_or_scope_mismatch",
                           "dataverse_source_definition_changed","dataverse_secret_unavailable","dataverse_application_auth_failed","dataverse_application_identity_mismatch",
                           "sap_source_definition_changed","sap_credential_unavailable","sap_metadata_changed_requires_review",
                           "servicenow_grant_unavailable","servicenow_grant_scope_mismatch","servicenow_reconnect_required",
                           "zendesk_grant_unavailable","zendesk_grant_scope_mismatch","zendesk_reconnect_required","zendesk_principal_mismatch","zendesk_principal_not_active_agent",
                           "salesforce_grant_unavailable","salesforce_grant_scope_mismatch","salesforce_reconnect_required","salesforce_principal_mismatch",
                           "slack_credential_unavailable","slack_enterprise_mismatch","slack_workspace_mismatch","slack_channel_scope_not_verified","slack_principal_changed_during_read",
                           "sql_source_not_deployment_allowlisted","sql_source_definition_changed",
                           "sql_source_schema_changed_requires_new_profile","sql_mapped_column_missing_or_type_changed"}:
            return "paused:rest_profile_changed",0.0
        if error.code=="no_new_checkpoint_window":
            return "skipped_no_new_window",0.0
        return "failed:"+error.code,float(error.retry_after)
