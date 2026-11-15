"""Dotted-chart color summaries stay bounded for high-cardinality logs."""
from io import BytesIO

from server import event_log_dotted_chart as dots
from server import event_log_workspace as workspace


def test_high_cardinality_legend_keeps_exact_other_counts_and_stable_colours(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    doc = b'case,activity,time\nlong,A,2024-01-01T00:00:00Z\nshort,A,2024-01-02T00:00:00Z\nshort,Keep,2024-01-02T01:00:00Z\n'
    log = workspace.import_log(BytesIO(doc), name='colours', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO events SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
            'Rare_' || lpad(CAST(i AS VARCHAR), 6, '0'), TIMESTAMP '2024-01-01 00:01:00', '', '{}'
            FROM range(10000) t(i)''', [log])
        conn.execute('''INSERT INTO event_objects SELECT log_id, event_id, 'long', '' FROM events
            WHERE log_id = ? AND starts_with(event_id, 'fixture_')''', [log])
    finally:
        conn.close()
    actual_connect = workspace._connect
    returned_group_sizes = []

    class InspectConnection:
        def __init__(self, raw):
            self.raw, self.activity_group = raw, False
        def execute(self, sql, *args):
            self.activity_group = 'GROUP BY activity' in sql and 'FROM ce' in sql
            self.raw.execute(sql, *args)
            return self
        def fetchone(self):
            return self.raw.fetchone()
        def fetchall(self):
            rows = self.raw.fetchall()
            if self.activity_group:
                returned_group_sizes.append(len(rows))
            return rows
        def __getattr__(self, name):
            return getattr(self.raw, name)

    monkeypatch.setattr(workspace, '_connect', lambda: InspectConnection(actual_connect()))
    full = dots.dotted_chart(log, max_cases=2)
    assert returned_group_sizes == [7, 7]  # neither query returns all 10002 labels
    legend = full['activities']
    assert [item['activity'] for item in legend[:2]] == ['A', 'Keep']
    assert legend[-1] == {'activity': None, 'slot': 7, 'events': 9995, 'activities': 9995}
    assert sum(item['events'] for item in legend) == full['coverage']['events'] == 10003
    filtered = dots.dotted_chart(log, cohort={'activity': 'Keep'})
    assert [item['activity'] for item in filtered['activities']] == [item['activity'] for item in legend[:7]]
    assert [item['events'] for item in filtered['activities']] == [1, 1, 0, 0, 0, 0, 0]
    assert filtered['coverage']['events'] == 2
    empty = dots.dotted_chart(log, cohort={'activity': 'not present'})
    assert empty['coverage']['events'] == 0 and all(item['events'] == 0 for item in empty['activities'])
