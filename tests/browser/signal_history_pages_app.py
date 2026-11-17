"""Per-log history browser fixture with synthetic retained transitions."""
import json
from signal_timeline_pages_app import app, logs
from process_logs_app import scratch
(scratch / 'history-test-logs.json').write_text(json.dumps(logs), encoding='utf-8')
