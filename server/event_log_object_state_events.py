"""Owner-directed exact event selection for event-time state checks."""
from __future__ import annotations

from datetime import date
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from shared.event_log_formats import _exact_key, EventLogFormatError
from . import event_log_workspace as workspace
from .event_log_object_relationships import _revision


class Lookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    activity: str = Field(min_length=1, max_length=200)
    search: str = Field(default='', max_length=200)
    from_date: str | None = None
    until_date: str | None = None
    after: str | None = Field(default=None, min_length=1, max_length=200)
    expected_revision: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator('activity', 'after')
    @classmethod
    def exact_key(cls, value):
        if value is not None:
            try:
                if not _exact_key(value):
                    raise ValueError('Use an exact imported identifier')
            except EventLogFormatError:
                raise ValueError('Use an exact imported identifier') from None
        return value

    @field_validator('search')
    @classmethod
    def valid_search(cls, value):
        if '\x00' in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError('Invalid search')
        return value

    @field_validator('from_date', 'until_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                raise ValueError('Use a UTC calendar date')
            date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def bounds(self):
        if self.after is not None and self.expected_revision is None:
            raise ValueError('Revision required for paging')
        if self.from_date and self.until_date and self.from_date >= self.until_date:
            raise ValueError('Invalid date window')
        return self


def search(log_id, value):
    try:
        spec = Lookup.model_validate(value)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid object state event lookup') from None
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('BEGIN TRANSACTION')
            info = workspace._check_log(conn, log_id)
            if info['source_format'] not in ('ocel', 'ocelxml'):
                raise workspace.EventLogWorkspaceError('object state event lookup needs a retained OCEL log')
            revision = _revision(conn, log_id)
            if spec.expected_revision is not None and revision != spec.expected_revision:
                raise workspace.EventLogWorkspaceError('log changed; restart the event lookup')
            clauses = ['log_id=?', 'activity=?', 'contains(event_id, ?)']
            args = [log_id, spec.activity, spec.search]
            if spec.from_date is not None:
                clauses.append('ts>=?');args.append(date.fromisoformat(spec.from_date))
            if spec.until_date is not None:
                clauses.append('ts<?');args.append(date.fromisoformat(spec.until_date))
            if spec.after is not None:
                clauses.append('event_id>?');args.append(spec.after)
            rows = conn.execute('SELECT event_id,ts FROM events WHERE ' + ' AND '.join(clauses) +
                                ' ORDER BY event_id LIMIT ?', [*args,spec.limit+1]).fetchall()
            more = len(rows)>spec.limit;rows=rows[:spec.limit]
            return {'revision':revision, 'events':[{'event_id':key,'at':at.isoformat()+'Z'} for key,at in rows],
                    'next_after':rows[-1][0] if more else None, 'match':'case_sensitive_literal'}
        finally:
            conn.close()
