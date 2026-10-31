"""Exact case coverage by ranked observed activity sequences, not business intent."""
from __future__ import annotations

from typing import Any
from . import event_log_workspace as workspace


def coverage(log_id: str, case_type: str | None = None, limit: int = 20,
             cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise workspace.EventLogWorkspaceError('variant coverage limit must be 1 to 200')

    def run(conn):
        # Arrays preserve literal labels, including separators, without path collisions.
        conn.execute('''CREATE TEMP TABLE vc AS
            WITH paths AS (SELECT case_id, list(activity ORDER BY ts, event_id) AS path
                           FROM ce GROUP BY case_id),
            counts AS (SELECT path, count(*) AS cases FROM paths GROUP BY path)
            SELECT path, cases, row_number() OVER (ORDER BY cases DESC, path) AS rank,
                   sum(cases) OVER (ORDER BY cases DESC, path ROWS UNBOUNDED PRECEDING) AS cumulative_cases
            FROM counts''')
        variants, cases = conn.execute('SELECT count(*), coalesce(sum(cases),0) FROM vc').fetchone()
        targets = []
        for percent in (50, 80, 90, 95):
            # Integer arithmetic avoids floating-point threshold drift.
            rank = conn.execute('SELECT min(rank) FROM vc WHERE cumulative_cases * 100 >= ? * ?',
                                [cases, percent]).fetchone()[0] if cases else None
            targets.append({'target_percent': percent, 'variants_needed': rank})
        rows = conn.execute('''SELECT rank, path[:100], len(path), cases, cumulative_cases
                               FROM vc ORDER BY rank LIMIT ?''', [limit]).fetchall()
        shown_cases = rows[-1][4] if rows else 0
        return {'cases': cases, 'distinct_variants': variants, 'coverage_targets': targets,
                'displayed_variants': [{'rank': rank, 'path': path, 'path_activity_count': length,
                    'path_truncated': length > len(path), 'cases': count,
                    'share': count / cases, 'cumulative_cases': cumulative,
                    'cumulative_share': cumulative / cases} for rank, path, length, count, cumulative in rows],
                'omitted_variants': variants - len(rows), 'omitted_cases': cases - shown_cases,
                'ranking': 'case count descending; literal activity sequence breaks ties',
                'interpretation': 'Coverage of recorded activity sequences only; not automation suitability, employee effort or verified process boundaries.'}
    return workspace._query_filtered(log_id, case_type, cohort, run)
