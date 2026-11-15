"""Bounded pages of stored batch quality metadata, with no whole-log scan."""
from __future__ import annotations

import json
from typing import Any

from . import event_log_quality_trends as trends
from . import event_log_workspace as workspace

MAX_PAGE = 100
MAX_BATCH = 2_147_483_647
FORMAT = 'openworkgraph.event-log-quality-history-page.v1'


def page(log_id: str, before: int | None = None, limit: int = MAX_PAGE) -> dict[str, Any]:
    if ((before is not None and (type(before) is not int or not 0 <= before <= MAX_BATCH))
            or type(limit) is not int or not 1 <= limit <= MAX_PAGE):
        raise workspace.EventLogWorkspaceError('Invalid batch history page')
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            # A logical batch can have a missing report, and legacy logs may have
            # no append report. Union the identifiers before selecting the page.
            batches = '''SELECT 0 AS batch UNION SELECT batch FROM log_appends WHERE log_id = ?
                         UNION SELECT batch FROM log_quality WHERE log_id = ?'''
            total = conn.execute(f'SELECT count(*) FROM ({batches}) b WHERE batch >= 0', [log_id, log_id]).fetchone()[0]
            cursor = MAX_BATCH + 1 if before is None else before
            ids = [row[0] for row in conn.execute(
                f'SELECT batch FROM ({batches}) b WHERE batch >= 0 AND batch < ? ORDER BY batch DESC LIMIT ?',
                [log_id, log_id, cursor, limit + 1]).fetchall()]
            selected = ids[:limit]
            next_before = selected[-1] if len(ids) > limit else None
            reports: list[dict[str, Any]] = []
            previous = None
            if selected:
                placeholders = ','.join('?' for _ in selected)
                records = conn.execute(
                    f'SELECT batch, report FROM log_quality WHERE log_id = ? AND batch IN ({placeholders}) ORDER BY batch LIMIT ?',
                    [log_id, *selected, limit + 1]).fetchall()
                if len(records) > len(selected) or len({row[0] for row in records}) != len(records):
                    raise workspace.EventLogWorkspaceError('Duplicate batch quality reports; history page unavailable')
                reports = [json.loads(row[1]) for row in records]
                if any(type(report.get('batch')) is not int or report['batch'] != record[0]
                       for report, record in zip(reports, records)):
                    raise workspace.EventLogWorkspaceError('Inconsistent batch quality report; history page unavailable')
                # The preceding report supplies the earliest row's comparison;
                # it is used for calculation only, never returned as part of this page.
                prior = conn.execute('SELECT batch, report FROM log_quality WHERE log_id = ? AND batch < ? ORDER BY batch DESC LIMIT 2',
                                     [log_id, min(selected)]).fetchall()
                if prior and len(prior) > 1 and prior[0][0] == prior[1][0]:
                    raise workspace.EventLogWorkspaceError('Duplicate batch quality reports; history page unavailable')
                previous = json.loads(prior[0][1]) if prior else None
                if previous is not None and (type(previous.get('batch')) is not int or previous['batch'] != prior[0][0]):
                    raise workspace.EventLogWorkspaceError('Inconsistent batch quality report; history page unavailable')
        finally:
            conn.close()
    present = {report['batch'] for report in reports}
    missing = sorted(batch for batch in selected if batch not in present)
    calculated = trends.build(([previous] if previous else []) + reports, missing)
    chosen = set(selected)
    calculated['batches'] = [item for item in calculated['batches'] if item['batch'] in chosen]
    calculated['review_queue'] = [item for item in calculated['review_queue'] if item['batch'] in chosen]
    return {'format': FORMAT, 'reports': reports, 'batches_without_report': missing,
            'batch_trends': calculated, 'window': {'before': before, 'limit': limit,
                'batch_count': len(selected), 'total_batches': total, 'next_before': next_before,
                'stable_snapshot': False}}
