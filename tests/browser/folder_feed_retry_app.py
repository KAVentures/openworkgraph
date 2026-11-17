"""Folder-feed browser fixture with a controllable transient append failure."""
from process_logs_app import app, scratch
from server import event_log_workspace as workspace

_append = workspace.append_log

def _controlled_append(*args, **kwargs):
    if (scratch / 'fail-append').exists():
        raise workspace.EventLogWorkspaceError('synthetic temporary ingestion failure')
    return _append(*args, **kwargs)

workspace.append_log = _controlled_append
