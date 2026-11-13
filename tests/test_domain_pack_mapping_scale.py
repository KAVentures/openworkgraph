"""Mapping validation is scoped to selected labels even on high-cardinality logs."""
import duckdb
import pytest
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace


@pytest.fixture
def conn():
    c=duckdb.connect(':memory:')
    c.execute('CREATE TABLE objects (log_id VARCHAR, object_type VARCHAR)')
    c.execute('CREATE TABLE events (log_id VARCHAR, activity VARCHAR)')
    c.execute("INSERT INTO objects SELECT 'a', 'type_' || i::VARCHAR FROM range(100000) t(i)")
    c.execute("INSERT INTO events SELECT 'a', 'activity_' || i::VARCHAR FROM range(100000) t(i)")
    c.execute("INSERT INTO events VALUES ('a', ?), ('b', 'only_other_log')", ["x'); DROP TABLE events; --"])
    yield c
    c.close()


def test_large_catalog_accepts_exact_selected_labels_and_literal_sql_text(conn):
    mapping=packs.Mapping(object_types={'claim':'type_99999'},activities={'start':'activity_99999','end':"x'); DROP TABLE events; --"})
    packs._check_names(conn,'a',mapping)
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0]==100002


def test_matching_another_log_or_case_variant_is_rejected(conn):
    for name in ('only_other_log','Activity_99999','missing'):
        mapping=packs.Mapping(object_types={'claim':'type_99999'},activities={'start':name})
        with pytest.raises(workspace.EventLogWorkspaceError,match='not an activity of this log'):
            packs._check_names(conn,'a',mapping)


def test_empty_optional_collections_and_missing_object_type(conn):
    packs._check_names(conn,'a',packs.Mapping(object_types={},activities={}))
    with pytest.raises(workspace.EventLogWorkspaceError,match='not an object type of this log'):
        packs._check_names(conn,'a',packs.Mapping(object_types={'claim':'unknown'},activities={}))
