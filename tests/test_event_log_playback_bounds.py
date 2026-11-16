"""Playback applies its point budget before returning event lists from DuckDB."""
from io import BytesIO

from server import event_log_playback as playback
from server import event_log_process_map as maps
from server import event_log_workspace as workspace


def test_long_case_is_bounded_before_fetch_and_keeps_order_and_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    log = workspace.import_log(BytesIO(b'case,activity,time\nlong,A,2024-01-01T00:00:00Z\nshort,A,2024-01-02T00:00:00Z\n'),
                               name='bounded playback', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO events
            SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
              CASE WHEN i % 2 = 0 THEN 'A' ELSE 'B' END,
              TIMESTAMP '2024-01-01 00:01:00' + (i // 2) * INTERVAL '1 second', '', '{}'
            FROM range(100000) t(i)''', [log])
        conn.execute('''INSERT INTO event_objects
            SELECT log_id, event_id, 'long', '' FROM events WHERE log_id = ? AND starts_with(event_id, 'fixture_')''', [log])
    finally:
        conn.close()
    monkeypatch.setattr(playback, 'MAX_POINTS', 10)
    actual_connect = workspace._connect
    fetched_lengths = []

    class InspectConnection:
        def __init__(self, raw):
            self.raw = raw
            self.playback_list = False
        def execute(self, sql, *args):
            self.playback_list = 'list(struct_pack(ts :=' in sql
            self.raw.execute(sql, *args)
            return self
        def fetchone(self):
            return self.raw.fetchone()
        def fetchall(self):
            rows = self.raw.fetchall()
            if self.playback_list:
                fetched_lengths.extend(len(row[3]) for row in rows)
            return rows
        def __getattr__(self, name):
            return getattr(self.raw, name)

    monkeypatch.setattr(workspace, '_connect', lambda: InspectConnection(actual_connect()))
    result = playback.playback(log, max_cases=2)
    assert sorted(fetched_lengths) == [1, 5]  # not the full 100001-event case
    cases = {item['case_id']: item for item in result['cases']}
    long = cases['long']
    a, b = result['activities'].index('A'), result['activities'].index('B')
    assert long['events'] == [[0.0, a], [60.0, a], [60.0, b], [61.0, a], [61.0, b]]
    assert long['events_truncated'] is True and cases['short']['events_truncated'] is False
    assert result['coverage']['events_shown'] == 6 and result['coverage']['cases_truncated'] == 1
    assert result['coverage']['steps_sharing_a_timestamp'] == 2
    assert result['coverage']['cases'] == result['coverage']['cases_shown'] == 2


def test_high_cardinality_activity_selection_uses_full_total_but_fetches_only_top_64(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    log = workspace.import_log(BytesIO(b'case,activity,time\nlong,A,2024-01-01T00:00:00Z\n'),
                               name='many activities', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO events
            SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
              'Activity_' || lpad(CAST(i AS VARCHAR), 6, '0'),
              TIMESTAMP '2024-01-01 00:01:00', '', '{}' FROM range(10000) t(i)''', [log])
        conn.execute('''INSERT INTO event_objects SELECT log_id, event_id, 'long', ''
            FROM events WHERE log_id = ? AND starts_with(event_id, 'fixture_')''', [log])
    finally:
        conn.close()
    actual_connect = workspace._connect
    fetched = []

    class InspectConnection:
        def __init__(self, raw):
            self.raw, self.ranking = raw, False
        def execute(self, sql, *args):
            self.ranking = ('GROUP BY activity' in sql and
                ('ORDER BY n DESC, activity' in sql or 'ORDER BY events DESC, activity' in sql))
            self.raw.execute(sql, *args)
            return self
        def fetchone(self):
            return self.raw.fetchone()
        def fetchall(self):
            rows = self.raw.fetchall()
            if self.ranking:
                fetched.append(len(rows))
            return rows
        def __getattr__(self, name):
            return getattr(self.raw, name)

    monkeypatch.setattr(workspace, '_connect', lambda: InspectConnection(actual_connect()))
    result = playback.playback(log, activities=0.001)
    assert fetched == [64]
    assert len(result['activities']) == 11  # ceil(0.001 * 10001), not 0.001 * 64
    assert result['activities'][0] == 'A' and result['activities_hidden'] == 9990
    assert result['coverage']['events_shown'] == 11
    mapped = maps.process_map(log, activities=0.001)
    assert [item['activity'] for item in mapped['activities']] == result['activities']
    assert mapped['coverage']['activities_hidden'] == result['activities_hidden']
    assert mapped['coverage']['event_share_shown'] == 11 / 10001
    assert fetched == [64, 64]  # both views bound activity rows before fetch


def test_frequency_ties_and_thresholds_match_independent_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    counts = {f'activity_{i:03}': 1 + i % 5 for i in range(75)}
    lines = ['case,activity,time']
    for activity, count in counts.items():
        lines.extend(f'c,{activity},2024-01-01T00:00:00Z' for _ in range(count))
    # Keep distinct rows by assigning a source event ID; import otherwise deduplicates payloads.
    rows = ['event,case,activity,time']
    rows.extend(f'e{i},{row}' for i, row in enumerate(lines[1:]))
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time',
               'event_id_column': 'event', 'source_namespace': 'test'}
    log = workspace.import_log(BytesIO(('\n'.join(rows) + '\n').encode()), name='ranking',
                               source_format='csv', mapping=mapping)['log_id']
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    total = sum(counts.values())
    for share in (0.5, 0.99, 1.0):
        expected, covered = [], 0
        for activity, count in ranked:
            if expected and (covered >= share * total or len(expected) >= 64):
                break
            expected.append(activity)
            covered += count
        played = playback.playback(log, activities=share)
        mapped = maps.process_map(log, activities=share)
        assert played['activities'] == expected == [item['activity'] for item in mapped['activities']]
        assert played['activities_hidden'] == mapped['coverage']['activities_hidden'] == 75 - len(expected)
        assert mapped['coverage']['event_share_shown'] == covered / total
