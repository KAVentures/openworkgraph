"""Complete path identity and bounded resource use for typed analyses."""
from io import BytesIO, StringIO
import csv
import json

import pytest

from server import event_log_query as query, event_log_workspace as workspace
from test_event_log_query import _log


@pytest.fixture(autouse=True)
def data(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def csv_log(paths):
    body=StringIO();writer=csv.writer(body);writer.writerow(['case','activity','time'])
    for identifier,path in paths.items():
        for index,activity in enumerate(path):
            writer.writerow([identifier,activity,f'2024-01-01T00:00:{index:02d}Z'])
    return workspace.import_log(BytesIO(body.getvalue().encode()),name='Owned path identity',source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']


def test_literal_activity_containing_the_display_separator_is_not_the_same_as_two_activities():
    paths={'one':['A > B'],'two':['A','B'],'repeat':['A','B'],
           'escaping':['Say "yes"','Back\\slash','Comma,here']}
    log=csv_log(paths)
    result=query.run(log,{'level':'cases','dimensions':['variant'],'measures':[{'fn':'count'}]})
    assert result['variant_encoding']=='ordered_activity_json_array'
    assert {tuple(json.loads(row[0])):row[1] for row in result['rows']}=={
        ('A > B',):1,('A','B'):2,('Say "yes"','Back\\slash','Comma,here'):1}
    assert result['matching']=={'rows':4,'cases':4}


def test_case_filters_are_applied_before_complete_variant_byte_limits(monkeypatch):
    log=_log();monkeypatch.setattr(query,'MAX_VARIANT_PATH_BYTES',30)
    spec={'level':'cases','dimensions':['variant'],'measures':[{'fn':'count'}]}
    with pytest.raises(workspace.EventLogWorkspaceError,match='byte limits'):query.run(log,spec)
    narrowed=query.run(log,{**spec,'where':{'duration_under_seconds':10000}})
    assert {tuple(json.loads(row[0])) for row in narrowed['rows']}=={('Receive',),('Receive','Check','Close')}
    assert narrowed['matching']['cases']==2
    combined=query.run(log,{**spec,'dimensions':['variant','attribute:region'],
        'cohort':{'attribute':'region=EU'},'where':{'min_events':2}})
    assert combined['rows']==[['["Receive","Check","Close"]','EU',1]]


def test_total_variant_byte_limit_never_silently_drops_or_truncates_paths(monkeypatch):
    log=_log();monkeypatch.setattr(query,'MAX_VARIANT_TOTAL_BYTES',40)
    spec={'level':'cases','dimensions':['variant'],'measures':[{'fn':'count'}]}
    with pytest.raises(workspace.EventLogWorkspaceError,match='No partial paths'):query.run(log,spec)
    assert query.run(log,{'level':'cases','measures':[{'fn':'count'}]})['rows']==[[3]]
    empty=query.run(log,{**spec,'where':{'ends_with':'Missing'}})
    assert empty['rows']==[] and empty['matching']['cases']==0


def test_variant_byte_preflight_counts_utf8_and_json_escaping_exactly(monkeypatch):
    path=['雪"','🐱\\'];log=csv_log({'unicode':path})
    encoded=json.dumps(path,ensure_ascii=False,separators=(',',':')).encode()
    spec={'level':'cases','dimensions':['variant'],'measures':[{'fn':'count'}]}
    monkeypatch.setattr(query,'MAX_VARIANT_PATH_BYTES',len(encoded))
    assert json.loads(query.run(log,spec)['rows'][0][0])==path
    monkeypatch.setattr(query,'MAX_VARIANT_PATH_BYTES',len(encoded)-1)
    with pytest.raises(workspace.EventLogWorkspaceError,match='byte limits'):query.run(log,spec)


def test_counting_a_large_case_does_not_materialize_its_unused_variant(monkeypatch):
    log=csv_log({'synthetic':['Start']})
    conn=workspace._connect()
    conn.execute("INSERT INTO events SELECT ?, 'owned_' || CAST(i AS VARCHAR), repeat('A',200), "
                 "TIMESTAMP '2024-01-01' + i * INTERVAL '1 second', '', '{}' FROM range(300000) t(i)",[log])
    conn.execute("INSERT INTO event_objects SELECT log_id,event_id,'synthetic','' FROM events "
                 "WHERE log_id=? AND event_id LIKE 'owned_%'",[log])
    conn.execute('UPDATE logs SET events=300001 WHERE log_id=?',[log]);conn.close()
    original=workspace._connect
    def limited():
        result=original()
        # Per-worker buffers depend on host CPU count. Compare one serial
        # worker on every OS while keeping the data/memory/spill limits fixed.
        result.execute('SET threads=1');result.execute("SET memory_limit='128MB'")
        result.execute("SET max_temp_directory_size='0B'");return result
    monkeypatch.setattr(workspace,'_connect',limited)
    result=query.run(log,{'level':'cases','measures':[{'fn':'count'}]})
    assert result['rows']==[[1]] and result['matching']=={'rows':1,'cases':1}
    with pytest.raises(workspace.EventLogWorkspaceError,match='byte limits'):
        query.run(log,{'level':'cases','dimensions':['variant'],'measures':[{'fn':'count'}]})


def test_memory_exhaustion_has_a_safe_error_without_a_partial_or_zero_result(monkeypatch):
    import duckdb
    def exhausted(*args,**kwargs):raise duckdb.OutOfMemoryException('PRIVATE_SQL_AND_LABELS')
    monkeypatch.setattr(workspace,'_query',exhausted)
    with pytest.raises(workspace.EventLogWorkspaceError,match='No partial result') as error:
        query.run('unused',{'level':'cases','measures':[{'fn':'count'}]})
    assert 'PRIVATE' not in str(error.value)
