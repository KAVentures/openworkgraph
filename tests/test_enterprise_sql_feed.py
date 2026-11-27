"""Native SQL ingestion acceptance with real SQLite and optional TLS PostgreSQL."""
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from server import enterprise_sql_feed as sql
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

MAPPING={"event_id":"eid","case_id":"cid","activity":"label","event_time":"happened",
         "change_time":"changed","resource":"actor"}
TABLE="""CREATE TABLE events (
 eid TEXT NOT NULL, cid TEXT NOT NULL, label TEXT NOT NULL,
 happened INTEGER NOT NULL, changed INTEGER NOT NULL, actor TEXT, ignored TEXT
) STRICT"""


@pytest.fixture(autouse=True)
def workspace_dir(tmp_path,monkeypatch):
    folder=tmp_path/"retained"
    folder.mkdir()
    monkeypatch.setattr(workspace,"DATA_DIR",folder)
    monkeypatch.setattr(scheduler,"DATA_DIR",folder)
    monkeypatch.setenv("OWG_SQL_SOURCE_CATALOG","{}")


def install(monkeypatch,source,alias="source"):
    monkeypatch.setenv("OWG_SQL_SOURCE_CATALOG",json.dumps({alias:source}))


@pytest.fixture
def local_source(tmp_path,monkeypatch):
    path=(tmp_path/"source.sqlite").resolve()
    with sqlite3.connect(path) as c:
        c.execute(TABLE)
    source={"engine":"sqlite","path":str(path),"relation":"events","mapping":MAPPING}
    install(monkeypatch,source)
    return path,source


def config(**overrides):
    since=(rest._now()-timedelta(days=2)).replace(microsecond=0)
    return {"name":"SQL fixture","source_alias":"source",
            "expected_source_sha256":sql.catalog()["sources"][0]["source_sha256"],
            "initial_since":rest._iso(since),"window_hours":24,"overlap_minutes":60,
            "page_size":2,"confirm_configuration":True,"confirm_activity_labels":True,**overrides}


def row(p,identity="event-private-1",*,hour=23,minute=30,label="Approve",actor="actor-private"):
    start=rest._stamp(p["config"]["initial_since"])
    at=sql._micros(start+timedelta(hours=1))
    changed=sql._micros(start+timedelta(hours=hour,minutes=minute))
    return (identity,"case-private",label,at,changed,actor,"ignored-secret")


def insert(path,*rows):
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)",rows)


def run(p):
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True)


def current():
    return rest.inventory()["profiles"][0]


def test_catalog_is_passive_and_no_source_path_or_secret_exposed(local_source,monkeypatch):
    path,_=local_source
    original=sqlite3.connect
    def guard(*args,**kwargs):
        pytest.fail("catalog must not open a SQL connection")
    monkeypatch.setattr(sql.sqlite3,"connect",guard)
    cat=sql.catalog()
    assert cat["provider_reads_performed"] is False
    assert str(path) not in json.dumps(cat)
    assert cat["sources"][0]["timestamp_storage"]=="utc_epoch_microseconds"
    monkeypatch.setattr(sql.sqlite3,"connect",original)
    p=sql.create(config())
    assert p["provider"]=="sql" and p["checkpoint"] is None
    assert str(path) not in json.dumps(p)
    assert p["source_schema_sha256"] is None
    assert workspace.list_logs()["logs"]==[]


def test_sqlite_snapshot_paging_ties_deduplication_and_minimized_retention(local_source):
    path,_=local_source
    p=sql.create(config())
    first=row(p)
    second=row(p,"event-private-2")
    third=row(p,"event-private-3",actor=None)
    insert(path,first,second,third,first)
    out=run(p)
    assert out["events_added"]==3
    assert out["last_result"]["pages"]==2
    assert out["last_result"]["duplicate_records_in_window"]==1
    assert out["last_result"]["snapshot_read"]
    assert out["source_schema_sha256"]==out["last_result"]["source_schema_sha256"]
    assert workspace.list_logs()["logs"][0]["events"]==3
    insert(path,row(p,"event-private-4",hour=25,minute=0))
    again=run(out)
    assert again["events_added"]==1 and again["duplicates_skipped"]==3
    assert again["source_schema_sha256"]==out["source_schema_sha256"]
    assert again["log_id"]==out["log_id"]
    with workspace._LOCK:
        c=workspace._connect()
        try:
            content=str(c.execute("SELECT * FROM events").fetchall())+str(c.execute("SELECT * FROM objects").fetchall())+str(c.execute("SELECT * FROM event_source_keys").fetchall())
        finally:
            c.close()
    for secret in ("event-private","case-private","actor-private","ignored-secret"):
        assert secret not in content
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM events").fetchone()[0]==5


