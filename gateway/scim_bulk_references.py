"""Request-local, collection-typed Bulk references in declared schema positions.

Never traverse arbitrary values or fetch URLs. Resolving a reference grants no
authority: the existing lifecycle service rechecks its provider and members.
"""
import json
import re

from . import scim_provisioning as scim
from .scim_groups import MAX_MEMBERS

_ID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
_SELECTOR = re.compile(r'members\[value eq ("(?:[^"\\]|\\.)*")\]', re.I)


def resolve(value, collection, created):
    if not isinstance(value, str) or not value.startswith('bulkId:'):
        return value
    key = value[len('bulkId:'):]
    target = created.get(key) if _ID.fullmatch(key) else None
    if target is None or target[0] != collection:
        raise scim.Error(400, 'A reference requires a successful earlier create of the expected resource type', 'invalidValue')
    return target[1]


def _member(value, created):
    if not isinstance(value, dict):
        return value  # The Group validator rejects unsupported member shapes.
    member = scim._keys(value, {'value', 'type'})
    if 'value' in member:
        member['value'] = resolve(member['value'], 'Users', created)
    return member


def _members(value, created):
    if isinstance(value, list):
        if len(value) > MAX_MEMBERS:
            raise scim.Error(400, 'At most 200 direct Users are supported', 'invalidValue')
        return [_member(item, created) for item in value]
    return _member(value, created)  # PATCH add also accepts one member object.


def group_data(data, method, created):
    if method != 'PATCH':
        result = scim._keys(data, {'schemas', 'id', 'meta', 'displayname', 'externalid', 'members'})
        if 'members' in result:
            result['members'] = _members(result['members'], created)
        return result
    result = scim._keys(data, {'schemas', 'operations'})
    operations = result.get('operations')
    if result.get('schemas') != [scim.PATCH] or not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise scim.Error(400, 'A PatchOp with 1–20 operations is required', 'invalidSyntax')
    rewritten = []
    for item in operations:
        op = scim._keys(item, {'op', 'path', 'value'})
        verb, path = op.get('op'), op.get('path')
        if isinstance(verb, str) and isinstance(path, str):
            if path.lower() == 'members' and verb.lower() in {'add', 'replace'} and 'value' in op:
                op['value'] = _members(op['value'], created)
            elif verb.lower() == 'remove' and (match := _SELECTOR.fullmatch(path)):
                try:
                    identifier = json.loads(match.group(1))
                except ValueError:
                    raise scim.Error(400, 'Invalid member selector', 'invalidPath') from None
                resolved = resolve(identifier, 'Users', created)
                if resolved != identifier:
                    op['path'] = 'members[value eq ' + json.dumps(resolved) + ']'
        rewritten.append(op)
    result['operations'] = rewritten
    return result
