"""Compatibility of integrity recovery with bounded quality history."""
from io import BytesIO
import json

import pytest
from server import event_log_quality as quality
from server import event_log_quality_pages as pages
from server import event_log_workspace as workspace

DOC=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n'
MAPPING={'case_column':'case','activity_column':'activity','timestamp_column':'time'}


def _make(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    return workspace.import_log(BytesIO(DOC),name='compat',source_format='csv',mapping=MAPPING)['log_id']


@pytest.mark.parametrize('bad', ['{bad json', '{}', '{"batch":"0"}', '{"batch":true}', 'null', '[' * 1100 + '0' + ']' * 1100])
def test_corrupt_import_report_stays_missing_and_never_verified(tmp_path, monkeypatch, bad):
    log=_make(tmp_path,monkeypatch)
    assert quality.overview(log)['integrity']['status']=='verified'
    conn=workspace._connect()
    try:
        conn.execute('UPDATE log_quality SET report = ? WHERE log_id = ? AND batch = 0',[bad,log])
    finally:
        conn.close()
    for result in (quality.quality(log),quality.batch_history(log),pages.page(log),quality.overview(log)):
        assert result['reports']==[] and result['batches_without_report']==[0]
    assert quality.quality(log)['integrity']['status']=='not_verifiable'
    overview=quality.overview(log)
    assert overview['integrity']['status']=='not_verifiable' and overview['first_report'] is None
    assert pages.page(log)['batch_trends']['review_queue'][0]['status']=='missing_report'


def test_corrupt_report_at_page_boundary_suppresses_comparison(tmp_path, monkeypatch):
    log=_make(tmp_path,monkeypatch)
    conn=workspace._connect()
    try:
        conn.execute('''INSERT INTO log_appends SELECT ?, i, TIMESTAMP '2024-01-01', 'csv', 1, 0, 0, 0
            FROM range(1,106) t(i)''',[log])
        conn.execute('''INSERT INTO log_quality SELECT log_id, i, created_at,
            json_merge_patch(report, json_object('batch',i,'kind','append'))
            FROM log_quality, range(1,106) t(i) WHERE log_id=? AND batch=0''',[log])
        conn.execute("UPDATE log_quality SET report='{broken' WHERE log_id=? AND batch=5",[log])
    finally:
        conn.close()
    full=quality.quality(log)
    result=pages.page(log)
    assert result['window']['batch_count']==100 and result['window']['next_before']==6
    first=result['batch_trends']['batches'][0]
    expected=next(item for item in full['batch_trends']['batches'] if item['batch']==6)
    assert first==expected and first['comparison']['status']=='missing_intermediate_report'
    assert all(value is None for value in first['comparison']['delta_percentage_points'].values())
    assert quality.overview(log)['integrity']['status']=='not_verifiable'
