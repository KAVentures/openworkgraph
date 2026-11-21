"""Owned fixture for timestamp-ordering inspection."""
import io
from process_logs_app import app
from server import event_log_workspace as workspace

workspace.import_log(io.BytesIO(b'''case,activity,time
1,Open,2026-01-01T00:00:00Z
1,Review,2026-01-01T00:00:00Z
2,Close,2026-01-02T00:00:00Z
'''), name='Ordering ties', source_format='csv', mapping={
    'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'})
