import csv
import io
import pytest
from scripts.salesforce_case_history import ExportError, project

CASE = '500000000000001'
OTHER = '500000000000002'
HISTORY = '017000000000001'


def raw(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Id', 'CaseId', 'CreatedDate', 'Field', 'NewValue', 'OldValue'])
    writer.writerows(rows)
    return out.getvalue().encode()


def test_projection_stable_deduped_private_and_account_scoped():
    row = [HISTORY, CASE, '2026-01-01T10:00:00.123+0100', 'Status', 'In Progress', 'secret-old']
    content, report = project(raw([row, row, [HISTORY, OTHER, 'bad', 'Subject', 'secret', '']]), account='sandbox', cases=[CASE])
    parsed = list(csv.DictReader(io.StringIO(content)))
    assert len(parsed) == 1
    assert parsed[0]['timestamp'] == '2026-01-01T09:00:00.123000+00:00'
    assert report['duplicate_rows'] == 1 and report['unselected_rows'] == 1
    assert CASE not in content and HISTORY not in content and 'secret' not in content
    assert project(raw([row]), account='sandbox', cases=[CASE])[0] == content
    assert project(raw([row]), account='other', cases=[CASE])[0] != content
    assert report['source_completeness'] == 'unknown'


def test_conflicting_history_refuses_whole_projection():
    rows = [[HISTORY, CASE, '2026-01-01T00:00:00Z', 'Status', status, ''] for status in ['New', 'Closed']]
    with pytest.raises(ExportError):
        project(raw(rows), account='sandbox', cases=[CASE])


@pytest.mark.parametrize('stamp', ['2026-01-01T10:00:00', '2026-13-01T10:00:00Z', 'bad'])
def test_invalid_selected_time_refused(stamp):
    with pytest.raises(ExportError):
        project(raw([[HISTORY, CASE, stamp, 'Status', 'New', '']]), account='sandbox', cases=[CASE])


def test_ties_and_excluded_fields_disclosed():
    rows = [[HISTORY, CASE, '2026-01-01T00:00:00Z', 'created', '', ''],
            ['017000000000002', CASE, '2026-01-01T00:00:00Z', 'Status', 'New', ''],
            ['017000000000003', CASE, 'bad', 'Description', 'secret', '']]
    output, report = project(raw(rows), account='sandbox', cases=[CASE, OTHER])
    assert report['tied_timestamp_events'] == 1
    assert report['excluded_fields'] == 1
    assert report['represented_cases'] == 1
    assert 'secret' not in output
