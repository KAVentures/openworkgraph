"""Owner-directed literal label lookup for the current case notion."""
from __future__ import annotations

from typing import Any
from . import event_log_workspace as workspace

MAX_PAGE = 100
FORMAT = 'openworkgraph.event-log-activity-lookup.v1'


def _literal(value: Any, *, allow_empty: bool) -> bool:
    return (isinstance(value, str) and (allow_empty or bool(value)) and len(value) <= 200
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
                        or ord(c) in (0xFFFE, 0xFFFF) for c in value))


def search(log_id: str, text: str, after: str = '', case_type: str | None = None) -> dict[str, Any]:
    return _search(log_id, text, after, case_type, 'activity', FORMAT)


def resource_search(log_id: str, text: str, after: str = '', case_type: str | None = None) -> dict[str, Any]:
    return _search(log_id, text, after, case_type, 'resource', 'openworkgraph.event-log-resource-lookup.v1')


def _search(log_id: str, text: str, after: str, case_type: str | None,
            column: str, format_name: str) -> dict[str, Any]:
    if (not _literal(text, allow_empty=False) or not _literal(after, allow_empty=True)
            or (case_type is not None and not _literal(case_type, allow_empty=False))):
        raise workspace.EventLogWorkspaceError(f'Enter valid search text and a valid {column} cursor')

    def run(conn):
        # ce already applies the current case notion and optional activity
        # mapping. Search is literal and independent of the active case filter,
        # so a filter that currently matches nothing cannot hide all choices.
        rows = conn.execute(f'''SELECT DISTINCT {column} FROM ce
            WHERE contains({column}, ?) AND {column} > ? ORDER BY {column} LIMIT ?''',
                            [text, after, MAX_PAGE + 1]).fetchall()
        labels = [row[0] for row in rows[:MAX_PAGE]]
        return {'labels': labels, 'next_after': labels[-1] if len(rows) > MAX_PAGE else None}

    result = workspace._query(log_id, case_type, run)
    return {'format': format_name, 'labels': result['labels'], 'next_after': result['next_after'],
            'limit': MAX_PAGE, 'stable_snapshot': False,
            'case_notion': result['case_notion'],
            'activity_mapping': result.get('activity_mapping')}
