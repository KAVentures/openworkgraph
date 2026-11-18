"""Bounded, counts-only pages of current event-attribute coverage."""
from . import event_log_workspace as workspace

PAGE_SIZE = 32
MAX_OFFSET = 2_147_483_647


def page(log_id: str, offset: int = 0, revision: int | None = None) -> dict:
    if (type(offset) is not int or not 0 <= offset <= MAX_OFFSET or
            (revision is not None and (type(revision) is not int or not 0 <= revision <= MAX_OFFSET))):
        raise workspace.EventLogWorkspaceError('Invalid attribute coverage page')
    if offset and revision is None:
        raise workspace.EventLogWorkspaceError('Restart attribute coverage from the first page')
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            current = int(conn.execute('SELECT coalesce(max(batch),0) FROM log_appends WHERE log_id=?', [log_id]).fetchone()[0])
            if revision is not None and revision != current:
                raise workspace.EventLogWorkspaceError('The log changed; reload attribute coverage from the first page')
            events = conn.execute('SELECT count(*) FROM events WHERE log_id=?', [log_id]).fetchone()[0]
            grouped = '''SELECT j.key AS attribute,
                count(DISTINCT e.event_id) FILTER (WHERE j.type <> 'NULL' AND j.value <> '\"\"') AS populated
                FROM events e, json_each(e.attributes) j WHERE e.log_id=? GROUP BY j.key'''
            rows = conn.execute(f'''SELECT attribute,populated,count(*) OVER() AS total_keys FROM ({grouped})
                ORDER BY populated DESC,attribute LIMIT ? OFFSET ?''', [log_id, PAGE_SIZE + 1, offset]).fetchall()
            total = rows[0][2] if rows else conn.execute(f'SELECT count(*) FROM ({grouped})', [log_id]).fetchone()[0]
            return {'events': int(events), 'total_keys': int(total), 'offset': offset,
                    'revision': current, 'revision_basis': 'local_log_append_batch',
                    'next_offset': offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None,
                    'attributes': [{'attribute': key, 'events_with_value': count,
                                    'events_without_value': events - count}
                                   for key, count, _ in rows[:PAGE_SIZE]]}
        finally:
            conn.close()
