"""Bounded display paths with exact grouping over complete observed variants."""
from __future__ import annotations

from typing import Any
from . import event_log_workspace as workspace

MAX_PATH = 100
FORMAT = 'openworkgraph.event-log-variant-preview.v1'


def preview(log_id: str, case_type: str | None = None, limit: int = 20,
            cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise workspace.EventLogWorkspaceError('variant preview limit must be 1 to 200')

    def run(conn):
        # Group complete literal arrays, not displayed prefixes or a delimiter
        # encoding. Paths are shortened only when returned to the application.
        conn.execute('''CREATE TEMP TABLE variant_preview_paths AS
            SELECT case_id, list(activity ORDER BY ts, event_id) AS path,
                   epoch(max(ts) - min(ts)) AS duration FROM ce GROUP BY case_id''')
        total, distinct = conn.execute(
            'SELECT count(*), count(DISTINCT path) FROM variant_preview_paths').fetchone()
        rows = conn.execute('''SELECT list_slice(path, 1, ?), len(path), count(*) AS cases,
                quantile_cont(duration, 0.5) AS median_duration
            FROM variant_preview_paths GROUP BY path ORDER BY cases DESC, path LIMIT ?''',
                            [MAX_PATH, limit]).fetchall()
        return {'format': FORMAT, 'cases': total, 'distinct_variants': distinct,
                'path_limit': MAX_PATH, 'variants': [
                    {'path': path, 'path_activity_count': n, 'path_truncated': n > len(path),
                     'cases': count, 'share': round(count / total, 6) if total else 0,
                     'median_duration_seconds': duration} for path, n, count, duration in rows]}

    return workspace._query_filtered(log_id, case_type, cohort, run)
