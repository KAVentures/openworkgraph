"""Browser fixture with a controllable file-read denial."""
from contextlib import contextmanager
from process_logs_app import app, scratch
from server import event_log_folder_feeds as feeds

_snapshot = feeds._snapshot

@contextmanager
def _controlled_snapshot(path, info):
    if (scratch / 'deny-feed-read').exists() and path.name == 'orders-1.csv':
        raise PermissionError('synthetic read denial')
    with _snapshot(path, info) as captured:
        yield captured

feeds._snapshot = _controlled_snapshot