def test_empty_window_retains_schema_pin_and_never_fabricates_log(local_source):
    p=sql.create(config())
    out=run(p)
    assert out["log_id"] is None and out["checkpoint"] is not None
    assert out["source_schema_sha256"]
    assert workspace.list_logs()["logs"]==[]


def test_changed_retained_event_is_not_silently_overwritten(local_source):
    path,_=local_source
    p=sql.create(config())
    insert(path,row(p))
    first=run(p)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE events SET label='Changed'")
    with pytest.raises(rest.FeedError,match="retention_or_source_failed"):
        run(first)
    assert current()["checkpoint"]==first["checkpoint"]
    assert current()["source_schema_sha256"]==first["source_schema_sha256"]
    assert workspace.list_logs()["logs"][0]["events"]==1


def test_source_schema_drift_even_after_empty_window(local_source):
    path,_=local_source
    p=sql.create(config())
    first=run(p)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE events RENAME TO old_events")
        c.execute(TABLE.replace("eid TEXT NOT NULL","eid INTEGER NOT NULL"))
    with pytest.raises(rest.FeedError,match="sql_source_schema_changed"):
        run(first)
    assert current()["checkpoint"]==first["checkpoint"]
    assert current()["source_schema_sha256"]==first["source_schema_sha256"]


