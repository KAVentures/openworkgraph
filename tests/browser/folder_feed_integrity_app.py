"""Folder-feed browser fixture with a controlled post-commit report failure."""
from process_logs_app import app, scratch
from server import event_log_workspace as workspace

_record_quality = workspace._record_quality

def _controlled_report(*args, **kwargs):
    if (scratch / 'fail-quality').exists():
        raise workspace.EventLogWorkspaceError('synthetic quality report failure')
    return _record_quality(*args, **kwargs)

workspace._record_quality = _controlled_report
