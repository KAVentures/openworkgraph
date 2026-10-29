"""Bounded SCIM Users provisioning over the roster; no OIDC/capture authority.

Provider metadata is not a second identity store. Employee status/name/actor
remain authoritative, including changes made through the ordinary admin UI.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import re
import sqlite3
import uuid

from . import employees as roster
from .auth import issue_token, token_hash

CORE = 'urn:ietf:params:scim:schemas:core:2.0:User'
PATCH = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'
LIST = 'urn:ietf:params:scim:api:messages:2.0:ListResponse'
ERROR = 'urn:ietf:params:scim:api:messages:2.0:Error'
MAX_PROVIDERS = 20
_ADMIN_LOOKUP = object()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS gateway_scim_providers (
 provider_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, label TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 0,
 revision BIGINT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 revoked_at TEXT);
CREATE INDEX IF NOT EXISTS idx_gateway_scim_providers_org ON gateway_scim_providers(organization_id);
CREATE TABLE IF NOT EXISTS gateway_scim_users (
 provider_id TEXT NOT NULL, employee_id TEXT NOT NULL UNIQUE,
 external_id TEXT, revision BIGINT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, deleted_at TEXT,
 PRIMARY KEY(provider_id,employee_id), UNIQUE(provider_id,external_id));
CREATE TABLE IF NOT EXISTS gateway_scim_groups (
 provider_id TEXT NOT NULL, group_id TEXT NOT NULL PRIMARY KEY,
 display_name TEXT NOT NULL, external_id TEXT, members_json TEXT NOT NULL,
 revision BIGINT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 deleted_at TEXT, UNIQUE(provider_id,external_id));
CREATE INDEX IF NOT EXISTS idx_gateway_scim_groups_provider
 ON gateway_scim_groups(provider_id);
'''


class Error(ValueError):
    def __init__(self, status, detail, scim_type=''):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def packet(self):
        return {'schemas': [ERROR], 'status': str(self.status), 'detail': self.detail,
                **({'scimType': self.scim_type} if self.scim_type else {})}


def init_schema(db):
    with db.connect() as conn:
        for statement in SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)


def _text(value, maximum, *, blank=False):
    if (not isinstance(value, str) or len(value) > maximum or
            (not blank and not value.strip()) or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 or ord(c) in (65534, 65535) for c in value)):
        raise Error(400, 'An identity attribute is missing, invalid or exceeds its bounds', 'invalidValue')
    return value


def _keys(body, allowed):
    if not isinstance(body, dict):
        raise Error(400, 'Expected a JSON object', 'invalidSyntax')
    result = {}
    for key, value in body.items():
        folded = key.lower() if isinstance(key, str) else ''
        if folded not in allowed:
            raise Error(400, 'An attribute or operation is unsupported', 'invalidPath')
        if folded in result:
            raise Error(400, 'Duplicate attribute names are not supported', 'invalidSyntax')
        result[folded] = value
    return result


def _attributes(body):
    values = _keys(body, {'username', 'displayname', 'externalid', 'active', 'schemas', 'id', 'meta', 'groups'})
    if values.get('schemas') != [CORE]:
        raise Error(400, 'Only the core User schema is supported', 'invalidSyntax')
    # SCIM readOnly fields echoed by a client are ignored; endpoint identity is
    # authoritative. The readOnly groups projection is ignored as well;
    # password, email aliases and extensions are refused.
    return _validate_values({k: v for k, v in values.items() if k not in {'schemas', 'id', 'meta', 'groups'}})


def _validate_values(values):
    for key, value in values.items():
        if key == 'active':
            if not isinstance(value, bool):
                raise Error(400, 'active must be a JSON boolean', 'invalidValue')
        elif key == 'username':
            try:
                values[key] = roster.normalize_email(_text(value, 512))
            except roster.EmployeeError:
                raise Error(400, 'userName must be a work email', 'invalidValue') from None
        elif key == 'displayname':
            values[key] = '' if value is None else ' '.join(_text(value, 120, blank=True).split())
        elif key == 'externalid':
            values[key] = None if value is None or value == '' else _text(value, 200)
    return values


_SELECT = '''SELECT e.*, l.external_id AS provider_external_id,
 l.revision AS resource_revision, l.created_at AS provisioned_at,
 l.updated_at AS provision_updated_at, l.deleted_at AS provision_deleted_at
 FROM gateway_scim_users l JOIN gateway_employees e ON e.employee_id=l.employee_id'''


def _project(row):
    resource = {'schemas': [CORE], 'id': row['employee_id'], 'userName': row['email'],
                'displayName': row['display_name'], 'active': row['status'] == 'active'}
    if row['provider_external_id'] is not None:
        resource['externalId'] = row['provider_external_id']
    basis = [resource, row['updated_at'], row['resource_revision'], row['provision_deleted_at']]
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    dates = [row['updated_at'], row['provision_updated_at']]
    modified = max(dates, key=lambda v: datetime.fromisoformat(v.replace('Z', '+00:00')))
    resource['meta'] = {'resourceType': 'User', 'created': row['provisioned_at'],
                        'lastModified': modified, 'version': 'W/"' + digest + '"'}
    return resource


class Provisioning:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self):
        try:
            with self.db.connect() as conn:
                if not self.db.is_postgres:
                    conn.execute('BEGIN IMMEDIATE')
                yield conn
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or getattr(exc, 'sqlstate', '') == '23505':
                raise Error(409, 'The identity or externalId is already assigned', 'uniqueness') from None
            raise

    def audit(self, conn, organization, principal, action, details):
        self.db._execute(conn, '''INSERT INTO audit_log
            (observed_at,organization_id,principal_id,action,details_json) VALUES (?,?,?,?,?)''',
            (roster._now(), organization, principal, action, json.dumps(details, sort_keys=True)))

    def provider(self, conn, provider_id, *, token=_ADMIN_LOOKUP):
        suffix = ' FOR UPDATE' if self.db.is_postgres else ''
        row = roster._one(self.db, conn, 'SELECT * FROM gateway_scim_providers WHERE provider_id=?' + suffix, (provider_id,))
        if token is not _ADMIN_LOOKUP:
            supplied = token_hash(token) if isinstance(token, str) and len(token) <= 200 else ''
            if (not row or not row['enabled'] or row['revoked_at'] or
                    not hmac.compare_digest(supplied, row['token_hash'])):
                raise Error(401, 'Current provider-scoped provisioning credentials required')
        elif not row:
            raise Error(404, 'Provisioning provider not found')
        return row

    @staticmethod
    def public_provider(row):
        return {k: row[k] for k in ('provider_id', 'organization_id', 'label', 'revision',
                                    'created_at', 'updated_at', 'revoked_at')} | {'enabled': bool(row['enabled'])}

    def create_provider(self, organization, label, principal):
        organization = roster._org(organization)
        label = _text(label, 120).strip()
        token = issue_token('owg_scim')
        pid, now = 'scim_' + uuid.uuid4().hex, roster._now()
        with self.transaction() as conn:
            # PostgreSQL serializes per-organization creation without a second
            # organization/identity store. SQLite has its existing writer lock.
            if self.db.is_postgres:
                self.db._execute(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('owg-scim:' + organization,))
            total = roster._one(self.db, conn, 'SELECT count(*) AS n FROM gateway_scim_providers WHERE organization_id=?', (organization,))['n']
            if total >= MAX_PROVIDERS:
                raise Error(409, 'At most twenty provisioning providers, including revoked history, are supported per organization')
            self.db._execute(conn, '''INSERT INTO gateway_scim_providers
                (provider_id,organization_id,label,token_hash,enabled,revision,created_at,updated_at)
                VALUES (?,?,?,?,0,1,?,?)''', (pid, organization, label, token_hash(token), now, now))
            self.audit(conn, organization, principal, 'scim.provider.created', {'provider_id': pid, 'enabled': False})
            result = self.public_provider(self.provider(conn, pid))
        return {'provider': result, 'token': token}

    def list_providers(self, organization):
        with self.db.connect() as conn:
            return [self.public_provider(row) for row in roster._all(self.db, conn,
                'SELECT * FROM gateway_scim_providers WHERE organization_id=? ORDER BY created_at,provider_id', (roster._org(organization),))]

    def _provider_change(self, provider_id, revision, principal, *, enabled=False, revoke=False, organization=None):
        if not isinstance(enabled, bool) or type(revision) is not int or revision < 1:
            raise Error(400, 'A current revision and explicit enabled boolean are required', 'invalidValue')
        with self.transaction() as conn:
            row = self.provider(conn, provider_id)
            if organization is not None and row['organization_id'] != organization:
                raise Error(404, 'Provisioning provider not found')
            if row['revision'] != revision:
                raise Error(412, 'Provisioning configuration changed; reload before updating')
            if row['revoked_at']:
                raise Error(409, 'A revoked credential cannot be enabled again')
            now = roster._now()
            self.db._execute(conn, '''UPDATE gateway_scim_providers SET enabled=?,revoked_at=?,
                revision=revision+1,updated_at=? WHERE provider_id=?''',
                (int(enabled and not revoke), now if revoke else None, now, provider_id))
            self.audit(conn, row['organization_id'], principal, 'scim.provider.revoked' if revoke else 'scim.provider.configured',
                       {'provider_id': provider_id, 'enabled': enabled and not revoke})
            return self.public_provider(self.provider(conn, provider_id))

    def set_enabled(self, provider_id, revision, enabled, principal, *, organization=None):
        return self._provider_change(provider_id, revision, principal, enabled=enabled, organization=organization)

    def revoke(self, provider_id, revision, principal, *, organization=None):
        return self._provider_change(provider_id, revision, principal, revoke=True, organization=organization)

    def load(self, conn, provider, uid):
        suffix = ' FOR UPDATE OF e,l' if self.db.is_postgres else ''
        row = roster._one(self.db, conn, _SELECT + ''' WHERE l.provider_id=? AND e.employee_id=?
            AND e.organization_id=? AND l.deleted_at IS NULL''' + suffix,
            (provider['provider_id'], uid, provider['organization_id']))
        if not row:
            raise Error(404, 'User not found')
        return row

    def user_projection(self, conn, provider, row):
        """Include only current, direct provider-owned groups in the User view."""
        resource = _project(row)
        # Lazy import keeps SCIM's provider/roster model independent of the
        # optional Group resource, which never grants access to OWG evidence.
        from .scim_groups import user_groups
        membership = user_groups(self.db, conn, provider, row['employee_id'])
        if membership:
            resource['groups'] = membership
            digest = hashlib.sha256(json.dumps(
                [resource['meta']['version'], membership],
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            resource['meta']['version'] = 'W/"' + digest + '"'
        return resource

    def conditional(self, conn, provider, row, etag):
        if not etag:
            raise Error(428, 'Read the current User and supply its exact If-Match version')
        if etag != self.user_projection(conn, provider, row)['meta']['version']:
            raise Error(412, 'User changed; read the current version before updating')

    def get_user(self, provider_id, token, uid):
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            return self.user_projection(conn, provider, self.load(conn, provider, uid))

    def list_users(self, provider_id, token, *, filter_text='', start_index=1, count=100):
        if type(start_index) is not int or start_index < 1 or type(count) is not int or not 0 <= count <= 200:
            raise Error(400, 'startIndex must be positive and count must be from zero to 200', 'invalidValue')
        where, extra = '', []
        if filter_text:
            if not isinstance(filter_text, str) or len(filter_text) > 1000:
                raise Error(400, 'Unsupported filter', 'invalidFilter')
            match = re.fullmatch(r'(userName|externalId)\s+eq\s+("(?:[^"\\]|\\.)*")', filter_text, re.I)
            if not match:
                raise Error(400, 'Only userName eq or externalId eq with a JSON string is supported', 'invalidFilter')
            try:
                value = json.loads(match[2])
            except ValueError:
                raise Error(400, 'Invalid filter string', 'invalidFilter') from None
            if match[1].lower() == 'username':
                where, extra = ' AND e.email=?', [value.strip().lower()]
            else:
                where, extra = ' AND l.external_id=?', [value]
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            scope = ' WHERE l.provider_id=? AND e.organization_id=? AND l.deleted_at IS NULL' + where
            params = (provider_id, provider['organization_id'], *extra)
            total = roster._one(self.db, conn, 'SELECT count(*) AS n FROM gateway_scim_users l JOIN gateway_employees e ON e.employee_id=l.employee_id' + scope, params)['n']
            rows = roster._all(self.db, conn, _SELECT + scope + ' ORDER BY e.email,e.employee_id LIMIT ? OFFSET ?', (*params, count, start_index - 1))
            return {'schemas': [LIST], 'totalResults': total, 'startIndex': start_index,
                    'itemsPerPage': len(rows), 'Resources': [self.user_projection(conn, provider, row) for row in rows]}

    def create_user(self, provider_id, token, body):
        values = _attributes(body)
        if 'username' not in values:
            raise Error(400, 'userName is required', 'invalidValue')
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            org = provider['organization_id']
            if roster._one(self.db, conn, 'SELECT employee_id FROM gateway_employees WHERE organization_id=? AND email=?', (org, values['username'])):
                raise Error(409, 'A roster identity already exists; provisioning cannot adopt or reactivate it', 'uniqueness')
            made, _ = roster.upsert_employee_in_transaction(self.db, conn, org, email=values['username'], display_name=values.get('displayname', ''))
            uid, now = made['employee_id'], roster._now()
            self.db._execute(conn, '''INSERT INTO gateway_scim_users
                (provider_id,employee_id,external_id,revision,created_at,updated_at)
                VALUES (?,?,?,1,?,?)''', (provider_id, uid, values.get('externalid'), now, now))
            if values.get('active') is False:
                roster.offboard_employee_in_transaction(self.db, conn, org, uid)
            self.audit(conn, org, provider_id, 'scim.user.created', {'employee_id': uid, 'active': values.get('active', True)})
            return self.user_projection(conn, provider, self.load(conn, provider, uid))

    def mutate(self, conn, provider, row, values):
        current = self.user_projection(conn, provider, row)
        if values.get('username', current['userName']) != current['userName']:
            raise Error(400, 'userName is the stable evidence actor; renaming is not supported', 'mutability')
        name = values.get('displayname', current['displayName'])
        active = values.get('active', current['active'])
        external = values.get('externalid', current.get('externalId'))
        if (name, active, external) == (current['displayName'], current['active'], current.get('externalId')):
            return current
        uid, now = row['employee_id'], roster._now()
        if current['active'] and not active:
            roster.offboard_employee_in_transaction(self.db, conn, provider['organization_id'], uid)
        elif not current['active'] and active:
            roster.revoke_inactive_credentials_in_transaction(self.db, conn, row)
        self.db._execute(conn, '''UPDATE gateway_employees SET display_name=?,status=?,
            offboarded_at=CASE WHEN ? THEN NULL ELSE offboarded_at END,updated_at=? WHERE employee_id=?''',
            (name, 'active' if active else 'offboarded', active, now, uid))
        self.db._execute(conn, '''UPDATE gateway_scim_users SET external_id=?,revision=revision+1,
            updated_at=? WHERE provider_id=? AND employee_id=?''', (external, now, provider['provider_id'], uid))
        self.audit(conn, provider['organization_id'], provider['provider_id'], 'scim.user.updated',
                   {'employee_id': uid, 'active': active})
        return self.user_projection(conn, provider, self.load(conn, provider, uid))

    def replace_user(self, provider_id, token, uid, etag, body):
        values = _attributes(body)
        if 'username' not in values:
            raise Error(400, 'userName is required', 'invalidValue')
        values.setdefault('displayname', '')
        values.setdefault('externalid', None)
        # Omitted active preserves status: replacement is never implicit rehire.
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            row = self.load(conn, provider, uid)
            self.conditional(conn, provider, row, etag)
            return self.mutate(conn, provider, row, values)

    def patch_user(self, provider_id, token, uid, etag, body):
        packet = _keys(body, {'schemas', 'operations'})
        operations = packet.get('operations')
        if packet.get('schemas') != [PATCH] or not isinstance(operations, list) or not 1 <= len(operations) <= 20:
            raise Error(400, 'A PatchOp with one to twenty operations is required', 'invalidSyntax')
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            row = self.load(conn, provider, uid)
            self.conditional(conn, provider, row, etag)
            current = self.user_projection(conn, provider, row)
            values = {'username': current['userName'], 'displayname': current['displayName'],
                      'active': current['active'], 'externalid': current.get('externalId')}
            for operation in operations:
                op = _keys(operation, {'op', 'path', 'value'})
                verb = op.get('op', '')
                path = op.get('path', '')
                if not isinstance(verb, str) or verb.lower() not in {'add', 'replace', 'remove'}:
                    raise Error(400, 'Unsupported patch operation', 'invalidSyntax')
                if not isinstance(path, str):
                    raise Error(400, 'Invalid patch path', 'invalidPath')
                path, verb = path.lower(), verb.lower()
                if verb == 'remove':
                    if not path:
                        raise Error(400, 'A remove path is required', 'noTarget')
                    if path not in {'displayname', 'externalid'}:
                        raise Error(400, 'Only displayName or externalId may be removed', 'mutability')
                    values[path] = '' if path == 'displayname' else None
                else:
                    if 'value' not in op:
                        raise Error(400, 'A patch value is required', 'invalidSyntax')
                    if path:
                        if path not in values:
                            raise Error(400, 'Unsupported patch path', 'invalidPath')
                        changed = {path: op['value']}
                    else:
                        changed = _keys(op['value'], set(values))
                    values.update(_validate_values(changed))
            return self.mutate(conn, provider, row, values)

    def delete_user(self, provider_id, token, uid, etag):
        with self.transaction() as conn:
            provider = self.provider(conn, provider_id, token=token)
            row = self.load(conn, provider, uid)
            self.conditional(conn, provider, row, etag)
            if row['status'] == 'active':
                roster.offboard_employee_in_transaction(self.db, conn, provider['organization_id'], uid)
            now = roster._now()
            self.db._execute(conn, '''UPDATE gateway_scim_users SET deleted_at=?,updated_at=?,revision=revision+1
                WHERE provider_id=? AND employee_id=?''', (now, now, provider_id, uid))
            self.audit(conn, provider['organization_id'], provider_id, 'scim.user.deleted', {'employee_id': uid, 'evidence_deleted': False})
