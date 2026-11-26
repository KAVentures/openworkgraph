"""Owner-saved, log-scoped definitions for event-time object state checks."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_object_state_check as state
from . import event_log_workspace as workspace

MAX_CHECKS = 50
_DEFINITION_KEYS = {'activity', 'object_type', 'attribute', 'expected_value', 'operator', 'quantifier', 'event_qualifier'}


class NewCheck(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=120)
    definition: dict[str, Any]


class UpdateCheck(NewCheck):
    expected_revision: int = Field(ge=1)


def _parse(model, payload):
    try:
        parsed = model.model_validate(payload)
        name = parsed.name.strip()
        if not name or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name):
            raise ValueError('invalid name')
        if set(parsed.definition) - _DEFINITION_KEYS:
            raise ValueError('unexpected definition fields')
        check = state._parse(parsed.definition)
        definition = check.model_dump(exclude={'status','offset','limit','expected_revision'}, exclude_none=True)
        encoded = json.dumps(definition, ensure_ascii=False)
        name.encode('utf-8'); encoded.encode('utf-8')
    except (ValidationError, ValueError, UnicodeEncodeError):
        raise workspace.EventLogWorkspaceError('invalid saved object state check') from None
    return parsed, name, encoded


def _with_log(log_id, operation):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            if info['source_format'] not in ('ocel', 'ocelxml'):
                raise workspace.EventLogWorkspaceError('saved object state checks need a retained OCEL log')
            return operation(conn)
        finally:
            conn.close()


def _read(conn, log_id, check_id):
    row = conn.execute('''SELECT name,revision,created_at,updated_at,definition FROM object_state_saved_checks
        WHERE log_id=? AND check_id=?''', [log_id, check_id]).fetchone()
    if row is None:
        raise workspace.EventLogWorkspaceError('no saved state check with this id in this log')
    name, revision, created, updated, definition = row
    return {'check_id':check_id, 'name':name, 'revision':revision,
            'created_at':created.isoformat()+'Z', 'updated_at':updated.isoformat()+'Z',
            'definition':json.loads(definition)}


def listing(log_id):
    def run(conn):
        rows = conn.execute('''SELECT check_id,name,revision,updated_at FROM object_state_saved_checks
            WHERE log_id=? ORDER BY name,check_id''', [log_id]).fetchall()
        return {'checks':[{'check_id':key,'name':name,'revision':revision,'updated_at':stamp.isoformat()+'Z'}
                          for key,name,revision,stamp in rows]}
    return _with_log(log_id, run)


def get(log_id, check_id):
    return _with_log(log_id, lambda conn: _read(conn, log_id, check_id))


def save(log_id, payload):
    _, name, encoded = _parse(NewCheck, payload)
    def run(conn):
        conn.execute('BEGIN TRANSACTION')
        workspace._check_log(conn, log_id)
        if conn.execute('SELECT count(*) FROM object_state_saved_checks WHERE log_id=?', [log_id]).fetchone()[0] >= MAX_CHECKS:
            raise workspace.EventLogWorkspaceError(f'a log keeps at most {MAX_CHECKS} saved state checks')
        key = 'objstate_' + secrets.token_hex(8)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            conn.execute('INSERT INTO object_state_saved_checks VALUES (?, ?, ?, 1, ?, ?, ?)',
                         [log_id, key, name, now, now, encoded])
            result = _read(conn, log_id, key)
            conn.execute('COMMIT')
            return result
        except duckdb.ConstraintException:
            raise workspace.EventLogWorkspaceError('a saved state check with this name already exists') from None
        except duckdb.TransactionException:
            raise workspace.EventLogWorkspaceError('the log or saved checks changed; retry saving') from None
    return _with_log(log_id, run)


def update(log_id, check_id, payload):
    parsed, name, encoded = _parse(UpdateCheck, payload)
    def run(conn):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            conn.execute('BEGIN TRANSACTION')
            workspace._check_log(conn, log_id)
            changed = conn.execute('''UPDATE object_state_saved_checks SET name=?,revision=revision+1,updated_at=?,definition=?
                WHERE log_id=? AND check_id=? AND revision=? RETURNING check_id''',
                [name, now, encoded, log_id, check_id, parsed.expected_revision]).fetchone()
            if not changed:
                raise workspace.EventLogWorkspaceError('the saved state check changed or was removed; load it again')
            result = _read(conn, log_id, check_id)
            conn.execute('COMMIT')
            return result
        except duckdb.ConstraintException:
            raise workspace.EventLogWorkspaceError('a saved state check with this name already exists') from None
        except duckdb.TransactionException:
            raise workspace.EventLogWorkspaceError('the saved state check changed; load it again') from None
    return _with_log(log_id, run)


def delete(log_id, check_id, revision):
    if type(revision) is not int or revision < 1:
        raise workspace.EventLogWorkspaceError('a valid saved state check revision is required')
    def run(conn):
        try:
            conn.execute('BEGIN TRANSACTION')
            workspace._check_log(conn, log_id)
            changed = conn.execute('''DELETE FROM object_state_saved_checks
                WHERE log_id=? AND check_id=? AND revision=? RETURNING check_id''', [log_id, check_id, revision]).fetchone()
            if not changed:
                raise workspace.EventLogWorkspaceError('the saved state check changed or was removed; load it again')
            conn.execute('COMMIT')
            return {'deleted':check_id}
        except duckdb.TransactionException:
            raise workspace.EventLogWorkspaceError('the saved state check changed; load it again') from None
    return _with_log(log_id, run)
