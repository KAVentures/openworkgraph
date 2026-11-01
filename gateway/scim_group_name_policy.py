"""Optional provider-local Group naming constraint; never an access grant."""
from . import employees as roster, scim_provisioning as scim

SCHEMA = '''CREATE TABLE IF NOT EXISTS gateway_scim_group_name_policies (
 provider_id TEXT PRIMARY KEY REFERENCES gateway_scim_providers(provider_id) ON DELETE CASCADE,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 updated_at TEXT NOT NULL)'''
COMPARISON = 'stored_whitespace_normalized_casefold'


def enabled(db, conn, provider_id):
    row = roster._one(db, conn,
        'SELECT enabled FROM gateway_scim_group_name_policies WHERE provider_id=?', (provider_id,))
    if not row:
        return False
    if type(row['enabled']) is not int or row['enabled'] not in (0, 1):
        raise scim.Error(503, 'Stored Group naming policy is unavailable')
    return row['enabled'] == 1


def _names(db, conn, provider_id):
    from .scim_groups import MAX_GROUPS

    rows = roster._all(db, conn, '''SELECT group_id,display_name FROM gateway_scim_groups
        WHERE provider_id=? AND deleted_at IS NULL LIMIT ?''', (provider_id, MAX_GROUPS + 1))
    if len(rows) > MAX_GROUPS:
        raise scim.Error(503, 'Stored Group naming metadata exceeds its supported bound')
    try:
        return [(row['group_id'], ' '.join(scim._text(row['display_name'], 120).split()).casefold())
                for row in rows]
    except scim.Error:
        raise scim.Error(503, 'Stored Group naming metadata is unavailable') from None


def require_available_name(db, conn, provider_id, name, *, exclude_id=None):
    """Caller MUST hold the existing provider lock in the mutation transaction.

    SQL lower/collations differ across SQLite/PostgreSQL. The common comparison
    is bounded to the existing 100-Group provider history; client values never
    become SQL. No name or membership is changed by a policy check.
    """
    if enabled(db, conn, provider_id):
        key = ' '.join(name.split()).casefold()
        if any((exclude_id is None or gid != exclude_id) and other == key
               for gid, other in _names(db, conn, provider_id)):
            raise scim.Error(409, 'An active Group already uses this name in the provider', 'uniqueness')


class Policies:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def _provider(self, conn, organization, provider_id):
        provider = self.service.provider(conn, provider_id)
        if provider['organization_id'] != organization:
            raise scim.Error(404, 'Provisioning provider not found')
        return provider

    def _public(self, conn, provider):
        names = [name for _, name in _names(self.db, conn, provider['provider_id'])]
        return {'provider_id': provider['provider_id'], 'revision': provider['revision'],
                'enabled': enabled(self.db, conn, provider['provider_id']),
                'provider_enabled': bool(provider['enabled']), 'revoked': bool(provider['revoked_at']),
                'active_groups': len(names), 'colliding_groups': len(names) - len(set(names)),
                'comparison': COMPARISON, 'scope': 'provider_active_group_names'}

    def get(self, organization, provider_id):
        with self.service.transaction() as conn:
            return self._public(conn, self._provider(conn, organization, provider_id))

    def set(self, organization, provider_id, body, principal):
        values = scim._keys(body, {'revision', 'enabled'})
        revision, allow = values.get('revision'), values.get('enabled')
        if type(revision) is not int or revision < 1 or type(allow) is not bool:
            raise scim.Error(400, 'Current revision and an explicit policy boolean are required', 'invalidValue')
        with self.service.transaction() as conn:
            provider = self._provider(conn, organization, provider_id)
            if provider['revision'] != revision:
                raise scim.Error(412, 'Provider changed; review its current Group naming policy')
            if provider['revoked_at']:
                raise scim.Error(409, 'A permanently revoked provider cannot change Group naming policy')
            current = self._public(conn, provider)
            if allow and current['colliding_groups']:
                raise scim.Error(409, 'Resolve active Group name collisions before enabling this policy', 'uniqueness')
            if current['enabled'] == allow:
                return current
            now = roster._now()
            self.db._execute(conn, '''INSERT INTO gateway_scim_group_name_policies
                (provider_id,enabled,updated_at) VALUES (?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at''',
                (provider_id, int(allow), now))
            self.db._execute(conn, '''UPDATE gateway_scim_providers SET revision=revision+1,
                updated_at=? WHERE provider_id=?''', (now, provider_id))
            self.service.audit(conn, organization, principal, 'scim.provider.group_name_policy',
                               {'provider_id': provider_id, 'unique_group_names': allow})
            return self._public(conn, self.service.provider(conn, provider_id))
