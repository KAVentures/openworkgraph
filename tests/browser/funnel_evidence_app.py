"""Owned fixture for paged milestone evidence."""
from io import BytesIO
from process_logs_app import app
from server import event_log_workspace as workspace

rows = ['case,activity,time']
for i in range(105):
    rows.extend([f'c{i:03},A,2024-01-01T00:00:00Z', f'c{i:03},B,2024-01-01T01:00:00Z'])
workspace.import_log(BytesIO('\n'.join(rows).encode()), name='Funnel evidence', source_format='csv', mapping={
    'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'})
