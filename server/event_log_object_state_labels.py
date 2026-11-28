"""Owner-directed literal labels for event-time object state checks."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from shared.event_log_formats import _exact_key, EventLogFormatError
from . import event_log_workspace as workspace
from .event_log_object_relationships import _revision


class Lookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['activity', 'attribute', 'value', 'qualifier']
    search: str = Field(default='', max_length=500)
    after: str | None = Field(default=None, max_length=500)
    object_type: str | None = Field(default=None, min_length=1, max_length=200)
    attribute: str | None = Field(default=None, min_length=1, max_length=200)
    expected_revision: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


def search(log_id: str, value) -> dict:
    try:
        spec = Lookup.model_validate(value)
        if spec.after is not None and spec.expected_revision is None:
            raise ValueError('revision required')
        if spec.kind == 'activity':
            if spec.object_type is not None or spec.attribute is not None:
                raise ValueError('unexpected scope')
        else:
            if spec.object_type is None or not _exact_key(spec.object_type):
                raise ValueError('object type required')
            if spec.kind == 'value':
                if spec.attribute is None or not _exact_key(spec.attribute):
                    raise ValueError('attribute required')
            elif spec.attribute is not None:
                raise ValueError('unexpected attribute')
        max_length = {'activity': 200, 'attribute': 200, 'value': 500, 'qualifier': 80}[spec.kind]
        for text in (spec.search, spec.after):
            if text is not None and (len(text) > max_length or '\x00' in text or
                    any(0xD800 <= ord(char) <= 0xDFFF for char in text)):
                raise ValueError('invalid search or cursor')
    except (ValidationError, ValueError, EventLogFormatError):
        raise workspace.EventLogWorkspaceError('invalid object state label lookup') from None

    if spec.kind == 'activity':
        source = 'SELECT e.activity AS label FROM events e WHERE e.log_id=?'
        args = [log_id]
    elif spec.kind == 'qualifier':
        source = '''SELECT eo.qualifier AS label FROM event_objects eo JOIN objects o
            ON o.log_id=eo.log_id AND o.object_id=eo.object_id WHERE eo.log_id=? AND o.object_type=?'''
        args = [log_id, spec.object_type]
    else:
        column = 'v.name' if spec.kind == 'attribute' else 'v.value'
        source = f'''SELECT {column} AS label FROM object_attribute_values v JOIN objects o
            ON o.log_id=v.log_id AND o.object_id=v.object_id WHERE v.log_id=? AND o.object_type=?'''
        args = [log_id, spec.object_type]
        if spec.kind == 'value':
            source += ' AND v.name=?'
            args.append(spec.attribute)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('BEGIN TRANSACTION')
            info = workspace._check_log(conn, log_id)
            if info['source_format'] not in ('ocel', 'ocelxml'):
                raise workspace.EventLogWorkspaceError('object state labels need a retained OCEL log')
            revision = _revision(conn, log_id)
            if spec.expected_revision is not None and revision != spec.expected_revision:
                raise workspace.EventLogWorkspaceError('log changed; restart the label lookup')
            cursor = ' AND label > ?' if spec.after is not None else ''
            rows = conn.execute('SELECT DISTINCT label FROM (' + source + ') labels WHERE contains(label, ?)' +
                cursor + ' ORDER BY label LIMIT ?', [*args, spec.search, *([spec.after] if spec.after is not None else []), spec.limit+1]).fetchall()
            more = len(rows) > spec.limit
            labels = [row[0] for row in rows[:spec.limit]]
            checkable = []
            for label in labels:
                try:
                    checkable.append(bool(_exact_key(label)) if spec.kind == 'attribute' else True)
                except EventLogFormatError:
                    checkable.append(False)
            return {'kind': spec.kind, 'search': spec.search, 'object_type': spec.object_type,
                    'attribute': spec.attribute, 'labels': labels, 'revision': revision,
                    'checkable': checkable,
                    'next_after': labels[-1] if more else None, 'match': 'case_sensitive_literal'}
        finally:
            conn.close()
