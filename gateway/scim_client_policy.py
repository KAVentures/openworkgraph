"""Explicit, narrow client compatibility; strict behavior remains the default."""
from . import employees as roster, scim_provisioning as scim

SCHEMA = '''CREATE TABLE IF NOT EXISTS gateway_scim_client_policies (
 provider_id TEXT PRIMARY KEY,
 unversioned_user_patch INTEGER NOT NULL DEFAULT 0 CHECK(unversioned_user_patch IN (0,1)),
 updated_at TEXT NOT NULL)'''


def enabled(db, conn, provider_id):
    row = roster._one(db, conn,
        'SELECT unversioned_user_patch FROM gateway_scim_client_policies WHERE provider_id=?', (provider_id,))
    return bool(row) and row['unversioned_user_patch'] == 1


class Policies:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def _provider(self, conn, organization, provider_id):
        provider = self.service.provider(conn, provider_id)
        if provider['organization_id'] != organization:
            raise scim.Error(404, 'Provisioning provider not found')
        return provider

    def _public(self, conn, provider):
        return {'provider_id': provider['provider_id'], 'revision': provider['revision'],
                'unversioned_user_patch': enabled(self.db, conn, provider['provider_id']),
                'provider_enabled': bool(provider['enabled']), 'revoked': bool(provider['revoked_at']),
                'scope': 'standalone_users_patch_only'}

    def get(self, organization, provider_id):
        with self.service.transaction() as conn:
            return self._public(conn, self._provider(conn, organization, provider_id))

    def set(self, organization, provider_id, body, principal):
        values = scim._keys(body, {'revision', 'enabled', 'confirmunversioned'})
        revision, allow = values.get('revision'), values.get('enabled')
        if (type(revision) is not int or revision < 1 or type(allow) is not bool or
                ('confirmunversioned' in values and type(values['confirmunversioned']) is not bool)):
            raise scim.Error(400, 'Current revision and explicit policy booleans are required', 'invalidValue')
        if allow and values.get('confirmunversioned') is not True:
            raise scim.Error(400, 'Confirm that unversioned PATCH can overwrite targeted current fields', 'invalidValue')
        with self.service.transaction() as conn:
            provider = self._provider(conn, organization, provider_id)
            if provider['revision'] != revision:
                raise scim.Error(412, 'Provider changed; review its current client policy')
            if provider['revoked_at']:
                raise scim.Error(409, 'A permanently revoked provider cannot change client policy')
            if enabled(self.db, conn, provider_id) == allow:
                return self._public(conn, provider)
            now = roster._now()
            self.db._execute(conn, '''INSERT INTO gateway_scim_client_policies
                (provider_id,unversioned_user_patch,updated_at) VALUES (?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                unversioned_user_patch=excluded.unversioned_user_patch,updated_at=excluded.updated_at''',
                (provider_id, int(allow), now))
            self.db._execute(conn, '''UPDATE gateway_scim_providers SET revision=revision+1,
                updated_at=? WHERE provider_id=?''', (now, provider_id))
            self.service.audit(conn, organization, principal, 'scim.provider.client_policy',
                {'provider_id': provider_id, 'unversioned_user_patch': allow})
            return self._public(conn, self.service.provider(conn, provider_id))
