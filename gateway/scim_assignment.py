"""Explicit administrator-controlled SCIM ownership of an existing employee.

The SCIM POST matching guard stays unchanged. This path inserts only a linkage;
no roster, identity, access epoch, consent, device or team row is updated.
"""
import hashlib
import hmac
import json
import re

from . import employees as roster, scim_provisioning as scim


class Assignments:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def _state(self, conn, organization, provider_id, employee_id):
        scim._text(provider_id, 200)
        scim._text(employee_id, 200)
        provider = self.service.provider(conn, provider_id)
        if provider['organization_id'] != organization:
            raise scim.Error(404, 'Provisioning provider not found')
        if provider['revoked_at']:
            raise scim.Error(409, 'A permanently revoked provider cannot receive an assignment')
        try:
            employee = roster._lock_employee(self.db, conn, organization, employee_id)
        except roster.EmployeeError:
            raise scim.Error(404, 'Employee not found in this organization') from None
        linked = roster._one(self.db, conn,
            'SELECT provider_id FROM gateway_scim_users WHERE employee_id=?', (employee_id,))
        if linked:
            raise scim.Error(409, 'Employee already has a provisioning linkage; transfers and tombstone adoption are unsupported', 'uniqueness')
        epoch = roster._one(self.db, conn,
            'SELECT generation FROM gateway_employee_access_epochs WHERE employee_id=?', (employee_id,))
        basis = [provider_id, provider['revision'], employee, int(epoch['generation']) if epoch else 0]
        # An opaque keyed fingerprint does not disclose the SSO subject or epoch.
        version = hmac.new(provider['token_hash'].encode(),
            json.dumps(basis, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
        return provider, employee, version

    @staticmethod
    def _preview(provider, employee, version):
        return {'provider_id': provider['provider_id'], 'provider_revision': provider['revision'],
                'provider_label': provider['label'], 'provider_enabled': bool(provider['enabled']),
                'employee': roster._employee_public(employee), 'employee_version': version,
                'assigns_provider_lifecycle_authority': True}

    def preview(self, organization, provider_id, employee_id):
        with self.service.transaction() as conn:
            return self._preview(*self._state(conn, organization, provider_id, employee_id))

    def preview_email(self, organization, provider_id, email):
        scim._text(provider_id, 200)
        try:
            address = roster.normalize_email(scim._text(email, 512))
        except roster.EmployeeError:
            raise scim.Error(400, 'An exact existing employee email is required', 'invalidValue') from None
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id)
            if provider['organization_id'] != organization:
                raise scim.Error(404, 'Provisioning provider not found')
            employee = roster._one(self.db, conn,
                'SELECT employee_id FROM gateway_employees WHERE organization_id=? AND email=?', (organization, address))
            if not employee:
                raise scim.Error(404, 'Employee not found in this organization')
            state = self._state(conn, organization, provider_id, employee['employee_id'])
            if state[1]['email'] != address:
                raise scim.Error(412, 'Employee changed; review the current identity')
            return self._preview(*state)

    def assign(self, organization, provider_id, employee_id, body, principal):
        values = scim._keys(body, {'revision', 'employeeversion', 'externalid', 'confirmassignment'})
        if values.get('confirmassignment') is not True:
            raise scim.Error(400, 'Explicit confirmation of provider lifecycle authority is required', 'invalidValue')
        revision, version = values.get('revision'), values.get('employeeversion')
        if type(revision) is not int or revision < 1 or not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{64}', version):
            raise scim.Error(400, 'Read the current provider and employee assignment preview first', 'invalidValue')
        external = scim._validate_values({'externalid': values.get('externalid')})['externalid']
        with self.service.transaction() as conn:
            provider, employee, current = self._state(conn, organization, provider_id, employee_id)
            if provider['revision'] != revision or not hmac.compare_digest(version, current):
                raise scim.Error(412, 'Provider or employee changed; review a new assignment preview')
            now = roster._now()
            self.db._execute(conn, '''INSERT INTO gateway_scim_users
                (provider_id,employee_id,external_id,revision,created_at,updated_at)
                VALUES (?,?,?,1,?,?)''', (provider_id, employee_id, external, now, now))
            self.service.audit(conn, organization, principal, 'scim.user.assigned',
                {'provider_id': provider_id, 'employee_id': employee_id,
                 'active': employee['status'] == 'active', 'lifecycle_authority_confirmed': True})
            return self.service.user_projection(conn, provider, self.service.load(conn, provider, employee_id))
