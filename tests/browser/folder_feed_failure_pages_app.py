"""Browser fixture: add synthetic failure receipts to exercise multiple pages."""
import json
from folder_feed_retry_app import app, scratch
from server import event_log_folder_feeds as feeds
from server import event_log_workspace as workspace

_original = feeds.failures
_seeded = False

def _with_test_receipts(feed_id, *, after=''):
    global _seeded
    if not _seeded and (scratch / 'seed-failures').exists():
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                feed = feeds._feed(conn, feed_id)
                for i in range(101):
                    conn.execute('INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)',
                        [feed['log_id'], feed_id, f'synthetic-{i}.csv', f'{i:064x}', 12, 'failed', json.dumps({'error': 'synthetic failure'})])
            finally:
                conn.close()
        _seeded = True
    return _original(feed_id, after=after)

feeds.failures = _with_test_receipts