@pytest.mark.parametrize("bad_table",[
    TABLE.replace(" STRICT",""),
    TABLE.replace("changed INTEGER NOT NULL","changed INTEGER"),
    TABLE.replace("changed INTEGER NOT NULL","changed TEXT NOT NULL"),
    TABLE.replace("label TEXT NOT NULL","label INTEGER NOT NULL"),
])
def test_sqlite_rejects_unwindowable_schema(local_source,bad_table):
    path,_=local_source
    p=sql.create(config())
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE events")
        c.execute(bad_table)
    with pytest.raises(rest.FeedError):
        run(p)
    assert current()["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_bounded_pages_no_partial_import_and_retry(local_source,monkeypatch):
    path,_=local_source
    p=sql.create(config())
    insert(path,*[row(p,str(i)) for i in range(5)])
    monkeypatch.setattr(rest,"MAX_PAGES",2)
    with pytest.raises(rest.FeedError,match="sql_window_budget_exceeded"):
        run(p)
    assert current()["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]
    monkeypatch.setattr(rest,"MAX_PAGES",3)
    assert run(p)["events_added"]==5


def test_overlength_value_refuses_instead_of_truncating(local_source):
    path,_=local_source
    p=sql.create(config())
    insert(path,row(p,label="x"*1000000))
    with pytest.raises(rest.FeedError,match="invalid_mapped_source_field"):
        run(p)
    assert current()["checkpoint"] is None


def test_embedded_nul_and_null_resource_are_not_confused(local_source):
    path,_=local_source
    p=sql.create(config())
    insert(path,row(p,"bad\0identity"))
    with pytest.raises(rest.FeedError,match="invalid_mapped_source_field"):
        run(p)
    assert current()["checkpoint"] is None
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM events")
    insert(path,row(p,actor=None))
    assert run(p)["events_added"]==1


def test_catalog_retarget_and_credential_pin_fail_closed(local_source,monkeypatch):
    path,source=local_source
    reviewed=config()
    install(monkeypatch,{**source,"relation":"different"})
    with pytest.raises(rest.FeedError,match="sql_source_definition_changed"):
        sql.create(reviewed)
    install(monkeypatch,source)
    p=sql.create(reviewed)
    install(monkeypatch,{**source,"relation":"different"})
    assert not current()["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="sql_source_definition_changed"):
        run(p)
    install(monkeypatch,source)
    assert current()["credential_binding_available"]
    # Swap the file with a different inode while keeping the original path.
    replacement=path.with_name("replacement.sqlite")
    with sqlite3.connect(replacement) as c:
        c.execute(TABLE)
    c.close()
    os.replace(replacement,path)
    assert not current()["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="credential_changed"):
        run(p)


def test_disable_during_fetch_invalidates_transaction(local_source,monkeypatch):
    path,_=local_source
    p=sql.create(config())
    insert(path,row(p))
    original=sql._project
    def disable(*args):
        value=original(*args)
        rest.change(p["profile_id"],p["revision"],False)
        return value
    monkeypatch.setattr(sql,"_project",disable)
    with pytest.raises(rest.FeedError,match="rest_run_invalidated"):
        run(p)
    assert current()["status"]=="disabled"
    assert workspace.list_logs()["logs"]==[]


def test_schedule_pin_and_dataset_deletion(local_source,monkeypatch):
    path,_=local_source
    p=sql.create(config())
    insert(path,row(p))
    out=run(p)
    job=scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=15,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
    assert job["rest_profile_revision"]==p["revision"]
    workspace.delete_log(out["log_id"])
    assert current()["status"]=="dataset_deleted"
    assert current()["source_schema_sha256"] is None
    monkeypatch.setattr(scheduler,"_recording",lambda:True)
    result=scheduler.run_due(now=rest.time.time()+1000)
    assert result[0]["outcome"]=="paused:rest_profile_changed"
    assert not scheduler.status()["schedules"][0]["enabled"]


@pytest.mark.parametrize("overrides",[
    {"source_alias":"unknown"},{"expected_source_sha256":"a"*64},
    {"confirm_configuration":False},{"confirm_activity_labels":False},
    {"window_hours":1,"overlap_minutes":60},{"page_size":True},
    {"query":"SELECT * FROM private_data"},
])
def test_strict_creation(local_source,overrides):
    with pytest.raises(rest.FeedError):
        sql.create(config(**overrides))


def test_catalog_rejects_arbitrary_identifiers_and_unknown_options(local_source,monkeypatch):
    _,source=local_source
    for bad in ({**source,"relation":"events; DROP TABLE users"},
                {**source,"query":"SELECT * FROM users"},
                {**source,"mapping":{**MAPPING,"activity":"label FROM secret"}},
                {**source,"mapping":{**MAPPING,"case_id":"eid"}}):
        install(monkeypatch,bad)
        with pytest.raises(rest.FeedError,match="invalid_sql_deployment_catalog"):
            sql.catalog()


def test_operations_export_excludes_source_catalog(local_source):
    path,_=local_source
    p=sql.create(config())
    report=ops.inventory()
    row=next(x for x in report["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="sql" and row["provider_live_health"]=="unknown"
    assert row["counts"]=={"schema_pin_observed":False}
    assert not report["provider_reads_performed"]
    serialized=json.dumps(report)
    for value in (str(path),"SQL fixture","case-private","happened"):
        assert value not in serialized


def pg_source():
    return {"engine":"postgresql","schema_name":"business","relation":"events","database":"business",
            "role":"reader","dsn_env":"OWG_SQL_DSN_TEST","mapping":MAPPING}


@pytest.mark.parametrize("dsn",[
    "host=db.example dbname=business user=reader password=secret sslmode=require",
    "host=db.example dbname=business user=other password=secret sslmode=verify-full sslrootcert=/cert",
    "host=db.example dbname=business user=reader password=secret sslmode=verify-full sslrootcert=/cert options='-c search_path=private'",
    "host=db.example,other.example dbname=business user=reader password=secret sslmode=verify-full sslrootcert=/cert",
    "host=/tmp dbname=business user=reader password=secret sslmode=verify-full sslrootcert=/cert",
    "host=db.example dbname=wrong user=reader password=secret sslmode=verify-full sslrootcert=/cert",
])
def test_postgresql_dsn_requires_single_exact_tls_destination(monkeypatch,dsn):
    pytest.importorskip("psycopg")
    install(monkeypatch,pg_source())
    monkeypatch.setenv("OWG_SQL_DSN_TEST",dsn)
    with pytest.raises(rest.FeedError,match="sql_postgresql_requires_exact_tls_verified_read_role"):
        sql.create(config())


@pytest.mark.skipif(not os.environ.get("OWG_SQL_TEST_ADMIN_DSN"),reason="requires CI TLS PostgreSQL service")
def test_real_postgresql_tls_snapshot_retention_and_view_drift(monkeypatch):
    import psycopg
    from psycopg import sql as pgsql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    admin=os.environ["OWG_SQL_TEST_ADMIN_DSN"]
    params=conninfo_to_dict(admin)
    suffix=uuid.uuid4().hex[:12]
    role="owg_reader_"+suffix
    schema="owg_source_"+suffix
    password=uuid.uuid4().hex
    with psycopg.connect(admin,autocommit=True) as db:
        with db.cursor() as c:
            c.execute(pgsql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(pgsql.Identifier(role),pgsql.Literal(password)))
            c.execute(pgsql.SQL("CREATE SCHEMA {}").format(pgsql.Identifier(schema)))
            relation=pgsql.Identifier(schema,"source_table")
            view=pgsql.Identifier(schema,"events")
            c.execute(pgsql.SQL("CREATE TABLE {} (eid uuid NOT NULL,cid bigint NOT NULL,label text NOT NULL,happened timestamptz NOT NULL,changed timestamptz NOT NULL,actor text,ignored text)").format(relation))
            c.execute(pgsql.SQL("CREATE VIEW {} AS SELECT * FROM {}").format(view,relation))
            c.execute(pgsql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(pgsql.Identifier(schema),pgsql.Identifier(role)))
            c.execute(pgsql.SQL("GRANT SELECT ON {} TO {}").format(view,pgsql.Identifier(role)))
        try:
            source={**pg_source(),"schema_name":schema,"role":role,"database":params["dbname"]}
            install(monkeypatch,source)
            params.update(user=role,password=password)
            monkeypatch.setenv("OWG_SQL_DSN_TEST",make_conninfo(**params))
            p=sql.create(config())
            start=rest._stamp(p["config"]["initial_since"])
            at=start+timedelta(hours=1)
            changed=start+timedelta(hours=23,minutes=30)
            rows=[(uuid.uuid4(),42,"Approve",at,changed,"actor-private","not-selected") for _ in range(3)]
            with db.cursor() as c:
                c.executemany(pgsql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s)").format(relation),rows)
            first=run(p)
            assert first["events_added"]==3 and first["last_result"]["pages"]==2
            assert first["source_schema_sha256"]
            with psycopg.connect(os.environ["OWG_SQL_DSN_TEST"]) as reader:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute(pgsql.SQL("DELETE FROM {}").format(relation))
                reader.rollback()
            with db.cursor() as c:
                c.execute(pgsql.SQL("CREATE OR REPLACE VIEW {} AS SELECT * FROM {} WHERE cid>0").format(view,relation))
            with pytest.raises(rest.FeedError,match="sql_source_schema_changed"):
                run(first)
            assert current()["checkpoint"]==first["checkpoint"]
        finally:
            with db.cursor() as c:
                c.execute(pgsql.SQL("DROP SCHEMA {} CASCADE").format(pgsql.Identifier(schema)))
                c.execute(pgsql.SQL("DROP ROLE {}").format(pgsql.Identifier(role)))


def test_real_http_catalog_and_configuration_require_dashboard(local_source,tmp_path):
    path,source=local_source
    script=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_enterprise_sql_feed import config
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    b={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    path='/v1/enterprise-connectors/sql'
    assert c.get(path+'/catalog',headers=b).status_code in (401,403)
    assert c.post(path,json=config(),headers=b).status_code in (401,403)
    r=c.get(path+'/catalog',headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["sources"][0]["engine"]=="sqlite"
    r=c.post(path,json=config(),headers=h)
    assert r.status_code==200,r.text
    p=r.json()
    assert p["provider"]=="sql"
    assert c.post(path,content=b'{"name":"a","name":"b"}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert c.post('/v1/enterprise-connectors/rest/'+p["profile_id"]+'/run',json={
      "revision":1,"confirm_reads":False,"confirm_retention":True},headers=h).status_code==403
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"http-data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+script],
                        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
