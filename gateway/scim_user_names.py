"""Optional provider-owned name metadata; never a login or roster name authority."""
import json

from . import scim_provisioning as scim

FIELDS = {'formatted': 512, 'familyName': 256, 'givenName': 256,
          'middleName': 256, 'honorificPrefix': 256, 'honorificSuffix': 256}
_NAMES = {name.lower(): name for name in FIELDS}
SCHEMA = '''CREATE TABLE IF NOT EXISTS gateway_scim_user_names (
 provider_id TEXT NOT NULL, employee_id TEXT NOT NULL, name_json TEXT NOT NULL,
 PRIMARY KEY(provider_id,employee_id),
 FOREIGN KEY(provider_id,employee_id) REFERENCES gateway_scim_users(provider_id,employee_id)
 ON DELETE CASCADE)'''


def _delta(value):
    if value is None:
        return None
    fields = scim._keys(value, set(_NAMES))
    return {_NAMES[key]: None if item is None else
            scim._text(item, FIELDS[_NAMES[key]], blank=True) for key, item in fields.items()}


def normalize(value):
    return {key: item for key, item in (_delta(value) or {}).items() if item is not None}


def stored(encoded):
    if encoded is None:
        return {}
    try:
        return normalize(json.loads(encoded))
    except (scim.Error, ValueError, TypeError, RecursionError):
        raise scim.Error(503, 'Stored name metadata is unavailable') from None


def save(db, conn, provider_id, employee_id, name):
    if name:
        db._execute(conn, '''INSERT INTO gateway_scim_user_names
            (provider_id,employee_id,name_json) VALUES (?,?,?)
            ON CONFLICT(provider_id,employee_id) DO UPDATE SET name_json=excluded.name_json''',
            (provider_id, employee_id, json.dumps(name, sort_keys=True, ensure_ascii=True)))
    else:
        db._execute(conn, 'DELETE FROM gateway_scim_user_names WHERE provider_id=? AND employee_id=?',
                    (provider_id, employee_id))


def patch(current, path, verb, value=None):
    result = dict(current)
    if path == 'name':
        if verb == 'remove' or value is None:
            return {}
        delta = _delta(value)
    else:
        child = path.removeprefix('name.')
        if child not in _NAMES:
            raise scim.Error(400, 'Unsupported name subattribute', 'invalidPath')
        delta = _delta({_NAMES[child]: None if verb == 'remove' else value})
    for key, item in delta.items():
        if item is None:
            result.pop(key, None)
        else:
            result[key] = item
    return result
