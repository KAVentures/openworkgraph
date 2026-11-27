"""Native, deployment-allowlisted SQL event feeds using the shared feed lifecycle.

No caller-supplied SQL. A deployment owner declares an exact relation and
column mapping; only those columns and a parameterized time window are read.
PostgreSQL uses verified TLS and a read-only repeatable-read server cursor.
SQLite uses a read-only file and a strict table with integer UTC microseconds.
Source data is never persisted outside the existing retained event-log path.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest

FORMAT = "openworkgraph.enterprise-native-sql-feed.v1"
ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}\Z")
IDENT = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]
ENV_NAME = re.compile(r"OWG_SQL_DSN_[A-Z0-9_]{1,48}\Z")
MAX_CATALOG = 50
MAX_CATALOG_BYTES = 65536
MAX_DSN = 8192
MAX_READ_SECONDS = 75
STATEMENT_MS = 10000
EPOCH = datetime(1970,1,1,tzinfo=timezone.utc)
PG_IDS = {"text","varchar","bpchar","int2","int4","int8","uuid"}
PG_LABELS = {"text","varchar","bpchar"}
SQLITE_IDS = {"TEXT","INTEGER"}


class Mapping(rest.Strict):
    event_id: IDENT
    case_id: IDENT
    activity: IDENT
    event_time: IDENT
    change_time: IDENT
    resource: IDENT | None = None

    @model_validator(mode="after")
    def distinct(self):
        names=[self.event_id,self.case_id,self.activity,self.event_time,self.change_time,self.resource]
        if self.event_time==self.change_time:
            names.remove(self.change_time)
        names=[n for n in names if n is not None]
        if len(set(names))!=len(names):
            raise ValueError("mapped fields must be distinct except event/change time")
        return self


class Source(rest.Strict):
    engine: Literal["postgresql","sqlite"]
    relation: IDENT
    mapping: Mapping
    schema_name: IDENT | None = None
    dsn_env: str | None = Field(default=None,max_length=64)
    database: str | None = Field(default=None,min_length=1,max_length=63)
    role: str | None = Field(default=None,min_length=1,max_length=63)
    path: str | None = Field(default=None,max_length=1000)

    @model_validator(mode="after")
    def engine_contract(self):
        if self.engine=="postgresql":
            if (not self.schema_name or not self.database or not self.role or not self.dsn_env
                    or not ENV_NAME.fullmatch(self.dsn_env) or self.path is not None
                    or self.schema_name.startswith("pg_") or self.schema_name=="information_schema"):
                raise ValueError("PostgreSQL needs exact application schema, database, role and DSN reference")
        elif (not self.path or any(v is not None for v in
                    (self.schema_name,self.dsn_env,self.database,self.role))):
            raise ValueError("SQLite needs an exact deployment-approved path and table")
        return self


class Create(rest.Strict):
    name: str = Field(min_length=1,max_length=100)
    source_alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_since: str = Field(min_length=20,max_length=40)
    window_hours: StrictInt = Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt = Field(ge=0,le=1440,default=60)
    page_size: StrictInt = Field(ge=1,le=500,default=500)
    confirm_configuration: StrictBool
    confirm_activity_labels: StrictBool

    @model_validator(mode="after")
    def window(self):
        if self.overlap_minutes>=self.window_hours*60:
            raise ValueError("overlap must be shorter than advancing window")
        return self


def _catalog():
    raw=os.environ.get("OWG_SQL_SOURCE_CATALOG","{}")
    if len(raw.encode("utf-8"))>MAX_CATALOG_BYTES:
        raise rest.FeedError("sql_catalog_too_large")
    try:
        value=rest._strict_json(raw.encode())
        if not isinstance(value,dict) or len(value)>MAX_CATALOG:
            raise ValueError()
        if any(not ALIAS.fullmatch(k) for k in value):
            raise ValueError()
        return {k:Source.model_validate(v) for k,v in value.items()}
    except (ValidationError,ValueError,TypeError,UnicodeError):
        raise rest.FeedError("invalid_sql_deployment_catalog") from None


def _public_source(alias,source):
    # The reviewed definition pin contains no secrets, paths or DSN names.
    public={"source_alias":alias,"engine":source.engine,"relation":source.relation,
            "schema_name":source.schema_name,"mapping":source.mapping.model_dump(),
            "timestamp_storage":"timestamptz" if source.engine=="postgresql" else "utc_epoch_microseconds"}
    # A full-definition digest pins the actual deployment-selected source;
    # no credential value is part of the unkeyed review digest.
    public["source_sha256"]=rest._digest(source.model_dump())
    public["driver_available"]=source.engine=="sqlite" or find_spec("psycopg") is not None
    return public


def catalog():
    """Passive, local configuration only. No DB connections or file reads."""
    return {"format":FORMAT,"sources":[_public_source(k,v) for k,v in sorted(_catalog().items())],
            "provider_reads_performed":False,"credentials_exposed":False}


def _source(alias):
    value=_catalog().get(alias)
    if value is None:
        raise rest.FeedError("sql_source_not_deployment_allowlisted",403)
    return value


def _postgres_params(source):
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        raise rest.FeedError("sql_postgresql_driver_unavailable") from None
    dsn=os.environ.get(source.dsn_env,"")
    if not dsn or len(dsn)>MAX_DSN:
        raise rest.FeedError("sql_credential_unavailable")
    try:
        params=conninfo_to_dict(dsn)
    except Exception:
        raise rest.FeedError("invalid_sql_connection_definition") from None
    # Explicit, single TLS-verified destination; no service, socket,
    # arbitrary startup options, alternate hosts, or implicit role/database.
    allowed={"host","port","dbname","user","password","sslmode","sslrootcert"}
    if (set(params)-allowed or params.get("dbname")!=source.database or params.get("user")!=source.role
            or params.get("sslmode")!="verify-full" or not params.get("sslrootcert")
            or not params.get("password") or not params.get("host")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}",params["host"])
            or "," in params["host"] or ".." in params["host"]):
        raise rest.FeedError("sql_postgresql_requires_exact_tls_verified_read_role")
    if params.get("port") and (not re.fullmatch(r"[0-9]{1,5}",params["port"])
                               or not 1<=int(params["port"])<=65535):
        raise rest.FeedError("invalid_sql_connection_definition")
    return params,dsn


def _sqlite_path(source):
    try:
        path=Path(source.path)
        if not path.is_absolute():
            raise ValueError()
        resolved=path.resolve(strict=True)
        if path!=resolved:
            raise ValueError()
        st=resolved.stat()
        if not stat.S_ISREG(st.st_mode):
            raise ValueError()
        from . import event_log_workspace as workspace
        if resolved.is_relative_to(Path(workspace.DATA_DIR).resolve()):
            raise ValueError()
    except (OSError,ValueError,RuntimeError):
        raise rest.FeedError("sql_readonly_file_unavailable") from None
    # File replacement is an explicit new source, not a continuation.
    return resolved,[st.st_dev,st.st_ino]


def _target(source):
    if source.engine=="postgresql":
        params,dsn=_postgres_params(source)
        return params,rest._digest(source.model_dump())+"\0"+dsn
    path,identity=_sqlite_path(source)
    return path,rest._digest(source.model_dump())+"\0"+json.dumps(identity)


def check_binding(p):
    cfg=p["config"]
    source=_source(cfg["source_alias"])
    current=_public_source(cfg["source_alias"],source)
    if (cfg["engine"]!=source.engine or cfg["source_sha256"]!=current["source_sha256"]):
        raise rest.FeedError("sql_source_definition_changed")
    target,identity=_target(source)
    if not hmac.compare_digest(rest._key(p,"credential",identity),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return source,target


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_sql_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_activity_labels:
        raise rest.FeedError("explicit_sql_configuration_and_retention_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    source=_source(spec.source_alias)
    public=_public_source(spec.source_alias,source)
    if spec.expected_source_sha256!=public["source_sha256"]:
        raise rest.FeedError("sql_source_definition_changed")
    _,identity=_target(source)
    now=rest._now()
    since=rest._stamp(spec.initial_since)
    if not now-timedelta(days=366)<=since<now:
        raise rest.FeedError("initial_since_must_be_within_last_366_days",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_activity_labels","expected_source_sha256"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),
               engine=source.engine,source_sha256=public["source_sha256"])
    return rest.create_profile(cfg,identity,now,provider="sql")


def _quote(name):
    # Identifiers have already passed the strict ASCII declaration grammar.
    # Quote even reserved words, mixed case and all qualified components.
    if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}",name):
        raise rest.FeedError("invalid_sql_identifier")
    return '"'+name+'"'


def _columns(source):
    m=source.mapping
    return [m.event_id,m.case_id,m.activity,m.event_time,m.change_time]+([m.resource] if m.resource else [])


def _relation(source):
    return _quote(source.schema_name if source.engine=="postgresql" else "main")+"."+_quote(source.relation)


def _query(source):
    placeholder="%s" if source.engine=="postgresql" else "?"
    m=source.mapping
    cols=", ".join(_quote(c) for c in _columns(source))
    changed=_quote(m.change_time)
    # A single snapshot cursor, no offset or keyset skipping of tied times.
    return ("SELECT "+cols+" FROM "+_relation(source)+" WHERE "+changed+" >= "+placeholder+
            " AND "+changed+" < "+placeholder+" ORDER BY "+changed+", "+_quote(m.event_id)+
            " LIMIT "+placeholder)


def _micros(at):
    delta=at-EPOCH
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def _timestamp(value,engine):
    try:
        if engine=="sqlite":
            if type(value) is not int:
                raise ValueError()
            value=EPOCH+timedelta(microseconds=value)
        if not isinstance(value,datetime) or value.utcoffset() is None or not 1900<=value.year<=2100:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise rest.FeedError("invalid_sql_timestamp",422) from None


def _identity(value,allow_null=False):
    if value is None and allow_null:
        return None
    if type(value) is int or isinstance(value,UUID):
        value=str(value)
    return rest._text(value,200)


def _project(p,source,row,since,until):
    if not isinstance(row,(tuple,list)) or len(row)!=len(_columns(source)):
        raise rest.FeedError("invalid_sql_row_shape")
    event,case,activity,event_time,changed,*resource=row
    event=_identity(event)
    case=_identity(case)
    activity=rest._text(activity,160)
    event_time=_timestamp(event_time,source.engine)
    changed=_timestamp(changed,source.engine)
    if not since<=changed<until:
        raise rest.FeedError("sql_row_outside_requested_change_window")
    actor=_identity(resource[0],allow_null=True) if resource else None
    return [rest._key(p,"event",event),rest._key(p,"case",case),activity,
            rest._iso(event_time),rest._key(p,"resource",actor) if actor is not None else ""]


def _validate_types(source,columns):
    m=source.mapping
    def allowed(name,types):
        if name not in columns or columns[name][0] not in types:
            raise rest.FeedError("sql_mapped_column_missing_or_type_changed")
    ids=PG_IDS if source.engine=="postgresql" else SQLITE_IDS
    labels=PG_LABELS if source.engine=="postgresql" else {"TEXT"}
    stamps={"timestamptz"} if source.engine=="postgresql" else {"INTEGER"}
    for n in (m.event_id,m.case_id,m.resource):
        if n is not None:
            allowed(n,ids)
    allowed(m.activity,labels)
    for n in (m.event_time,m.change_time):
        allowed(n,stamps)
    # Pin only selected column metadata, not unrelated source fields.
    return rest._digest([(n,*columns[n]) for n in _columns(source)])


def _schema_guard(p,fingerprint):
    prior=p.get("source_schema_sha256")
    if prior is not None and prior!=fingerprint:
        raise rest.FeedError("sql_source_schema_changed_requires_new_profile")


def _budget(deadline):
    if time.monotonic()>=deadline:
        raise rest.FeedError("sql_read_time_budget_exceeded")


@contextmanager
def _sqlite_read(p,source,path,since,until,deadline):
    conn=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=5)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
        conn.execute("BEGIN")
        # A STRICT ordinary table with a non-null INTEGER change time is
        # required: dynamically typed text/null timestamps can silently fall
        # outside a numeric window. No attached databases or virtual tables.
        tables=conn.execute("PRAGMA main.table_list").fetchall()
        found=[r for r in tables if r[1]==source.relation]
        if len(found)!=1 or found[0][2]!="table" or found[0][5]!=1:
            raise rest.FeedError("sql_sqlite_requires_strict_ordinary_table")
        cols=conn.execute("PRAGMA main.table_xinfo("+_quote(source.relation)+")").fetchall()
        meta={r[1]:(r[2].upper(),bool(r[3] or r[5]),int(r[6])) for r in cols if r[6]==0}
        fingerprint=_validate_types(source,meta)
        if not meta[source.mapping.change_time][1]:
            raise rest.FeedError("sql_sqlite_change_time_must_be_not_null")
        _schema_guard(p,fingerprint)
        _budget(deadline)
        cursor=conn.execute(_query(source),(_micros(since),_micros(until),rest.MAX_ROWS+1))
        with closing(cursor):
            yield cursor,fingerprint
    finally:
        conn.close()


def _open_postgres(params):
    import psycopg
    return psycopg.connect(**params,connect_timeout=5,application_name="OpenWorkGraph audit feed",
                           options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=5000")


@contextmanager
def _postgres_read(p,source,params,since,until,deadline):
    with _open_postgres(params) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL search_path TO pg_catalog")
            cur.execute("SET LOCAL statement_timeout = '10s'")
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
            cur.execute("""SELECT current_database(),current_user,session_user,
                         current_setting('transaction_read_only'),current_setting('transaction_isolation'),
                         (SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid())""")
            observed=cur.fetchone()
            if observed!=(source.database,source.role,source.role,"on","repeatable read",True):
                raise rest.FeedError("sql_postgresql_source_identity_or_readonly_check_failed")
            cur.execute("""SELECT a.attname,t.typname,a.attnotnull,a.atttypmod
                FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
                WHERE n.nspname=%s AND c.relname=%s AND c.relkind IN ('r','p','v','m')
                AND a.attnum>0 AND NOT a.attisdropped AND tn.nspname='pg_catalog'""",
                (source.schema_name,source.relation))
            meta={r[0]:(r[1],bool(r[2]),r[3]) for r in cur.fetchall()}
            fingerprint=_validate_types(source,meta)
            _schema_guard(p,fingerprint)
            _budget(deadline)
            # Nullable view columns are allowed, but refuse a source that
            # actually contains unwindowable NULL change times.
            cur.execute("SELECT 1 FROM "+_relation(source)+" WHERE "+_quote(source.mapping.change_time)+" IS NULL LIMIT 1")
            if cur.fetchone() is not None:
                raise rest.FeedError("sql_source_has_null_change_time")
        _budget(deadline)
        # Named server cursor avoids buffering an unbounded result on the client.
        with conn.cursor(name="owg_event_window") as cursor:
            cursor.execute(_query(source),(since,until,rest.MAX_ROWS+1))
            yield cursor,fingerprint
        conn.rollback()


def extract(p,since,until):
    source,target=rest._check(p)
    deadline=time.monotonic()+MAX_READ_SECONDS
    rows={}
    received=pages=duplicates=total_bytes=0
    manager=_postgres_read if source.engine=="postgresql" else _sqlite_read
    try:
        with manager(p,source,target,since,until,deadline) as (cursor,fingerprint):
            while True:
                _budget(deadline)
                rest._check(p)
                batch=cursor.fetchmany(p["config"]["page_size"])
                if not batch:
                    break
                pages+=1
                received+=len(batch)
                if received>rest.MAX_ROWS or pages>rest.MAX_PAGES:
                    raise rest.FeedError("sql_window_budget_exceeded_narrower_profile_required")
                for raw in batch:
                    row=_project(p,source,raw,since,until)
                    total_bytes+=len(json.dumps(row,ensure_ascii=True).encode())
                    if total_bytes>rest.MAX_TOTAL_BYTES:
                        raise rest.FeedError("sql_projection_budget_exceeded")
                    if row[0] in rows:
                        if rows[row[0]]!=row:
                            raise rest.FeedError("conflicting_source_event_identity",422)
                        duplicates+=1
                    rows[row[0]]=row
                _budget(deadline)
        rest._check(p)
    except rest.FeedError:
        raise
    except Exception:
        # Never echo DSNs, SQL, database error messages or selected values.
        if time.monotonic()>=deadline:
            raise rest.FeedError("sql_read_time_budget_exceeded") from None
        raise rest.FeedError("sql_source_read_failed",502) from None
    return list(rows.values()),{"pages":pages,"records_received":received,
                                "duplicate_records_in_window":duplicates,
                                "source_schema_sha256":fingerprint,
                                "snapshot_read":True,"provider_completeness_verified":False}
