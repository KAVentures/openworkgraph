"""Isolated local app seeded with synthetic signals across five logs."""
from datetime import datetime, timezone
from io import BytesIO
from process_logs_app import app
from server import event_log_signals as signals
from server import event_log_workspace as workspace

csv = b'case,activity,time\n1,Open,2024-01-01T00:00:00Z\n1,Close,2024-01-02T00:00:00Z\n'
logs = []
for index in range(5):
    logs.append(workspace.import_log(BytesIO(csv), name=f'Page log {index}', source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id'])
with workspace._LOCK:
    conn = workspace._connect()
    try:
        rows = []
        for i in range(205):
            rule = signals._validate({'name':f'Page signal {i:03}', 'when':{'kind':'kpi','metric':'case_count','operator':'above','threshold':0}})
            rows.append([logs[i // 50], f'sig_{i:016x}', datetime.now(timezone.utc).replace(tzinfo=None), rule.model_dump_json()])
        conn.executemany('INSERT INTO signal_rules VALUES (?, ?, ?, ?, NULL, NULL)', rows)
    finally:
        conn.close()
