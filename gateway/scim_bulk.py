"""Bounded Users/Groups SCIM bulk dispatch over existing lifecycle services.

Operations commit individually. There is no batch transaction, persistent job,
identity adoption, implicit ETag refresh, or authority derived from a bulk ID.
"""
import logging
import re
from urllib.parse import quote

from . import scim_provisioning as scim
from .scim_groups import Groups
from . import scim_bulk_references as references

REQUEST = 'urn:ietf:params:scim:api:messages:2.0:BulkRequest'
RESPONSE = 'urn:ietf:params:scim:api:messages:2.0:BulkResponse'
MAX_OPERATIONS = 50
MAX_PAYLOAD = 65536
_LOG = logging.getLogger(__name__)
_ID = r'[A-Za-z0-9_-]{1,100}'
_PATH = re.compile(r'/(Users|Groups)/(' + _ID + r'|bulkId:' + _ID + r')')


def validate(body):
    """Refuse ambiguous envelopes/paths before dispatching any operation."""
    values = scim._keys(body, {'schemas', 'operations', 'failonerrors'})
    if values.get('schemas') != [REQUEST]:
        raise scim.Error(400, 'Use the BulkRequest schema', 'invalidSyntax')
    operations = values.get('operations')
    if not isinstance(operations, list) or not operations:
        raise scim.Error(400, 'Bulk requires an operation list with at least one entry', 'invalidSyntax')
    if len(operations) > MAX_OPERATIONS:
        raise scim.Error(413, 'Bulk supports at most fifty operations', 'tooMany')
    threshold = values.get('failonerrors')
    if 'failonerrors' in values and (type(threshold) is not int or not 1 <= threshold <= MAX_OPERATIONS):
        raise scim.Error(400, 'failOnErrors must be an integer from one to fifty', 'invalidValue')
    result, declared = [], set()
    for operation in operations:
        row = scim._keys(operation, {'method', 'bulkid', 'version', 'path', 'data'})
        method, path = row.get('method'), row.get('path')
        if not isinstance(method, str) or method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise scim.Error(400, 'Bulk supports POST, PUT, PATCH and DELETE only', 'invalidValue')
        valid_path = isinstance(path, str) and (path in {'/Users', '/Groups'} if method == 'POST' else _PATH.fullmatch(path) is not None)
        if not valid_path:
            raise scim.Error(400, 'Use exact relative Users or Groups paths without URL encoding, query or traversal', 'invalidPath')
        if method == 'POST':
            bulk_id = row.get('bulkid')
            if not isinstance(bulk_id, str) or not re.fullmatch(_ID, bulk_id) or bulk_id in declared:
                raise scim.Error(400, 'POST needs a unique bounded alphanumeric, dash or underscore bulkId', 'invalidValue')
            declared.add(bulk_id)
            if 'version' in row:
                raise scim.Error(400, 'POST does not accept a resource version', 'invalidValue')
        elif 'bulkid' in row:
            raise scim.Error(400, 'bulkId identifies a POST operation only', 'invalidValue')
        if method == 'DELETE':
            if 'data' in row:
                raise scim.Error(400, 'DELETE does not accept operation data', 'invalidSyntax')
        elif not isinstance(row.get('data'), dict):
            raise scim.Error(400, 'POST, PUT and PATCH require an object in data', 'invalidSyntax')
        if 'version' in row and (not isinstance(row['version'], str) or len(row['version']) > 200
                                or any(ord(c) < 32 or ord(c) == 127 for c in row['version'])):
            raise scim.Error(400, 'Use the exact current resource version', 'invalidValue')
        result.append(row)
    return result, threshold


def execute(service, provider_id, token, body, base_url):
    # Invalid credentials must not expose even envelope validation behavior.
    with service.transaction() as conn:
        service.provider(conn, provider_id, token=token)
    operations, threshold = validate(body)
    groups = Groups(service)
    dispatch = {'Users': (service.create_user, service.replace_user, service.patch_user, service.delete_user),
                'Groups': (groups.create, groups.replace, groups.patch, groups.delete)}
    responses, created, failures = [], {}, 0
    for operation in operations:
        method, path = operation['method'], operation['path']
        collection = path.split('/')[1]
        create, replace, patch, delete = dispatch[collection]
        item = {'method': method}
        if method == 'POST':
            item['bulkId'] = operation['bulkid']
        else:
            identifier = path.split('/')[2]
            # Unresolved references are never rewritten in unrelated attributes
            # and never resolved by userName, externalId or another request.
            item['location'] = base_url + '/' + collection + '/' + quote(identifier, safe='')
        try:
            # Individual methods validate some resource attributes before opening
            # their transaction. Authenticate before that validation as well;
            # the method's own locked check still guards the actual mutation.
            with service.transaction() as conn:
                service.provider(conn, provider_id, token=token)
            data = operation.get('data')
            if method != 'POST':
                identifier = references.resolve(identifier, collection, created)
                item['location'] = base_url + '/' + collection + '/' + quote(identifier, safe='')
            if collection == 'Groups' and method != 'DELETE':
                data = references.group_data(data, method, created)
            # Each existing method rechecks and locks current provider authority
            # inside its own roster/link/audit transaction. No lock spans calls.
            if method == 'POST':
                resource = create(provider_id, token, data)
                location = base_url + '/' + collection + '/' + quote(resource['id'], safe='')
                version = resource['meta']['version']
                item.update(location=location, version=version, status='201')
                created[operation['bulkid']] = (collection, resource['id'])
            else:
                version = operation.get('version')
                if method == 'DELETE':
                    delete(provider_id, token, identifier, version)
                    item['status'] = '204'
                else:
                    update = replace if method == 'PUT' else patch
                    resource = update(provider_id, token, identifier, version, data)
                    item.update(status='200', version=resource['meta']['version'])
        except scim.Error as error:
            item.update(status=str(error.status), response=error.packet())
        except Exception:
            _LOG.error('SCIM bulk %s operation failed (%s)', collection, method)
            # Existing operation transaction rolls back. No DB/roster input or
            # exception text belongs in the provisioning response.
            error = scim.Error(500, 'The provisioning operation failed; inspect server health before retrying')
            item.update(status='500', response=error.packet())
        responses.append(item)
        if int(item['status']) >= 400:
            failures += 1
            if item['status'] == '401' or threshold is not None and failures >= threshold:
                break
    return {'schemas': [RESPONSE], 'Operations': responses}
